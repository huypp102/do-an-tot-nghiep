"""PHA B -- Bộ so sánh SÂU dùng chung cho mọi phép kiểm đúng đắn.

Module này được import từ HAI phía chạy trong HAI môi trường khác nhau:
  * tiến trình cha (venv của benchmark) -- khi phát lại và so kết quả;
  * `capture_plugin.py` chạy BÊN TRONG venv riêng của từng repo.

Vì vậy nó chỉ được phụ thuộc stdlib, còn `numpy` là TUỲ CHỌN (repo trong
dataset có thể không cài numpy). Đừng thêm import nặng vào đây.

Thứ tự kiểm tra có chủ ý, từ hẹp tới rộng:
    ndarray -> số thực -> bytes/str -> Mapping -> Sequence -> set
    -> đối tượng có __dict__ -> ==
Nếu để `==` lên trước thì `np.allclose` không bao giờ được dùng, và so 2
ndarray bằng `==` trả về MẢNG bool chứ không phải bool -> `if` sẽ raise.
"""
from __future__ import annotations

import math
from typing import Any

DEFAULT_RTOL = 1e-5
DEFAULT_ATOL = 1e-8
DEFAULT_MAX_DEPTH = 6

try:  # numpy là tuỳ chọn -- repo trong dataset có thể không dùng tới.
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None


def _is_ndarray(value: Any) -> bool:
    return _np is not None and isinstance(value, _np.ndarray)


def deep_compare(
    left: Any,
    right: Any,
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
    max_depth: int = DEFAULT_MAX_DEPTH,
    _depth: int = 0,
    _path: str = "",
) -> tuple[bool, str]:
    """So sánh 2 giá trị. Trả về (khớp?, đường dẫn + mô tả chỗ lệch).

    `max_depth` chặn đệ quy vô hạn trên cấu trúc tự tham chiếu (đối tượng có
    thuộc tính trỏ về chính nó). Chạm giới hạn thì lùi về so `==` thay vì
    raise RecursionError.
    """
    where = _path or "<root>"

    if _depth > max_depth:
        try:
            return (left == right), "" if left == right else f"{where}: lệch (đã chạm giới hạn độ sâu)"
        except Exception:  # noqa: BLE001
            return False, f"{where}: không so sánh được ở giới hạn độ sâu"

    # --- ndarray ---
    if _is_ndarray(left) or _is_ndarray(right):
        try:
            a, b = _np.asarray(left), _np.asarray(right)
        except Exception as exc:  # noqa: BLE001
            return False, f"{where}: không chuyển được về ndarray: {exc}"
        if a.shape != b.shape:
            return False, f"{where}: lệch shape {a.shape} vs {b.shape}"
        if a.dtype.kind in "biufc" and b.dtype.kind in "biufc":
            if _np.allclose(a, b, rtol=rtol, atol=atol, equal_nan=True):
                return True, ""
            bad = int((~_np.isclose(a, b, rtol=rtol, atol=atol, equal_nan=True)).sum())
            try:
                worst = float(_np.abs(a.astype(float) - b.astype(float)).max())
            except Exception:  # noqa: BLE001
                worst = float("nan")
            return False, f"{where}: lệch {bad}/{a.size} phần tử, sai số lớn nhất {worst:.6g}"
        if _np.array_equal(a, b):
            return True, ""
        return False, f"{where}: mảng không bằng nhau (dtype {a.dtype} vs {b.dtype})"

    # --- bool phải xét TRƯỚC số: bool là con của int trong Python ---
    if isinstance(left, bool) or isinstance(right, bool):
        if left is right or left == right:
            return True, ""
        return False, f"{where}: {left!r} vs {right!r}"

    # --- số thực/nguyên: dùng ngưỡng, không dùng == ---
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        lf, rf = float(left), float(right)
        if math.isnan(lf) and math.isnan(rf):
            return True, ""
        if math.isclose(lf, rf, rel_tol=rtol, abs_tol=atol):
            return True, ""
        return False, f"{where}: lệch giá trị {left!r} vs {right!r}"

    if isinstance(left, complex) or isinstance(right, complex):
        try:
            if math.isclose(abs(complex(left) - complex(right)), 0.0, abs_tol=atol):
                return True, ""
        except Exception:  # noqa: BLE001
            pass
        return False, f"{where}: lệch số phức {left!r} vs {right!r}"

    # --- bytes/str: so nguyên vẹn, và phải xét TRƯỚC Sequence ---
    if isinstance(left, (str, bytes, bytearray)) or isinstance(right, (str, bytes, bytearray)):
        if type(left) is not type(right):
            return False, f"{where}: lệch kiểu {type(left).__name__} vs {type(right).__name__}"
        if left == right:
            return True, ""
        return False, f"{where}: lệch nội dung chuỗi (dài {len(left)} vs {len(right)})"

    # --- dict / Mapping: so theo KHOÁ, không theo thứ tự ---
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            only_l = sorted(map(repr, set(left) - set(right)))[:5]
            only_r = sorted(map(repr, set(right) - set(left)))[:5]
            return False, f"{where}: lệch tập khoá (chỉ bên trái: {only_l}, chỉ bên phải: {only_r})"
        for key in left:
            ok, why = deep_compare(
                left[key], right[key], rtol, atol, max_depth, _depth + 1, f"{where}[{key!r}]"
            )
            if not ok:
                return False, why
        return True, ""

    # --- set/frozenset ---
    if isinstance(left, (set, frozenset)) and isinstance(right, (set, frozenset)):
        if left == right:
            return True, ""
        return False, f"{where}: lệch tập hợp (|L|={len(left)}, |R|={len(right)})"

    # --- list/tuple ---
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return False, f"{where}: lệch độ dài {len(left)} vs {len(right)}"
        for i, (x, y) in enumerate(zip(left, right)):
            ok, why = deep_compare(x, y, rtol, atol, max_depth, _depth + 1, f"{where}[{i}]")
            if not ok:
                return False, why
        return True, ""

    # --- đối tượng tuỳ ý: so __dict__ (có giới hạn độ sâu) ---
    # Chỉ làm khi CẢ HAI cùng lớp: khác lớp mà __dict__ trùng nhau vẫn là lệch.
    if hasattr(left, "__dict__") and hasattr(right, "__dict__"):
        if type(left) is not type(right):
            return False, f"{where}: lệch lớp {type(left).__name__} vs {type(right).__name__}"
        return deep_compare(
            vars(left), vars(right), rtol, atol, max_depth, _depth + 1, f"{where}.__dict__"
        )

    # --- còn lại ---
    try:
        if left == right:
            return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{where}: so sánh ném lỗi: {type(exc).__name__}: {exc}"
    return False, f"{where}: khác nhau ({type(left).__name__} vs {type(right).__name__})"


def describe_type(value: Any, _depth: int = 0, max_depth: int = 3) -> str:
    """Mô tả KIỂU QUAN SÁT ĐƯỢC của một giá trị, dạng người và LLM đều đọc được.

    Đây là đầu vào cho prompt của Generator Agent ở Pha D: nó cần biết hàm
    thật nhận `list[float]` hay `ndarray[float64, (256,)]` hay `đối tượng của
    lớp Foo` mới viết được chữ ký PyO3 đúng. Chỉ mô tả cái ĐO ĐƯỢC từ đối số
    thật, không suy đoán từ type-hint (type-hint hay thiếu hoặc sai).

    Ví dụ output:
        "int", "float", "str(len=12)", "list[float](n=256)",
        "ndarray[float64, (256,)]", "dict[str -> int](n=3)",
        "Foo(thuộc tính: a: int, b: list[float])"
    """
    if value is None:
        return "None"
    if _is_ndarray(value):
        return f"ndarray[{value.dtype}, {tuple(value.shape)}]"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, complex):
        return "complex"
    if isinstance(value, str):
        return f"str(len={len(value)})"
    if isinstance(value, (bytes, bytearray)):
        return f"{type(value).__name__}(len={len(value)})"

    if isinstance(value, dict):
        if not value:
            return "dict(rỗng)"
        if _depth >= max_depth:
            return f"dict(n={len(value)})"
        k = describe_type(next(iter(value.keys())), _depth + 1, max_depth)
        v = describe_type(next(iter(value.values())), _depth + 1, max_depth)
        return f"dict[{k} -> {v}](n={len(value)})"

    if isinstance(value, (list, tuple, set, frozenset)):
        kind = type(value).__name__
        if not value:
            return f"{kind}(rỗng)"
        if _depth >= max_depth:
            return f"{kind}(n={len(value)})"
        inner = {describe_type(x, _depth + 1, max_depth) for x in list(value)[:8]}
        inner_txt = next(iter(inner)) if len(inner) == 1 else "|".join(sorted(inner))
        return f"{kind}[{inner_txt}](n={len(value)})"

    if hasattr(value, "__dict__") and _depth < max_depth:
        attrs = vars(value)
        if not attrs:
            return f"{type(value).__name__}(không có thuộc tính)"
        shown = list(attrs.items())[:8]
        parts = [f"{k}: {describe_type(v, _depth + 1, max_depth)}" for k, v in shown]
        more = "" if len(attrs) <= 8 else f", ... (+{len(attrs) - 8})"
        return f"{type(value).__name__}(thuộc tính: {', '.join(parts)}{more})"

    return type(value).__name__


# ---------------------------------------------------------------------------
# PHÂN TẦNG KIỂU -- quyết định bằng CODE, không hỏi LLM (Pha D).
# ---------------------------------------------------------------------------
TIER_NATIVE = "TIER1_NATIVE"
"""Kiểu gốc: số, str, bytes, list/tuple/dict của kiểu gốc, ndarray.
-> Rust thuần, chữ ký PyO3 có kiểu rõ ràng."""

TIER_KERNEL = "TIER2_KERNEL"
"""Đối tượng tuỳ ý, nhưng các thuộc tính (phần vòng lặp nóng chạm tới) là kiểu
gốc -> "kernel extraction": Rust nhận các trường kiểu gốc, một shim Python
mỏng tháo đối tượng ra và đóng gói kết quả lại."""

TIER_UNSUPPORTED = "TIER_UNSUPPORTED"
"""Ngoài 2 tầng trên -> UNSUPPORTED_KIND."""

_NATIVE_SCALARS = (bool, int, float, str, bytes, bytearray, type(None))


def _is_native(value: Any, _depth: int = 0) -> bool:
    if isinstance(value, _NATIVE_SCALARS):
        return True
    if _is_ndarray(value):
        return value.dtype.kind in "biufc"
    if _depth >= 4:
        return False
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_is_native(x, _depth + 1) for x in list(value)[:64])
    if isinstance(value, dict):
        return all(
            _is_native(k, _depth + 1) and _is_native(v, _depth + 1)
            for k, v in list(value.items())[:64]
        )
    return False


def classify_tier(values: list[Any]) -> tuple[str, str]:
    """Phân tầng một BỘ giá trị (thường là mọi đối số của 1 hotspot).

    Trả về (tầng, lý do). Tầng được quyết định bởi giá trị "khó" nhất: một đối
    số ngoài tầng là đủ để hạ cả hotspot xuống tầng thấp hơn, vì chữ ký Rust
    phải xử lý được TẤT CẢ đối số.
    """
    if all(_is_native(v) for v in values):
        return TIER_NATIVE, "mọi đối số là kiểu gốc (số/str/bytes/list/dict/ndarray)"

    offenders: list[str] = []
    for v in values:
        if _is_native(v):
            continue
        if hasattr(v, "__dict__") and vars(v) and all(_is_native(x) for x in vars(v).values()):
            continue  # đối tượng mà mọi thuộc tính là kiểu gốc -> Tầng 2 chấp nhận
        offenders.append(type(v).__name__)

    if not offenders:
        return TIER_KERNEL, (
            "có đối tượng tuỳ ý nhưng mọi thuộc tính là kiểu gốc -> tách kernel "
            "Rust + shim Python tháo/đóng gói đối tượng"
        )
    return TIER_UNSUPPORTED, (
        "có đối số không thuộc tầng nào: " + ", ".join(sorted(set(offenders))[:5])
    )
