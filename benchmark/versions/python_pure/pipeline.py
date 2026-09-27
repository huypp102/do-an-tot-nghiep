"""Phiên bản Python THUẦN của pipeline tiền xử lý ảnh (baseline).

SCAFFOLD: mỗi hàm hiện CHƯA có logic xử lý ảnh thật, chỉ trả về dummy data
ĐÚNG SHAPE để stage6_benchmark/bench.py chạy end-to-end được ngay. Khi điền logic thật,
port trực tiếp từ các file gốc trong repo tham chiếu:

    https://github.com/viraj7/Computer-Vision-Image-processing

Lưu ý khi port (đã xác minh bằng cách đọc source gốc trên GitHub):
  * Input gốc luôn là `cv2.imread(path, 0)` -> ảnh XÁM (grayscale), uint8, 2D
    (khớp EXPECTED_SPEC trong data/loader.py). data/loader.py đã đảm bảo
    `image` truyền vào các hàm dưới đây đúng định dạng này.
  * Các hàm gốc KHÔNG return giá trị -- chúng gọi cv2.imshow()/cv2.waitKey()
    để hiển thị từng bước trung gian (tương tác, cần GUI). Khi port sang đây
    BẮT BUỘC phải:
      1) Xoá hết cv2.imshow / cv2.waitKey / cv2.destroyAllWindows (benchmark
         chạy headless, không có GUI, các lệnh này sẽ treo hoặc lỗi).
      2) Thêm `return <mảng kết quả cuối>` ở cuối hàm.
  * "harris corner detection.py" và image_entropy.py viết bằng cú pháp
    Python 2 (`print bien` thay vì `print(bien)`) -> phải sửa sang Python 3
    trước khi copy logic vào đây (nếu không sẽ bị SyntaxError).
  * imtools.py bị cắt cụt khi crawl qua GitHub preview, cần tự mở file gốc để
    xem đầy đủ (có ít nhất `get_image_list`, `im_resize`, và 1 hàm nữa bị cụt).
"""
from __future__ import annotations

import numpy as np

__all__ = [
    "edge_det",
    "harris",
    "hess_corner_det",
    "im_threshold",
    "run_pipeline",
    "PIPELINE_REGISTRY",
]


def edge_det(image: np.ndarray, sig: float = 1.0, ar: list[int] | None = None) -> np.ndarray:
    """TODO: port từ edge_detection.py::edge_det (Canny-style, tự cài tay,
    KHÔNG gọi cv2.Canny). Gốc: Gaussian smoothing X/Y riêng biệt -> đạo hàm
    của Gaussian (derivative of Gaussian) trên mỗi trục -> magnitude +
    orientation -> non-maximum suppression theo hướng gradient -> hysteresis
    thresholding (high=0.7*max, low=0.3*min). Ghi chú cuối file gốc: sig=1
    cho kết quả tốt nhất, sig càng tăng edge detection càng kém.

    Input: image (H,W) uint8 grayscale. Output kỳ vọng: (H,W) uint8, ảnh nhị
    phân/biên đã hysteresis-threshold (cùng shape/dtype input).
    """
    ar = ar if ar is not None else [-1, 0, 1]
    # --- DUMMY: trả về mảng zeros cùng shape/dtype ảnh input ---
    return np.zeros_like(image, dtype=np.uint8)


def harris(image: np.ndarray) -> np.ndarray:
    """TODO: port từ "harris corner detection.py"::harris.
    Gốc: Gaussian smooth (sigma=1.5) -> đạo hàm Ix/Iy bằng filter2D với kernel
    [[-1,0,1]]x3 (và transpose) -> Laplace(Ix), Laplace(Iy), Laplace(Ixy) ->
    response Harris R = det(H) - alpha*trace(H)^2 với alpha=0.04, H = ma trận
    2x2 [[Lx*Lx, Lxy],[Lxy, Ly*Ly]] mỗi pixel -> đánh dấu corner nơi
    R > 0.99*max(R) bằng cách tô đỏ [0,0,255] trên ảnh RGB.

    Input: image (H,W) uint8 grayscale. Output kỳ vọng: (H,W,3) uint8 RGB
    (ảnh gốc chuyển RGB + overlay corner màu đỏ) -- KHÁC input về số chiều.
    """
    # --- DUMMY: trả về ảnh RGB toàn đen, đúng shape mà hàm thật sẽ trả ---
    h, w = image.shape[:2]
    return np.zeros((h, w, 3), dtype=np.uint8)


def hess_corner_det(image: np.ndarray, sig: float = 1.5, th: float = 200.0) -> np.ndarray:
    """TODO: port từ "hessian corner detection.py"::hess_corner_det.
    Gốc: Gaussian smooth(sig) -> Ix, Iy (đạo hàm bậc 1, filter2D) -> Ixx, Iyy,
    Ixy (đạo hàm bậc 2) -> với mỗi pixel tính eigenvalues l1,l2 của ma trận
    Hessian [[Ixx,Ixy],[Ixy,Iyy]] -> đánh dấu corner nếu |l1| > th VÀ |l2| > th.
    Repo dùng (sig, th) mẫu: (1.5, 200), (2, 210), (1.5, 150) cho 3 ảnh khác nhau.

    Input: image (H,W) uint8 grayscale. Output kỳ vọng: (H,W,3) uint8 RGB
    (giống harris -- overlay corner màu đỏ trên ảnh gốc chuyển RGB).
    """
    h, w = image.shape[:2]
    return np.zeros((h, w, 3), dtype=np.uint8)


def im_threshold(image: np.ndarray) -> np.ndarray:
    """TODO: port từ image_entropy.py::im_threshold (tách nền/vật thể bằng
    entropy). Gốc: tính histogram 256 bin -> với mỗi ngưỡng T trong [0,255)
    chia phân bố thành lớp A=[0,T) và B=[T,255], tính entropy H(A)+H(B) của
    2 lớp -> chọn T tối đa hoá tổng entropy -> nhị phân hoá: pixel < T -> 0,
    pixel >= T -> 255.

    Input: image (H,W) uint8 grayscale. Output kỳ vọng: (H,W) uint8, chỉ có
    giá trị {0, 255} (ảnh nhị phân nền/vật thể).
    """
    return np.zeros_like(image, dtype=np.uint8)


PIPELINE_REGISTRY = {
    "edge_det": edge_det,
    "harris": harris,
    "hess_corner_det": hess_corner_det,
    "im_threshold": im_threshold,
}


def run_pipeline(image: np.ndarray, functions: list[str]) -> dict[str, np.ndarray]:
    """Chạy tuần tự các hàm trong `functions` trên CÙNG ảnh input gốc (4 hàm
    này độc lập trong repo gốc, không chain output hàm này làm input hàm
    khác). Trả về dict {tên hàm: mảng kết quả}."""
    results: dict[str, np.ndarray] = {}
    for name in functions:
        if name not in PIPELINE_REGISTRY:
            raise KeyError(
                f"Không tìm thấy hàm '{name}' trong PIPELINE_REGISTRY của "
                f"versions/python_pure/pipeline.py. Có: {list(PIPELINE_REGISTRY)}"
            )
        results[name] = PIPELINE_REGISTRY[name](image)
    return results
