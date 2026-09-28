"""PHA B -- Phát lại lời gọi đã ghi, kiểm tính tất định, so khớp Python/Rust.

VÌ SAO PHÁT LẠI PHẢI CHẠY TRONG VENV CỦA REPO (không phải venv benchmark):
cloudpickle pickle các lớp import được THEO THAM CHIẾU. Một đối số là instance
của `mypkg.models.Point` chỉ unpickle được ở nơi `import mypkg.models` chạy
được -- tức là venv riêng của repo, nơi phụ thuộc của nó đã được cài. Venv
benchmark không có những phụ thuộc đó, nên mọi việc đọc file `.pkl` đều được
đẩy sang tiến trình con (`_replay_runner.py`).

Module này chứa phần DÙNG CHUNG cho cả hai phía:
  * hàm nhẹ (chỉ stdlib) mà `_replay_runner.py` gọi bên trong venv repo;
  * hàm điều phối mà `run_pipeline.py` gọi ở venv cha để spawn tiến trình con.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.stage1_profiling.replay")

RUNNER = Path(__file__).resolve().parent / "_replay_runner.py"
DEFAULT_REPLAY_TIMEOUT_SEC = 600


# ---------------------------------------------------------------------------
# Phần chạy TRONG venv repo (chỉ stdlib + cloudpickle)
# ---------------------------------------------------------------------------
def resolve_callable(module_name: str, qualname: str, import_root: str | None = None):
    """Lấy hàm thật từ `module:qualname`. Raise nếu không được."""
    if import_root and import_root not in sys.path:
        sys.path.insert(0, import_root)
    module = importlib.import_module(module_name)
    obj = module
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def load_calls(path: str | Path) -> list[dict]:
    """Đọc file lời gọi đã ghi. Chỉ gọi được ở nơi unpickle được (venv repo)."""
    import cloudpickle

    return cloudpickle.loads(Path(path).read_bytes())


def _loads_seq(blobs: list[bytes] | None) -> tuple:
    import cloudpickle

    if not blobs:
        return ()
    return tuple(cloudpickle.loads(b) for b in blobs)


def _loads_map(blobs: dict[str, bytes] | None) -> dict:
    import cloudpickle

    if not blobs:
        return {}
    return {k: cloudpickle.loads(v) for k, v in blobs.items()}


def replay_all(fn, calls: list[dict]) -> list[dict]:
    """Phát lại từng lời gọi trên `fn`. KHÔNG raise ra ngoài.

    Mỗi lần phát lại đều unpickle đối số MỚI từ `args_pre`, nên lời gọi thứ 2
    không thừa hưởng đối số đã bị lời gọi thứ 1 sửa tại chỗ -- nếu dùng lại
    cùng một object thì hàm sửa-tại-chỗ sẽ luôn bị báo là bất tất định.
    """
    out: list[dict] = []
    for call in calls:
        args = _loads_seq(call.get("args_pre"))
        kwargs = _loads_map(call.get("kwargs_pre"))
        entry: dict = {"ok": False, "result": None, "exception": None,
                       "args_after": None}
        try:
            entry["result"] = fn(*args, **kwargs)
            entry["ok"] = True
        except BaseException as exc:  # noqa: BLE001 -- exception là kết quả hợp lệ
            entry["exception"] = f"{type(exc).__name__}: {exc}"
        # Giữ lại đối số SAU lời gọi: hàm sửa tại chỗ thì đây mới là kết quả thật.
        entry["args_after"] = args
        entry["kwargs_after"] = kwargs
        out.append(entry)
    return out


def compare_runs(run_a: list[dict], run_b: list[dict], rtol: float, atol: float) -> tuple[bool, str]:
    """So 2 lượt phát lại: giá trị trả về, exception, VÀ đối số sau lời gọi."""
    from stage1_profiling.deep_compare import deep_compare

    if len(run_a) != len(run_b):
        return False, f"số lời gọi lệch: {len(run_a)} vs {len(run_b)}"

    for i, (a, b) in enumerate(zip(run_a, run_b)):
        if (a["exception"] is None) != (b["exception"] is None):
            return False, (
                f"lời gọi #{i}: một bên ném lỗi, bên kia không "
                f"({a['exception']} vs {b['exception']})"
            )
        if a["exception"] is not None:
            if a["exception"] != b["exception"]:
                return False, f"lời gọi #{i}: exception khác nhau ({a['exception']} vs {b['exception']})"
            continue
        ok, why = deep_compare(a["result"], b["result"], rtol, atol)
        if not ok:
            return False, f"lời gọi #{i} giá trị trả về: {why}"
        # Hàm sửa đối số tại chỗ: phải so cả trạng thái đối số sau lời gọi,
        # nếu không thì hàm `def f(buf): buf[0] = 1` (trả về None) sẽ luôn khớp.
        ok, why = deep_compare(a.get("args_after"), b.get("args_after"), rtol, atol)
        if not ok:
            return False, f"lời gọi #{i} đối số sau lời gọi (hàm sửa tại chỗ): {why}"
        ok, why = deep_compare(a.get("kwargs_after"), b.get("kwargs_after"), rtol, atol)
        if not ok:
            return False, f"lời gọi #{i} đối số từ khoá sau lời gọi: {why}"
    return True, ""


# ---------------------------------------------------------------------------
# Phần chạy ở venv CHA (điều phối)
# ---------------------------------------------------------------------------
@dataclass
class ReplayVerdict:
    """Kết luận phát lại cho 1 hotspot."""

    function_name: str
    reason: str | None = None       # None = ổn, chờ bước sau
    detail: str = ""
    n_calls: int = 0
    observed_arg_types: list[str] = field(default_factory=list)
    observed_kwarg_types: dict[str, str] = field(default_factory=dict)
    tier: str = ""
    tier_reason: str = ""
    # correctness theo từng phiên bản: {"rust_pure": {...}, "hybrid_pyo3": {...}}
    correctness: dict = field(default_factory=dict)
    # Ví dụ vào/ra thật, đưa vào prompt Generator ở CẢ HAI nhánh ablation.
    io_examples: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "function_name": self.function_name,
            "reason": self.reason,
            "detail": self.detail,
            "n_calls": self.n_calls,
            "observed_arg_types": self.observed_arg_types,
            "observed_kwarg_types": self.observed_kwarg_types,
            "tier": self.tier,
            "tier_reason": self.tier_reason,
            "correctness": self.correctness,
            "io_examples": self.io_examples,
        }


def run_replay_checks(
    py: Path,
    work_dir: Path,
    capture_dir: Path,
    benchmark_root: Path,
    rtol: float = 1e-5,
    atol: float = 1e-8,
    rust_targets: dict | None = None,
    timeout_sec: int = DEFAULT_REPLAY_TIMEOUT_SEC,
) -> tuple[dict[str, ReplayVerdict], str]:
    """Spawn `_replay_runner.py` TRONG venv repo để:
      1. phát lại 2 lần trên bản Python -> NONDETERMINISTIC nếu lệch;
      2. phân tầng kiểu (Tầng 1 / Tầng 2 / ngoài tầng) từ đối số thật;
      3. nếu có `rust_targets`, so khớp bản Rust với bản Python.

    Trả về ({tên hàm: ReplayVerdict}, lỗi mức tiến trình).
    """
    from stage5_compiler_in_the_loop.repo_runner import build_child_env

    out_path = Path(capture_dir) / "replay_verdicts.json"
    cmd = [
        str(py), str(RUNNER),
        "--capture-dir", str(capture_dir),
        "--out", str(out_path),
        "--rtol", repr(rtol), "--atol", repr(atol),
    ]
    if rust_targets:
        rt_path = Path(capture_dir) / "rust_targets.json"
        rt_path.write_text(json.dumps(rust_targets, ensure_ascii=False), encoding="utf-8")
        cmd += ["--rust-targets", str(rt_path)]

    env = build_child_env({
        "PYTHONPATH": os.pathsep.join(
            [str(Path(benchmark_root).resolve()), str(Path(work_dir).resolve())]
        ),
    })
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_sec,
            cwd=str(work_dir), env=env,
        )
    except subprocess.TimeoutExpired:
        return {}, f"phát lại quá {timeout_sec}s -> huỷ"
    except OSError as exc:
        return {}, f"không spawn được tiến trình phát lại: {exc}"

    if not out_path.exists():
        return {}, (
            f"tiến trình phát lại không ghi được kết quả (exit={proc.returncode}). "
            f"stderr: {(proc.stderr or '')[-500:]}"
        )

    try:
        raw = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"không đọc được kết quả phát lại: {exc}"

    verdicts: dict[str, ReplayVerdict] = {}
    for item in raw:
        verdicts[item["function_name"]] = ReplayVerdict(
            function_name=item["function_name"],
            reason=item.get("reason"),
            detail=item.get("detail", ""),
            n_calls=int(item.get("n_calls", 0) or 0),
            observed_arg_types=item.get("observed_arg_types") or [],
            observed_kwarg_types=item.get("observed_kwarg_types") or {},
            tier=item.get("tier", ""),
            tier_reason=item.get("tier_reason", ""),
            correctness=item.get("correctness") or {},
            io_examples=item.get("io_examples") or [],
        )
    return verdicts, ""
