"""Wrapper Python cho phần PyO3 IN-PROCESS của bản Rust THUẦN (rust_pure).

TRƯỚC ĐÂY rust_pure được benchmark bằng cách stage6_benchmark/bench.py spawn 1 tiến
trình con chạy versions/rust_pure/src/main.rs (CLI, nhận argv, lặp N lần BÊN
TRONG tiến trình con đó, đo bằng std::time::Instant phía Rust). Cách đo đó
công bằng hơn kiểu spawn-mỗi-lần-gọi, nhưng vẫn KHÔNG cùng điều kiện với
python_pure/hybrid_pyo3 (đo bằng time.perf_counter() NGAY TRONG tiến trình
Python đang chạy, không spawn tiến trình nào). Use-case thật của đồ án là gọi
hàm preprocessing LẶP LẠI nhiều lần trong CÙNG 1 tiến trình training (theo
batch/epoch) -- không phải chạy như 1 chương trình rời mỗi lần gọi.

Module này import extension PyO3 `rust_pure_ext` (build từ
versions/rust_pure/pyo3_ext/ -- KHÔNG phải versions/rust_pure/src/main.rs,
main.rs vẫn được giữ nguyên làm CLI độc lập, xem README.md) để gọi hàm Rust
TRỰC TIẾP trong tiến trình Python, cho phép stage6_benchmark/bench.py đo bằng đúng
time.perf_counter() như 2 phiên bản còn lại -- xem stage6_benchmark/bench.py::_bench_rust_pure.

KHÁC với versions/hybrid_pyo3/pipeline.py: khi extension CHƯA build, module
này KHÔNG fallback sang dummy Python. Đo thời gian chạy Python rồi gắn nhãn
"rust_pure" sẽ gây hiểu sai (đây là phiên bản LẼ RA phải toàn Rust). Thay vào
đó `HAS_EXT=False` và PIPELINE_REGISTRY rỗng; stage6_benchmark/bench.py tự phát hiện và
BỎ QUA đo rust_pure lần chạy đó (log warning rõ ràng, không crash) -- đúng
pattern đã có sẵn khi trước đây thiếu binary CLI.
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("benchmark.rust_pure.pipeline")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

try:
    import rust_pure_ext as _ext  # type: ignore  # module Rust build bằng maturin (pyo3_ext/)
    HAS_EXT = True
except ImportError:
    _ext = None  # noqa: F841
    HAS_EXT = False
    logger.warning(
        "Chưa build extension Rust 'rust_pure_ext' (chạy `maturin develop "
        "--release` trong versions/rust_pure/pyo3_ext/) -> stage6_benchmark/bench.py sẽ "
        "BỎ QUA đo rust_pure (không fallback dummy, vì đo Python rồi gắn nhãn "
        "'rust_pure' sẽ gây hiểu sai)."
    )


def _bytes_from_image(image: np.ndarray) -> tuple[bytes, int, int]:
    """Giống hệt hàm cùng tên trong versions/hybrid_pyo3/pipeline.py (duplicate
    có chủ đích -- xem docstring module đó về I/O qua Vec<u8> thô)."""
    h, w = image.shape[:2]
    return image.astype(np.uint8).tobytes(), w, h


def edge_det(image: np.ndarray, sig: float = 1.0) -> np.ndarray:
    buf, w, h = _bytes_from_image(image)
    out = _ext.edge_det(buf, w, h, sig)
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w)


def harris(image: np.ndarray) -> np.ndarray:
    buf, w, h = _bytes_from_image(image)
    out = _ext.harris(buf, w, h)
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)


def hess_corner_det(image: np.ndarray, sig: float = 1.5, th: float = 200.0) -> np.ndarray:
    buf, w, h = _bytes_from_image(image)
    out = _ext.hess_corner_det(buf, w, h, sig, th)
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)


def im_threshold(image: np.ndarray) -> np.ndarray:
    buf, w, h = _bytes_from_image(image)
    out = _ext.im_threshold(buf, w, h)
    return np.frombuffer(out, dtype=np.uint8).reshape(h, w)


# Rỗng khi chưa build extension -- stage6_benchmark/bench.py kiểm tra HAS_EXT trước khi
# đụng tới registry này, nên không cần entry giả ở đây.
PIPELINE_REGISTRY = (
    {
        "edge_det": edge_det,
        "harris": harris,
        "hess_corner_det": hess_corner_det,
        "im_threshold": im_threshold,
    }
    if HAS_EXT
    else {}
)


def run_pipeline(image: np.ndarray, functions: list[str]) -> dict[str, np.ndarray]:
    if not HAS_EXT:
        raise RuntimeError(
            "Extension 'rust_pure_ext' chưa được build. Chạy `maturin develop "
            "--release` trong versions/rust_pure/pyo3_ext/ trước."
        )
    results: dict[str, np.ndarray] = {}
    for name in functions:
        if name not in PIPELINE_REGISTRY:
            raise KeyError(
                f"Không tìm thấy hàm '{name}' trong PIPELINE_REGISTRY của "
                f"versions/rust_pure/pipeline.py. Có: {list(PIPELINE_REGISTRY)}"
            )
        results[name] = PIPELINE_REGISTRY[name](image)
    return results
