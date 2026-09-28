"""PHA B (+ PHA E) -- Plugin pytest ghi ĐỐI SỐ THẬT, và hoán đổi Rust vào repo.

Nạp bằng `-p stage1_profiling.capture_plugin` (đường dẫn `benchmark/` phải có
trong `PYTHONPATH` của subprocess). Plugin này chạy BÊN TRONG venv riêng của
repo, nên chỉ được dùng stdlib + `cloudpickle` (+ `numpy` nếu repo có) và
`stage1_profiling.deep_compare` -- tuyệt đối không import yaml/networkx/
tree_sitter.

HAI CHẾ ĐỘ, chọn bằng biến môi trường:
  RTB_CAPTURE_TARGETS  -> CHẾ ĐỘ GHI (Pha B): bọc hotspot, ghi lại mọi lời
                          gọi thật mà bộ test của repo tạo ra.
  RTB_SWAP_TARGETS     -> CHẾ ĐỘ HOÁN ĐỔI (Pha E): thay hotspot bằng bản Rust
                          đã build, rồi chạy lại đúng bộ test đó.

VÌ SAO PHẢI THAY THAM CHIẾU TRONG TOÀN BỘ `sys.modules`: chỉ gán lại
`module.f = wrapper` là KHÔNG đủ. Nếu ở đâu đó có `from mypkg.utils import
normalize` thì module kia đã giữ một tham chiếu RIÊNG tới hàm gốc, và nó sẽ
gọi bản gốc chứ không phải wrapper. Nên `_patch_everywhere` quét mọi module
đã nạp và thay MỌI tham chiếu trùng ĐỊNH DANH (`is`) với hàm gốc.

RÀNG BUỘC QUAN TRỌNG (Pha E): khi bản Rust ném exception, plugin KHÔNG được
âm thầm quay về bản Python. Test đó phải fail. Che lỗi ở đây sẽ biến bản dịch
sai thành "pass", làm toàn bộ số liệu correctness thành vô nghĩa.
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import threading
import time
import traceback
from pathlib import Path

try:
    import cloudpickle
except Exception as _exc:  # noqa: BLE001
    cloudpickle = None
    _CLOUDPICKLE_ERR = str(_exc)

# --- Lý do (phải trùng chuỗi trong outcomes.py -- plugin không import được
#     outcomes.py vì file đó nằm ở gốc benchmark/ và có thể không trên path) ---
R_MEASURED = "MEASURED"
R_UNSUPPORTED_KIND = "UNSUPPORTED_KIND"
R_UNREPLAYABLE_ARGS = "UNREPLAYABLE_ARGS"
R_NOT_COVERED = "NOT_COVERED_BY_TESTS"
R_UNRESOLVABLE_IMPORT = "UNRESOLVABLE_IMPORT"

DEFAULT_MAX_CALLS = 20
DEFAULT_MAX_ARG_BYTES = 5 * 1024 * 1024  # 5MB mỗi đối số


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


class _Recorder:
    """Giữ toàn bộ dữ liệu ghi được cho MỘT hotspot."""

    def __init__(self, spec: dict, max_calls: int, max_arg_bytes: int):
        self.spec = spec
        self.name = spec["function_name"]
        self.max_calls = max_calls
        self.max_arg_bytes = max_arg_bytes
        self.calls: list[dict] = []
        self.reason: str | None = None       # None = chưa có vấn đề gì
        self.detail: str = ""
        self.observed_types: list[str] = []  # mô tả kiểu của lần gọi đầu
        self.observed_kwargs_types: dict[str, str] = {}
        self.n_seen = 0                      # tổng số lần gọi thật (kể cả sau khi đủ N)
        # Chống đệ quy: hàm tự gọi chính nó sẽ sinh ra vô số bản ghi lồng nhau
        # và làm nổ N. Chỉ ghi lời gọi ở tầng ngoài cùng.
        self._local = threading.local()

    # -- serialize ---------------------------------------------------------
    def _dumps(self, value: object) -> tuple[bytes | None, str]:
        """cloudpickle 1 giá trị. Trả về (bytes, lý do lỗi)."""
        if cloudpickle is None:
            return None, f"không import được cloudpickle: {_CLOUDPICKLE_ERR}"
        try:
            blob = cloudpickle.dumps(value)
        except Exception as exc:  # noqa: BLE001 -- pickle lỗi là tín hiệu hợp lệ
            return None, f"{type(value).__name__}: {type(exc).__name__}: {exc}"
        if len(blob) > self.max_arg_bytes:
            return None, (
                f"{type(value).__name__}: {len(blob)} byte > giới hạn "
                f"{self.max_arg_bytes} byte"
            )
        return blob, ""

    def _dump_seq(self, values: tuple) -> tuple[list[bytes] | None, str]:
        out: list[bytes] = []
        for v in values:
            blob, err = self._dumps(v)
            if err:
                return None, err
            out.append(blob)
        return out, ""

    def _dump_map(self, values: dict) -> tuple[dict[str, bytes] | None, str]:
        out: dict[str, bytes] = {}
        for k, v in values.items():
            blob, err = self._dumps(v)
            if err:
                return None, err
            out[k] = blob
        return out, ""

    # -- ghi 1 lời gọi -----------------------------------------------------
    def record(self, args: tuple, kwargs: dict, fn) -> object:
        """Gọi hàm thật, ghi lại trạng thái TRƯỚC và SAU lời gọi."""
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        try:
            if depth > 0:  # lời gọi đệ quy -- chạy thẳng, không ghi
                return fn(*args, **kwargs)

            self.n_seen += 1
            capture = (
                self.reason is None
                and len(self.calls) < self.max_calls
            )
            if not capture:
                return fn(*args, **kwargs)

            # Chụp TRƯỚC khi gọi: hàm có thể sửa đối số tại chỗ.
            args_pre, err = self._dump_seq(args)
            if err:
                self._fail(R_UNREPLAYABLE_ARGS, f"đối số vị trí không pickle được -- {err}")
                return fn(*args, **kwargs)
            kwargs_pre, err = self._dump_map(kwargs)
            if err:
                self._fail(R_UNREPLAYABLE_ARGS, f"đối số từ khoá không pickle được -- {err}")
                return fn(*args, **kwargs)

            if not self.observed_types:
                self._describe(args, kwargs)

            started = time.perf_counter()
            exc_text = None
            result_blob = None
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 -- ghi lại rồi ném tiếp
                duration = time.perf_counter() - started
                exc_text = f"{type(exc).__name__}: {exc}"
                self._append(args_pre, kwargs_pre, args, kwargs, None, exc_text, duration)
                raise
            duration = time.perf_counter() - started

            result_blob, err = self._dumps(result)
            if err:
                # Đối số pickle được nhưng GIÁ TRỊ TRẢ VỀ thì không -> vẫn
                # không so sánh được kết quả, nên cũng là UNREPLAYABLE_ARGS.
                self._fail(R_UNREPLAYABLE_ARGS, f"giá trị trả về không pickle được -- {err}")
                return result

            self._append(args_pre, kwargs_pre, args, kwargs, result_blob, None, duration)
            return result
        finally:
            self._local.depth = getattr(self._local, "depth", 1) - 1

    def _append(self, args_pre, kwargs_pre, args, kwargs, result_blob, exc_text, duration):
        """Chụp trạng thái SAU lời gọi để phát hiện hàm sửa đối số tại chỗ."""
        args_post, err_a = self._dump_seq(args)
        kwargs_post, err_k = self._dump_map(kwargs)
        self.calls.append({
            "args_pre": args_pre,
            "kwargs_pre": kwargs_pre,
            # Chụp sau có thể lỗi dù chụp trước OK (hàm nhét đối tượng lạ vào
            # list). Không coi là lỗi chặn: chỉ mất khả năng kiểm tra mutation.
            "args_post": None if err_a else args_post,
            "kwargs_post": None if err_k else kwargs_post,
            "post_error": err_a or err_k,
            "result": result_blob,
            "exception": exc_text,
            "duration_sec": duration,
        })

    def _describe(self, args: tuple, kwargs: dict) -> None:
        try:
            from stage1_profiling.deep_compare import describe_type
        except Exception:  # noqa: BLE001
            return
        try:
            self.observed_types = [describe_type(a) for a in args]
            self.observed_kwargs_types = {k: describe_type(v) for k, v in kwargs.items()}
        except Exception:  # noqa: BLE001 -- mô tả kiểu chỉ để làm prompt đẹp
            pass

    def _fail(self, reason: str, detail: str) -> None:
        if self.reason is None:
            self.reason = reason
            self.detail = detail

    # -- kết luận ----------------------------------------------------------
    def finalize(self) -> dict:
        if self.reason is None and not self.calls:
            self.reason = R_NOT_COVERED
            self.detail = "bộ test của repo không gọi hàm này lần nào"
        return {
            "function_name": self.name,
            "target": f"{self.spec['module']}:{self.spec['qualname']}",
            "module": self.spec["module"],
            "qualname": self.spec["qualname"],
            # PHẢI ghi lại: bước phát lại (`_replay_runner.py`) chạy trong một
            # tiến trình KHÁC, không có sẵn sys.path mà conftest của repo đã
            # dựng. Thiếu khoá này thì repo layout `src/` báo
            # ModuleNotFoundError dù lúc ghi vẫn import được bình thường.
            "import_root": self.spec.get("import_root"),
            "reason": self.reason,  # None = ghi được, chờ các bước sau
            "detail": self.detail,
            "n_calls_captured": len(self.calls),
            "n_calls_seen": self.n_seen,
            "observed_arg_types": self.observed_types,
            "observed_kwarg_types": self.observed_kwargs_types,
        }


# ---------------------------------------------------------------------------
# Tra và thay hàm
# ---------------------------------------------------------------------------
def _resolve_attr(module, qualname: str):
    """Đi theo `qualname` (vd "Foo.bar") để lấy (chủ sở hữu, tên thuộc tính,
    giá trị thô). "Chủ sở hữu" là module hoặc class chứa thuộc tính cuối."""
    owner = module
    parts = qualname.split(".")
    for part in parts[:-1]:
        # `<locals>` xuất hiện với hàm lồng trong hàm -- không lấy được từ ngoài.
        if part == "<locals>":
            raise AttributeError("hàm lồng trong hàm khác, không truy cập được từ ngoài")
        try:
            owner = getattr(owner, part)
        except AttributeError as exc:
            # Gặp thật trên piskvorky_sqlitedict: Stage 0 dựng qualname
            # "...DummyQueue.put" cho một class ĐỊNH NGHĨA BÊN TRONG một hàm.
            # Class đó chỉ tồn tại khi hàm bao ngoài chạy, nên không có đường
            # nào lấy nó từ module. Nói rõ ra thay vì để nguyên AttributeError
            # trần -- người đọc báo cáo không tự suy ra được.
            if callable(owner):
                raise AttributeError(
                    f"'{part}' được định nghĩa BÊN TRONG hàm "
                    f"'{getattr(owner, '__name__', owner)}' (class/hàm lồng cục bộ), "
                    f"nên không truy cập được từ cấp module -- qualname={qualname!r}"
                ) from exc
            raise AttributeError(
                f"không tìm thấy '{part}' trong {owner!r} -- qualname={qualname!r}"
            ) from exc
    last = parts[-1]
    raw = inspect.getattr_static(owner, last)
    return owner, last, raw


def _kind_of(raw) -> str:
    if isinstance(raw, staticmethod):
        return "staticmethod"
    if isinstance(raw, classmethod):
        return "classmethod"
    return "function"


def _unwrap(raw):
    """Lấy hàm thật bên trong staticmethod/classmethod."""
    return raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw


def _rewrap(kind: str, fn):
    """Bọc lại đúng loại descriptor để `Foo.bar` vẫn hoạt động như trước."""
    if kind == "staticmethod":
        return staticmethod(fn)
    if kind == "classmethod":
        return classmethod(fn)
    return fn


def _patch_everywhere(original, replacement, owner, attr: str, kind: str) -> int:
    """Thay MỌI tham chiếu tới `original` trong các module đã nạp.

    Bắt được cả `from x import f`: module nào đã giữ tham chiếu riêng tới hàm
    gốc cũng bị thay. So sánh bằng `is` (định danh) chứ không bằng `==`, để
    không vô tình thay một hàm khác chỉ vì nó định nghĩa `__eq__`.
    """
    import sys

    n = 0
    setattr(owner, attr, _rewrap(kind, replacement))
    n += 1

    for module in list(sys.modules.values()):
        if module is None:
            continue
        try:
            names = vars(module)
        except Exception:  # noqa: BLE001
            continue
        for key, value in list(names.items()):
            if value is original:
                try:
                    setattr(module, key, replacement)
                    n += 1
                except Exception:  # noqa: BLE001 -- module chỉ đọc
                    pass
            # Class được import sang module khác vẫn là CÙNG một object, nhưng
            # một class con có thể giữ bản copy của method trong __dict__ riêng
            # của nó -- phải thay cả ở đó, nếu không gọi qua class con sẽ trúng
            # hàm gốc.
            elif isinstance(value, type):
                try:
                    raw = value.__dict__.get(attr)
                except Exception:  # noqa: BLE001
                    continue
                if raw is not None and _unwrap(raw) is original:
                    try:
                        setattr(value, attr, _rewrap(_kind_of(raw), replacement))
                        n += 1
                    except Exception:  # noqa: BLE001 -- class dùng __slots__/chỉ đọc
                        pass
    return n


class _Plugin:
    """Thân plugin. Tách khỏi các hook module-level cho dễ test."""

    def __init__(self):
        self.mode = None
        self.recorders: dict[str, _Recorder] = {}
        self.skipped: dict[str, dict] = {}   # tên hàm -> {reason, detail}
        self.out_dir = Path(os.environ.get("RTB_CAPTURE_OUT", ".")).resolve()
        self.max_calls = _env_int("RTB_CAPTURE_MAX_CALLS", DEFAULT_MAX_CALLS)
        self.max_arg_bytes = _env_int("RTB_CAPTURE_MAX_ARG_BYTES", DEFAULT_MAX_ARG_BYTES)
        self.swap_report: dict[str, dict] = {}
        # PHAN 1.3: {ten hotspot: {"n": so lan ham Rust duoc goi}}.
        # Doc lai o pytest_sessionfinish -> rust_call_count.
        self._swap_counters: dict[str, dict] = {}

    # -- cài đặt -----------------------------------------------------------
    def install(self) -> None:
        raw_capture = os.environ.get("RTB_CAPTURE_TARGETS")
        raw_swap = os.environ.get("RTB_SWAP_TARGETS")
        if raw_swap:
            self.mode = "swap"
            self._install_swap(json.loads(raw_swap))
        elif raw_capture:
            self.mode = "capture"
            self._install_capture(json.loads(raw_capture))

    def _import_target(self, spec: dict):
        """Import module của hotspot, thêm `import_root` vào sys.path nếu cần."""
        import importlib
        import sys

        root = spec.get("import_root")
        if root and root not in sys.path:
            sys.path.insert(0, root)
        module = importlib.import_module(spec["module"])
        return _resolve_attr(module, spec["qualname"])

    def _install_capture(self, specs: list[dict]) -> None:
        for spec in specs:
            name = spec["function_name"]
            try:
                owner, attr, raw = self._import_target(spec)
            except Exception as exc:  # noqa: BLE001
                self.skipped[name] = {
                    "reason": R_UNRESOLVABLE_IMPORT,
                    "detail": f"{type(exc).__name__}: {exc}",
                    "import_root": spec.get("import_root"),
                }
                continue

            kind = _kind_of(raw)
            fn = _unwrap(raw)

            if not callable(fn):
                self.skipped[name] = {
                    "reason": R_UNSUPPORTED_KIND,
                    "detail": f"không phải hàm gọi được: {type(fn).__name__}",
                    "import_root": spec.get("import_root"),
                }
                continue
            if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn) \
                    or inspect.iscoroutinefunction(fn):
                what = ("generator" if inspect.isgeneratorfunction(fn)
                        else "async generator" if inspect.isasyncgenfunction(fn)
                        else "coroutine (async def)")
                self.skipped[name] = {
                    "reason": R_UNSUPPORTED_KIND,
                    "detail": (
                        f"hàm kiểu {what}: kết quả sinh dần theo thời gian nên "
                        "không ghi/phát lại được như một lời gọi đơn"
                    ),
                    "import_root": spec.get("import_root"),
                }
                continue

            recorder = _Recorder(spec, self.max_calls, self.max_arg_bytes)
            self.recorders[name] = recorder

            @functools.wraps(fn)
            def wrapper(*args, _rec=recorder, _fn=fn, **kwargs):
                return _rec.record(args, kwargs, _fn)

            n = _patch_everywhere(fn, wrapper, owner, attr, kind)
            print(f"[rtb-capture] bọc {spec['module']}:{spec['qualname']} "
                  f"({kind}), thay {n} tham chiếu")

    def _install_swap(self, specs: list[dict]) -> None:
        """PHA E -- thay hotspot bằng bản Rust đã build."""
        import importlib
        import sys

        for spec in specs:
            name = spec["function_name"]
            try:
                owner, attr, raw = self._import_target(spec)
            except Exception as exc:  # noqa: BLE001
                self.swap_report[name] = {
                    "swapped": False, "error": f"import hàm Python: {exc}"}
                continue
            kind = _kind_of(raw)
            fn = _unwrap(raw)

            try:
                ext_root = spec.get("ext_root")
                if ext_root and ext_root not in sys.path:
                    sys.path.insert(0, ext_root)
                ext = importlib.import_module(spec["ext_module"])
                rust_fn = getattr(ext, spec.get("ext_func") or name)
            except Exception as exc:  # noqa: BLE001
                self.swap_report[name] = {
                    "swapped": False,
                    "error": f"import extension Rust '{spec.get('ext_module')}': {exc}",
                }
                continue

            # KHÔNG bọc try/except quanh rust_fn: nếu bản Rust ném lỗi thì test
            # PHẢI fail. Quay âm thầm về Python sẽ biến bản dịch sai thành
            # "pass" và làm mọi số liệu correctness thành vô nghĩa.
            #
            # PHẦN 1.3 -- ĐẾM SỐ LẦN GỌI THẬT. Bộ test có thể chạy xanh mà
            # không hề chạm tới hotspot (hàm chỉ được gọi ở code path mà test
            # không đi qua). Khi đó "test vẫn pass" KHÔNG chứng minh gì về bản
            # Rust -- đó là "đúng một cách rỗng". Biến đếm này là thứ duy nhất
            # phân biệt được hai trường hợp.
            counter = {"n": 0}
            self._swap_counters[name] = counter

            @functools.wraps(fn)
            def wrapper(*args, _rust=rust_fn, _c=counter, **kwargs):
                _c["n"] += 1
                return _rust(*args, **kwargs)

            n = _patch_everywhere(fn, wrapper, owner, attr, kind)
            self.swap_report[name] = {"swapped": True, "n_refs": n}
            print(f"[rtb-swap] thay {spec['module']}:{spec['qualname']} bằng "
                  f"{spec['ext_module']}.{spec.get('ext_func') or name} "
                  f"({n} tham chiếu)")

    # -- kết thúc ----------------------------------------------------------
    def dump(self) -> None:
        if self.mode is None:
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)

        if self.mode == "swap":
            # Chot so lan goi THAT truoc khi ghi. Hotspot swap duoc nhung
            # rust_call_count == 0 se bi gan VACUOUS o tang tren.
            for name, counter in self._swap_counters.items():
                self.swap_report.setdefault(name, {})["rust_call_count"] = counter["n"]
                if counter["n"] == 0:
                    print(f"[rtb-swap] CANH BAO: '{name}' da duoc thay bang Rust "
                          f"nhung KHONG duoc bo test goi lan nao -> ket qua test "
                          f"xanh KHONG chung minh gi ve ban Rust (VACUOUS).")
            (self.out_dir / "swap_report.json").write_text(
                json.dumps(self.swap_report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return

        index: list[dict] = []
        for name, recorder in self.recorders.items():
            meta = recorder.finalize()
            if recorder.calls and cloudpickle is not None:
                blob_path = self.out_dir / f"calls_{name}.pkl"
                try:
                    blob_path.write_bytes(cloudpickle.dumps(recorder.calls))
                    meta["calls_path"] = str(blob_path)
                except Exception as exc:  # noqa: BLE001
                    meta["reason"] = R_UNREPLAYABLE_ARGS
                    meta["detail"] = f"không ghi được file lời gọi: {exc}"
            index.append(meta)

        for name, info in self.skipped.items():
            index.append({
                "function_name": name,
                "reason": info["reason"],
                "detail": info["detail"],
                "import_root": info.get("import_root"),
                "n_calls_captured": 0,
                "n_calls_seen": 0,
                "observed_arg_types": [],
                "observed_kwarg_types": {},
            })

        (self.out_dir / "capture_index.json").write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        print(f"[rtb-capture] đã ghi {len(index)} mục vào {self.out_dir}")


_PLUGIN = _Plugin()


# --------------------------------------------------------------- pytest hooks
def pytest_collection_finish(session):  # noqa: ARG001
    """Cài wrapper SAU khi pytest thu thập xong test.

    Cố ý không dùng `pytest_configure`: lúc đó module của repo thường chưa
    được import, nên chưa có gì để bọc. Sau collection thì conftest và các
    module test đã import xong -- đây là thời điểm muộn nhất còn trước khi
    test chạy, và là lúc `_patch_everywhere` bắt được nhiều tham chiếu nhất.
    """
    try:
        _PLUGIN.install()
    except Exception:  # noqa: BLE001 -- không được làm sập bộ test của repo
        print("[rtb-capture] LỖI khi cài plugin:\n" + traceback.format_exc())


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    try:
        _PLUGIN.dump()
    except Exception:  # noqa: BLE001
        print("[rtb-capture] LỖI khi ghi kết quả:\n" + traceback.format_exc())
