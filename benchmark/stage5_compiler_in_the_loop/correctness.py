"""Kiểm tra TÍNH ĐÚNG ĐẮN: output bản Rust có khớp bản Python không.

Chạy SAU Stage 5 (đã biên dịch được) và TRƯỚC Stage 6 (đo tốc độ). Lý do thứ
tự đó: đo tốc độ của một bản dịch SAI là vô nghĩa, thậm chí gây hiểu lầm --
code sai thường nhanh hơn vì nó bỏ bớt việc.

    correctness_match = "MATCH"    -- mọi sample input đều cho kết quả khớp
                        "MISMATCH" -- có ít nhất 1 sample cho kết quả lệch
                        "ERROR"    -- không chạy/so sánh được (chưa build
                                      extension, hàm ném exception, ...)

Ngưỡng so khớp:
    numpy array  -> np.allclose(rtol=1e-5, atol=1e-8)  (kèm kiểm tra shape)
    số vô hướng  -> math.isclose(rel_tol=1e-5, abs_tol=1e-8)
    còn lại      -> so sánh bằng ==

LƯU Ý VỀ 2 VÒNG LẶP KHÁC NHAU (rất dễ nhầm):
  - Vòng ở Stage 5: sửa code cho tới khi BIÊN DỊCH ĐƯỢC và CHẠY ĐÚNG.
  - Vòng ở Stage 6 (optimization_loop.max_rounds): tối ưu thêm cho NHANH HƠN.
Correctness thuộc nhóm thứ nhất và luôn CHẶN TRƯỚC: hàm nào MISMATCH thì
không được đem đi đo tốc độ, cũng không được vào vòng tối ưu tốc độ nào cả.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

logger = logging.getLogger("benchmark.stage5_compiler_in_the_loop.correctness")

MATCH = "MATCH"
MISMATCH = "MISMATCH"
ERROR = "ERROR"

DEFAULT_RTOL = 1e-5
DEFAULT_ATOL = 1e-8


@dataclass
class CorrectnessResult:
    """Kết quả kiểm tra đúng đắn cho ĐÚNG 1 hotspot."""

    function_name: str
    status: str = ERROR
    n_samples: int = 0
    n_matched: int = 0
    detail: str = ""
    mismatches: list[str] = field(default_factory=list)

    @property
    def blocks_benchmark(self) -> bool:
        """True nếu KHÔNG được đem hàm này đi đo tốc độ."""
        return self.status != MATCH


def compare_outputs(
    left: Any, right: Any, rtol: float = DEFAULT_RTOL, atol: float = DEFAULT_ATOL
) -> tuple[bool, str]:
    """So sánh 2 giá trị đầu ra. Trả về (khớp?, mô tả lệch nếu có)."""
    # --- numpy array ---
    if isinstance(left, np.ndarray) or isinstance(right, np.ndarray):
        try:
            a = np.asarray(left)
            b = np.asarray(right)
        except Exception as exc:  # noqa: BLE001
            return False, f"không chuyển được về numpy array: {exc}"
        if a.shape != b.shape:
            return False, f"lệch shape: {a.shape} vs {b.shape}"
        if a.dtype.kind in "biufc" and b.dtype.kind in "biufc":
            if np.allclose(a, b, rtol=rtol, atol=atol, equal_nan=True):
                return True, ""
            diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
            n_bad = int((~np.isclose(a, b, rtol=rtol, atol=atol, equal_nan=True)).sum())
            return False, (
                f"lệch {n_bad}/{a.size} phần tử, sai số lớn nhất {diff.max():.6g}"
            )
        return (bool(np.array_equal(a, b)), "" if np.array_equal(a, b) else "mảng không bằng nhau")

    # --- số vô hướng ---
    if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
            and not isinstance(left, bool) and not isinstance(right, bool):
        if math.isclose(float(left), float(right), rel_tol=rtol, abs_tol=atol):
            return True, ""
        return False, f"lệch giá trị: {left} vs {right}"

    # --- chuỗi/tuple: so từng phần tử ---
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False, f"lệch độ dài: {len(left)} vs {len(right)}"
        for i, (x, y) in enumerate(zip(left, right)):
            ok, why = compare_outputs(x, y, rtol, atol)
            if not ok:
                return False, f"phần tử [{i}]: {why}"
        return True, ""

    # --- còn lại ---
    if left == right:
        return True, ""
    return False, f"khác nhau: {type(left).__name__} vs {type(right).__name__}"


def build_sample_inputs(image: np.ndarray, n_extra: int = 2) -> list[np.ndarray]:
    """Bộ sample input để so khớp. Dùng ảnh thật đang benchmark làm mẫu
    chính, cộng thêm vài biến thể nhỏ để bắt lỗi biên (ảnh nhỏ, ảnh phẳng).

    Cố ý dùng ảnh nhỏ cho biến thể: so khớp cần chạy nhanh, không phải đo
    hiệu năng.
    """
    samples: list[np.ndarray] = [np.asarray(image)]
    if n_extra >= 1:
        h, w = image.shape[:2]
        crop = np.asarray(image)[: min(32, h), : min(32, w)]
        samples.append(np.ascontiguousarray(crop))
    if n_extra >= 2:
        # Ảnh hằng số: bắt lỗi chia cho 0 / nhánh biên mà ảnh thật không chạm tới.
        samples.append(np.full((16, 16), 128, dtype=np.uint8))
    return samples


def check_function(
    function_name: str,
    python_fn: Callable | None,
    rust_fn: Callable | None,
    sample_inputs: list[np.ndarray],
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> CorrectnessResult:
    """So khớp output Python vs Rust trên từng sample. KHÔNG raise."""
    result = CorrectnessResult(function_name=function_name, n_samples=len(sample_inputs))

    if python_fn is None:
        result.detail = "không có implementation Python để làm chuẩn so sánh"
        return result
    if rust_fn is None:
        result.detail = (
            "không có implementation Rust để so sánh (extension chưa build -- "
            "chạy `maturin develop --release` trong versions/rust_pure/pyo3_ext/)"
        )
        return result

    for idx, sample in enumerate(sample_inputs):
        try:
            expected = python_fn(sample)
        except Exception as exc:  # noqa: BLE001
            result.detail = f"bản Python ném lỗi trên sample #{idx}: {type(exc).__name__}: {exc}"
            return result
        try:
            actual = rust_fn(sample)
        except Exception as exc:  # noqa: BLE001
            result.detail = f"bản Rust ném lỗi trên sample #{idx}: {type(exc).__name__}: {exc}"
            return result

        ok, why = compare_outputs(expected, actual, rtol, atol)
        if ok:
            result.n_matched += 1
        else:
            result.mismatches.append(f"sample #{idx} (shape={np.asarray(sample).shape}): {why}")

    if result.mismatches:
        result.status = MISMATCH
        result.detail = result.mismatches[0]
    else:
        result.status = MATCH
        result.detail = f"khớp trên toàn bộ {result.n_samples} sample"
    return result


def run_correctness_checks(
    function_names: list[str],
    image: np.ndarray,
    python_registry: dict[str, Callable],
    rust_registry: dict[str, Callable],
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> dict[str, CorrectnessResult]:
    """Kiểm tra đúng đắn cho danh sách hotspot. Trả về {tên hàm: kết quả}."""
    samples = build_sample_inputs(image)
    results: dict[str, CorrectnessResult] = {}
    for name in function_names:
        res = check_function(
            name, python_registry.get(name), rust_registry.get(name), samples, rtol, atol
        )
        results[name] = res
        if res.status == MATCH:
            logger.info("Correctness [%s]: MATCH (%s)", name, res.detail)
        elif res.status == MISMATCH:
            logger.error(
                "Correctness [%s]: MISMATCH -- %s. KHÔNG đem đi đo tốc độ.",
                name, res.detail,
            )
        else:
            logger.warning("Correctness [%s]: ERROR -- %s", name, res.detail)
    return results
