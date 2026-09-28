"""PHA F -- Đo BASELINE PYTHON và BẢN RUST trong CÙNG MỘT tiến trình.

VẤN ĐỀ ĐƯỢC SỬA (lỗ hổng #3 trong AUDIT_REPORT.md): trước Pha F, `python_pure`
chỉ được đo MỘT LẦN in-process ở vòng 1, còn từ vòng 2 `rust_pure` được đo lại
bằng một subprocess mới. Speedup vòng >= 2 vì thế là

    mean(Python đo in-process) / mean(Rust đo qua subprocess)

-- hai cách đo, hai tiến trình, hai trạng thái interpreter khác nhau. Tỉ số đó
không so được với vòng 1, cũng không so được với chính nó giữa các vòng.

Script này đo MỌI phiên bản trong CÙNG một tiến trình, CÙNG bộ đối số, CÙNG
vòng lặp `warmup + N`, nên tỉ số lấy ra là tỉ số của hai con số sinh cùng điều
kiện. Nó chạy trong venv RIÊNG của repo vì đó là nơi duy nhất vừa import được
module của repo vừa nạp được extension Rust vừa build.

XỬ LÝ HÀM SỬA ĐỐI SỐ TẠI CHỖ: mọi bản sao đối số được dựng TRƯỚC vòng đo (một
bản cho mỗi lần lặp), nên `accumulate_inplace` không cộng dồn qua các lần lặp
và chi phí deepcopy/unpickle KHÔNG bị tính vào thời gian.
"""
from __future__ import annotations

import argparse
import copy
import json
import platform
import sys
import time
import traceback
from pathlib import Path


def _load_first_call(calls: list[dict]) -> tuple[tuple, dict]:
    """Lấy đối số của lời gọi ĐẦU TIÊN làm workload đo.

    Cố ý dùng một lời gọi duy nhất (không phải tất cả): phép đo cần workload
    CỐ ĐỊNH để so được giữa các phiên bản và giữa các vòng. Các lời gọi còn
    lại đã được dùng cho phép so khớp correctness ở Pha B.
    """
    from stage1_profiling.replay import _loads_map, _loads_seq

    first = calls[0]
    return _loads_seq(first.get("args_pre")), _loads_map(first.get("kwargs_pre"))


def _describe_input(args: tuple, kwargs: dict) -> dict:
    """Đặc tả input để ghi vào kết quả (lỗ hổng #7: phải diễn giải được speedup).

    Dữ liệu nhỏ thì Rust qua PyO3 có thể CHẬM HƠN Python vì chi phí gọi cố
    định. Không ghi lại kích thước input thì con số speedup không đọc được.
    """
    from stage1_profiling.deep_compare import describe_type

    def _n_elements(v):
        try:
            if hasattr(v, "size"):
                return int(v.size)
            if isinstance(v, (list, tuple, set, frozenset, dict, str, bytes)):
                return len(v)
        except Exception:  # noqa: BLE001
            pass
        return None

    return {
        "arg_types": [describe_type(a) for a in args],
        "kwarg_types": {k: describe_type(v) for k, v in kwargs.items()},
        "arg_n_elements": [_n_elements(a) for a in args],
    }


def _time_callable(fn, args_copies: list, kwargs_copies: list, warmup: int, iterations: int):
    """Warmup rồi đo `iterations` lần bằng `time.perf_counter`.

    Giữ ĐÚNG cách đo của `stage6_benchmark/bench.py::_time_callable` để số liệu
    hai đường đi (legacy và động) so sánh được với nhau.
    """
    for i in range(warmup):
        fn(*args_copies[i], **kwargs_copies[i])
    durations: list[float] = []
    for i in range(warmup, warmup + iterations):
        start = time.perf_counter()
        fn(*args_copies[i], **kwargs_copies[i])
        durations.append(time.perf_counter() - start)
    return durations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--targets", required=True, help="file JSON đặc tả phiên bản")
    parser.add_argument("--out", required=True)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()

    from stage1_profiling.replay import load_calls, resolve_callable

    capture_dir = Path(args.capture_dir)
    index_path = capture_dir / "capture_index.json"
    targets = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    index = {
        m["function_name"]: m
        for m in json.loads(index_path.read_text(encoding="utf-8"))
    }

    out: dict = {
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "processor": platform.processor(),
        },
        "warmup": args.warmup,
        "iterations": args.iterations,
        "hotspots": {},
    }

    for name, spec in targets.items():
        entry: dict = {"versions": {}, "input_spec": {}, "error": ""}
        meta = index.get(name) or {}
        calls_path = meta.get("calls_path")
        if not calls_path or not Path(calls_path).exists():
            entry["error"] = "không có lời gọi đã ghi để làm workload đo"
            out["hotspots"][name] = entry
            continue

        try:
            calls = load_calls(calls_path)
            base_args, base_kwargs = _load_first_call(calls)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"không nạp được workload: {type(exc).__name__}: {exc}"
            out["hotspots"][name] = entry
            continue

        entry["input_spec"] = _describe_input(base_args, base_kwargs)

        total = args.warmup + args.iterations
        for version, vspec in (spec or {}).items():
            vres: dict = {"durations": None, "error": ""}
            try:
                if vspec.get("ext_module"):
                    fn = resolve_callable(
                        vspec["ext_module"], vspec.get("ext_func") or name,
                        vspec.get("ext_root"),
                    )
                else:
                    fn = resolve_callable(
                        vspec["module"], vspec["qualname"], vspec.get("import_root")
                    )
            except Exception as exc:  # noqa: BLE001
                vres["error"] = f"không import được: {type(exc).__name__}: {exc}"
                entry["versions"][version] = vres
                continue

            # Dựng sẵn bản sao cho TỪNG lần lặp -> hàm sửa đối số tại chỗ
            # không cộng dồn, và deepcopy không bị tính vào thời gian đo.
            try:
                args_copies = [copy.deepcopy(base_args) for _ in range(total)]
                kwargs_copies = [copy.deepcopy(base_kwargs) for _ in range(total)]
            except Exception as exc:  # noqa: BLE001
                vres["error"] = f"không deepcopy được đối số: {exc}"
                entry["versions"][version] = vres
                continue

            try:
                vres["durations"] = _time_callable(
                    fn, args_copies, kwargs_copies, args.warmup, args.iterations
                )
            except Exception as exc:  # noqa: BLE001
                vres["error"] = (
                    f"lời gọi ném lỗi khi đo: {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc(limit=2)
                )
            entry["versions"][version] = vres

        out["hotspots"][name] = entry

    Path(args.out).write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"[rtb-pair] đã đo {len(out['hotspots'])} hotspot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
