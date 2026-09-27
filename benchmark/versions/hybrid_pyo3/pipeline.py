"""Phiên bản HYBRID (Python + Rust qua PyO3) của pipeline.

Ý tưởng: giữ "khung" bằng Python thuần, chỉ chuyển các hàm HOTSPOT (vòng lặp
per-pixel nặng CPU) sang Rust, biên dịch thành extension module native bằng
PyO3 + maturin (xem versions/hybrid_pyo3/Cargo.toml, src/lib.rs).

Trạng thái hiện tại (scaffold): extension `hybrid_pyo3_ext` CHƯA được build,
nên mọi hàm ở đây fallback về dummy Python (cùng shape với bản python_pure)
để stage6_benchmark/bench.py chạy end-to-end được ngay cả khi chưa có Rust toolchain.

Checklist để có số liệu benchmark hybrid THẬT:
  1. Điền logic Rust thật vào versions/hybrid_pyo3/src/lib.rs (hotspot của
     edge_det/harris/hess_corner_det/im_threshold, port từ repo viraj7 --
     xem versions/python_pure/pipeline.py để biết chi tiết thuật toán gốc).
  2. `pip install maturin` rồi trong venv Python dùng để benchmark:
       cd versions/hybrid_pyo3 && maturin develop --release
     Lệnh này build & cài extension module `hybrid_pyo3_ext` vào venv.
  3. Nối lời gọi Rust thật vào từng hàm bên dưới (thay các dòng
     `raise NotImplementedError(...)` -- xem TODO trong thân từng hàm).
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger("benchmark.hybrid_pyo3.pipeline")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

try:
    import hybrid_pyo3_ext as _ext  # type: ignore  # module Rust build bằng maturin
    _HAS_EXT = True
except ImportError:
    _ext = None  # noqa: F841
    _HAS_EXT = False
    logger.warning(
        "Chưa build extension Rust 'hybrid_pyo3_ext' (chạy `maturin develop "
        "--release` trong versions/hybrid_pyo3/) -> dùng fallback Python dummy. "
        "Kết quả benchmark 'hybrid_pyo3' hiện tại KHÔNG phản ánh tốc độ Rust thật."
    )


def _bytes_from_image(image: np.ndarray) -> tuple[bytes, int, int]:
    """Chuẩn hoá ảnh (H,W) uint8 thành buffer bytes phẳng + (width, height) để
    truyền qua biên giới PyO3. TODO (tuỳ chọn): dùng crate `numpy` (PyArray)
    phía Rust thay vì Vec<u8> thô để tránh copy dữ liệu qua lại."""
    h, w = image.shape[:2]
    return image.astype(np.uint8).tobytes(), w, h


def edge_det(image: np.ndarray, sig: float = 1.0) -> np.ndarray:
    if _HAS_EXT:
        # TODO: gọi hàm Rust thật, vd:
        #   buf, w, h = _bytes_from_image(image)
        #   out = _ext.edge_det(buf, w, h, sig)
        #   return np.frombuffer(out, dtype=np.uint8).reshape(h, w)
        raise NotImplementedError(
            "Extension đã build nhưng lời gọi Rust thật chưa được nối trong "
            "hybrid_pyo3/pipeline.py::edge_det -- điền TODO ở trên."
        )
    return np.zeros_like(image, dtype=np.uint8)


def harris(image: np.ndarray) -> np.ndarray:
    if _HAS_EXT:
        # TODO: buf, w, h = _bytes_from_image(image); out = _ext.harris(buf, w, h)
        #       return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)
        raise NotImplementedError("TODO: nối lời gọi Rust thật cho harris().")
    h, w = image.shape[:2]
    return np.zeros((h, w, 3), dtype=np.uint8)


def hess_corner_det(image: np.ndarray, sig: float = 1.5, th: float = 200.0) -> np.ndarray:
    if _HAS_EXT:
        # TODO: buf, w, h = _bytes_from_image(image)
        #       out = _ext.hess_corner_det(buf, w, h, sig, th)
        #       return np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)
        raise NotImplementedError("TODO: nối lời gọi Rust thật cho hess_corner_det().")
    h, w = image.shape[:2]
    return np.zeros((h, w, 3), dtype=np.uint8)


def im_threshold(image: np.ndarray) -> np.ndarray:
    if _HAS_EXT:
        # TODO: buf, w, h = _bytes_from_image(image); out = _ext.im_threshold(buf, w, h)
        #       return np.frombuffer(out, dtype=np.uint8).reshape(h, w)
        raise NotImplementedError("TODO: nối lời gọi Rust thật cho im_threshold().")
    return np.zeros_like(image, dtype=np.uint8)


PIPELINE_REGISTRY = {
    "edge_det": edge_det,
    "harris": harris,
    "hess_corner_det": hess_corner_det,
    "im_threshold": im_threshold,
}


def run_pipeline(image: np.ndarray, functions: list[str]) -> dict[str, np.ndarray]:
    results: dict[str, np.ndarray] = {}
    for name in functions:
        if name not in PIPELINE_REGISTRY:
            raise KeyError(
                f"Không tìm thấy hàm '{name}' trong PIPELINE_REGISTRY của "
                f"versions/hybrid_pyo3/pipeline.py. Có: {list(PIPELINE_REGISTRY)}"
            )
        results[name] = PIPELINE_REGISTRY[name](image)
    return results
