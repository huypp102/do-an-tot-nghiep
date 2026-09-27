"""Nạp ảnh đầu vào cho benchmark, theo `data.source` trong config.yaml.

Định dạng input mà pipeline THẬT (viraj7/Computer-Vision-Image-processing)
mong đợi, xác định bằng cách đọc trực tiếp source gốc trên GitHub:

    edge_detection.py             : cv2.imread(path, 0)  -> grayscale uint8, ndim=2
    "harris corner detection.py"  : cv2.imread(path, 0)  -> grayscale uint8, ndim=2
    "hessian corner detection.py" : cv2.imread(path, 0)  -> grayscale uint8, ndim=2
    image_entropy.py              : cv2.imread(path, 0)  -> grayscale uint8, ndim=2

=> EXPECTED_SPEC bên dưới = ảnh xám (1 kênh), dtype uint8, mảng 2 chiều (H, W).

TODO: nếu khi điền logic thật bạn thấy hàm nào thực sự cần định dạng khác
(vd `cv2.cvtColor(im, cv2.COLOR_GRAY2RGB)` trong harris/hessian là ảnh KẾT
QUẢ dùng để vẽ overlay, KHÔNG phải input) thì input vẫn là grayscale như trên
-- không cần sửa EXPECTED_SPEC. Chỉ sửa nếu bạn đổi sang dùng hàm OpenCV
built-in nhận RGB trực tiếp.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config_loader import BENCHMARK_ROOT, ensure_utf8_stdio, load_config  # noqa: E402

# Phải gọi TRƯỚC logging.basicConfig()/mọi print() -- tránh UnicodeEncodeError
# khi log/in text tiếng Việt trên Windows console dùng codepage cp1252.
ensure_utf8_stdio()

logger = logging.getLogger("benchmark.data.loader")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

EXPECTED_SPEC: dict[str, Any] = {
    "ndim": 2,          # grayscale, không có channel dim
    "dtype": "uint8",   # 0-255
}

_KNOWN_SKIMAGE_IMAGES = {"camera", "coins", "astronaut", "checkerboard", "moon", "page"}


def _placeholder_image(shape: tuple[int, int] = (256, 256)) -> np.ndarray:
    """Ảnh placeholder xác định (deterministic), dùng khi chưa có ảnh mẫu thật.

    TODO: thay bằng ảnh thật copy từ repo viraj7 (chess.jpg, horse.jpg,
    monument.jpg, pyramid.jpg, reindeer.jpg, scene.jpg, starfish.jpg) đặt vào
    data/sample_input/ rồi điền `data.sample_input_file` trong config.yaml.
    """
    h, w = shape
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    gradient = (xx * 255 // max(w - 1, 1) + yy * 255 // max(h - 1, 1)) // 2
    checker = ((xx // 16 + yy // 16) % 2) * 40
    img = np.clip(gradient + checker, 0, 255).astype(np.uint8)
    return img


def _load_repo_sample(cfg: dict[str, Any]) -> tuple[np.ndarray, str]:
    data_cfg = cfg["data"]
    sample_dir = BENCHMARK_ROOT / data_cfg["sample_input_dir"]
    filename = data_cfg.get("sample_input_file")

    if filename:
        path = sample_dir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"data.sample_input_file = '{filename}' nhưng không tìm thấy tại {path}. "
                "Copy ảnh mẫu thật (vd chess.jpg từ repo viraj7) vào đó, hoặc set "
                "sample_input_file: null trong config.yaml để dùng ảnh placeholder."
            )
        # TODO: repo viraj7 dùng cv2.imread(path, 0). Dùng Pillow ở đây để scaffold
        # không phụ thuộc cứng vào OpenCV; đổi sang cv2.imread nếu muốn khớp 100%.
        try:
            from PIL import Image
        except ImportError as exc:
            raise ImportError(
                "Cần cài Pillow để đọc ảnh mẫu thật: pip install pillow"
            ) from exc
        img = np.array(Image.open(path).convert("L"), dtype=np.uint8)
        return img, f"repo_sample:{filename}"

    logger.warning(
        "data.sample_input_file chưa được điền trong config.yaml -> dùng ảnh "
        "PLACEHOLDER (KHÔNG phải ảnh thật từ repo viraj7). Xem hướng dẫn trong "
        "data/sample_input/PLACEHOLDER.txt."
    )
    return _placeholder_image(), "repo_sample:placeholder"


def _load_library_dataset(cfg: dict[str, Any]) -> tuple[np.ndarray, str]:
    data_cfg = cfg["data"]
    name = data_cfg.get("library_dataset_name", "camera")
    backend = data_cfg.get("library_dataset_backend", "skimage")

    if backend == "skimage":
        try:
            import skimage.data as skdata
        except ImportError:
            logger.warning(
                "Chưa cài scikit-image (pip install scikit-image) -> dùng ảnh "
                "placeholder thay cho library_dataset_name='%s'.", name,
            )
            return _placeholder_image(), f"library_dataset:skimage:{name}:placeholder"

        if not hasattr(skdata, name):
            raise ValueError(
                f"skimage.data không có ảnh '{name}'. Các lựa chọn phổ biến: "
                f"{sorted(_KNOWN_SKIMAGE_IMAGES)}"
            )
        img = getattr(skdata, name)()
        return np.asarray(img), f"library_dataset:skimage:{name}"

    if backend == "torchvision":
        # TODO: hoàn thiện nhánh này nếu cần lấy ảnh từ dataset thật (vd
        # ImageFolder trỏ tới thư mục ảnh cục bộ) thay vì FakeData minh hoạ.
        try:
            import torchvision
        except ImportError:
            logger.warning(
                "Chưa cài torchvision (pip install torchvision) -> dùng ảnh "
                "placeholder thay cho library_dataset backend='torchvision'."
            )
            return _placeholder_image(), "library_dataset:torchvision:placeholder"
        ds = torchvision.datasets.FakeData(size=1, image_size=(3, 256, 256))
        img, _ = ds[0]
        return np.array(img), "library_dataset:torchvision:FakeData"

    raise ValueError(
        f"library_dataset_backend không hỗ trợ: '{backend}' (skimage | torchvision)"
    )


def validate_and_convert(image: np.ndarray, source_label: str) -> np.ndarray:
    """Kiểm tra ảnh có khớp EXPECTED_SPEC (grayscale uint8 2D, như đầu ra
    `cv2.imread(path, 0)` mà các hàm trong repo viraj7 dùng) hay không.

    Tự convert khi có thể (RGB->gray, channel-first->channel-last, float->uint8),
    LUÔN log rõ mọi thay đổi. Raise lỗi rõ ràng khi không convert an toàn được,
    thay vì âm thầm trả về kết quả sai lệch.
    """
    img = np.asarray(image)
    original_shape, original_dtype = img.shape, img.dtype
    notes: list[str] = []

    if img.ndim == 3:
        if img.shape[-1] in (3, 4):
            rgb = img[..., :3].astype(np.float64)
            img = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
            has_alpha = image.shape[-1] == 4
            notes.append(f"RGB{'A' if has_alpha else ''} (H,W,C) -> grayscale (luminosity)")
        elif img.shape[0] in (1, 3, 4):
            # channel-first (C,H,W) kiểu torchvision -> (H,W,C) rồi grayscale
            hwc = np.moveaxis(img, 0, -1)
            if hwc.shape[-1] == 1:
                img = hwc[..., 0]
            else:
                rgb = hwc[..., :3].astype(np.float64)
                img = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
            notes.append("channel-first (C,H,W) -> grayscale (H,W)")
        else:
            raise ValueError(
                f"Ảnh từ '{source_label}' có shape {original_shape}, không xác định "
                "được layout (không khớp (H,W,3/4) hay (3/4,H,W)). TODO: xử lý layout "
                "này tường minh trong data/loader.py::validate_and_convert."
            )
    elif img.ndim != 2:
        raise ValueError(
            f"Ảnh từ '{source_label}' có ndim={img.ndim} (shape={original_shape}), "
            "không phải ảnh 2D/3D thông thường -- không tự convert an toàn được."
        )

    if img.dtype != np.uint8:
        if np.issubdtype(img.dtype, np.floating):
            lo, hi = float(np.min(img)), float(np.max(img))
            if lo >= 0.0 and hi <= 1.0:
                img = np.clip(img * 255.0, 0, 255).astype(np.uint8)
                notes.append("float[0,1] -> uint8[0,255] (nhân 255)")
            else:
                img = np.clip(img, 0, 255).astype(np.uint8)
                notes.append(
                    f"float[{lo:.3f},{hi:.3f}] -> uint8 (clip, KHÔNG rescale -- "
                    "kiểm tra lại nếu thang giá trị gốc không phải 0-255)"
                )
        else:
            img = np.clip(img, 0, 255).astype(np.uint8)
            notes.append(f"{original_dtype} -> uint8 (clip)")

    if notes:
        logger.warning(
            "Ảnh từ '%s' KHÔNG khớp EXPECTED_SPEC ban đầu (shape=%s, dtype=%s) "
            "-> đã convert: %s. Kết quả cuối: shape=%s, dtype=%s.",
            source_label, original_shape, original_dtype, "; ".join(notes),
            img.shape, img.dtype,
        )
    else:
        logger.info(
            "Ảnh từ '%s' đã khớp EXPECTED_SPEC (shape=%s, dtype=%s), không cần convert.",
            source_label, img.shape, img.dtype,
        )

    return np.ascontiguousarray(img)


def load_image(cfg: dict[str, Any] | None = None) -> np.ndarray:
    """Entry point chính: đọc config nếu chưa truyền vào, nạp ảnh theo
    `data.source`, validate/convert rồi trả về mảng numpy sẵn sàng dùng cho
    cả 3 phiên bản pipeline (python_pure / rust_pure / hybrid_pyo3)."""
    if cfg is None:
        cfg = load_config()

    source = cfg["data"]["source"]
    if source == "repo_sample":
        img, label = _load_repo_sample(cfg)
    elif source == "library_dataset":
        img, label = _load_library_dataset(cfg)
    else:
        raise ValueError(
            f"data.source không hỗ trợ: '{source}' (repo_sample | library_dataset)"
        )

    return validate_and_convert(img, label)


if __name__ == "__main__":
    image = load_image()
    print(
        f"Loaded image: shape={image.shape}, dtype={image.dtype}, "
        f"min={int(image.min())}, max={int(image.max())}"
    )
