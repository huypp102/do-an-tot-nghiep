"""Script CON, chỉ được gọi bởi stage6_benchmark/measure_subprocess.py.

Chạy trong 1 tiến trình MỚI để import extension PyO3 VỪA ĐƯỢC BUILD LẠI --
extension native không thể reload an toàn trong tiến trình đang chạy (module
.pyd/.so đã nạp sẽ không bị thay thế dù file trên đĩa đã đổi).

Logic đo giữ ĐÚNG như stage6_benchmark/bench.py::_time_callable: warmup N lần
rồi đo `iterations` lần bằng time.perf_counter(). Kết quả in ra stdout dưới
dạng 1 dòng JSON để tiến trình cha parse.

KHÔNG dùng file này cho vòng lặp training thật -- thiết kế in-process đã chốt
cho bối cảnh đó vẫn giữ nguyên trong bench.py. File này CHỈ phục vụ việc đo
lại giữa các vòng tối ưu (vài lần cho mỗi hotspot).
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-root", required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--image", required=True, help="file .npy chứa ảnh input")
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=20)
    # Cho phép trỏ sang module pipeline khác -- dùng khi kiểm thử harness này
    # mà không cần Rust toolchain.
    parser.add_argument("--pipeline-module", default="versions.rust_pure.pipeline")
    args = parser.parse_args()

    root = str(Path(args.benchmark_root).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        import numpy as np

        image = np.load(args.image)
        pipeline = importlib.import_module(args.pipeline_module)
        registry = getattr(pipeline, "PIPELINE_REGISTRY", {})
        fn = registry.get(args.function)
        if fn is None:
            print(json.dumps({
                "ok": False,
                "error": (
                    f"module {args.pipeline_module} không có hàm "
                    f"'{args.function}' (extension chưa build hoặc build thiếu hàm)"
                ),
            }))
            return 1

        for _ in range(max(args.warmup, 0)):
            fn(image)

        durations = []
        for _ in range(max(args.iterations, 1)):
            start = time.perf_counter()
            fn(image)
            durations.append(time.perf_counter() - start)

        print(json.dumps({"ok": True, "durations": durations}))
        return 0
    except Exception as exc:  # noqa: BLE001 -- báo lỗi qua JSON, không để cha thấy traceback thô
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
