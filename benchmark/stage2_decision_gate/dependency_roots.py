"""PHA 4.3 -- Phụ thuộc ở CẤP HÀM, không phải cấp file.

LỖI ĐANG SỬA: một dòng `import cv2` ở đầu file làm **mọi** hàm trong file đó bị
đánh dấu `blocked`, kể cả hàm chỉ cộng hai số và không hề chạm tới cv2. File
Python thật thường import chục thứ cho cả module; xét ở cấp file thì hầu như
mọi hàm đều "blocked", và Decision Gate mất tác dụng.

CÁCH LÀM: lấy source của CHÍNH hàm đó, duyệt AST tìm mọi `Name.id` và
`Attribute` gốc xuất hiện trong thân hàm, rồi chỉ tính import nào có TÊN KHỚP
với một trong các name đó là phụ thuộc thật của hàm này.

Ví dụ, file có `import cv2` và `import numpy as np`:

    def add(a, b):            -> roots = {}            (không dùng gì)
    def blur(img):
        return cv2.blur(img)  -> roots = {"cv2"}       (blocked nếu cv2 blocked)
    def norm(x):
        return np.sum(x)      -> roots = {"numpy"}     (numpy KHÔNG blocked)

GIỚI HẠN ĐÃ BIẾT, ghi ra để không ai tưởng là bug: phân tích tĩnh theo tên nên
không bắt được `getattr(cv2, "blur")` hay import trong thân hàm gán vào biến
khác. Bỏ sót theo chiều AN TOÀN cho tốc độ (đánh dấu ít blocked hơn thực tế),
nhưng bù lại không chặn oan -- và chặn oan là lỗi tệ hơn ở đây, vì nó làm mất
hẳn hotspot khỏi thực nghiệm.
"""
from __future__ import annotations

import ast
import logging
import textwrap

logger = logging.getLogger("benchmark.stage2_decision_gate.dependency_roots")

# Package mà dịch sang Rust là vô nghĩa hoặc bất khả thi trong phạm vi đồ án:
# hàm chỉ là lớp vỏ mỏng gọi xuống thư viện đã tối ưu bằng C/C++, hoặc phụ
# thuộc I/O-mạng/GUI mà Rust thuần không thay thế được.
BLOCKED_ROOTS: dict[str, str] = {
    "cv2": "OpenCV đã là C++ tối ưu -- dịch lớp vỏ Python sang Rust không nhanh hơn",
    "torch": "PyTorch: kernel đã là C++/CUDA",
    "tensorflow": "TensorFlow: kernel đã là C++/CUDA",
    "scipy": "SciPy: phần nóng đã là C/Fortran (BLAS/LAPACK)",
    "sklearn": "scikit-learn: phần nóng đã là Cython/C",
    "pandas": "pandas: phần nóng đã là Cython/C",
    "PIL": "Pillow: codec đã là C",
    "matplotlib": "vẽ hình, không phải tính toán nóng",
    "requests": "I/O mạng -- nút cổ chai là mạng, không phải CPU",
    "urllib": "I/O mạng",
    "httpx": "I/O mạng",
    "aiohttp": "I/O mạng bất đồng bộ",
    "socket": "I/O mạng cấp thấp",
    "sqlite3": "I/O đĩa + engine C",
    "psycopg2": "I/O mạng + driver C",
    "tkinter": "GUI",
    "flask": "web framework -- nút cổ chai là I/O",
    "django": "web framework -- nút cổ chai là I/O",
    "boto3": "SDK gọi API qua mạng",
    "subprocess": "gọi tiến trình ngoài, không phải CPU trong hàm",
}

# `numpy` CỐ Ý không nằm trong danh sách: hàm dùng numpy thường là ứng viên tốt
# cho gợi ý vectorization, và nhiều hàm chỉ dùng numpy làm kiểu dữ liệu chứ
# phần nóng vẫn là vòng lặp Python.


def _root_of(dotted: str) -> str:
    return (dotted or "").split(".")[0]


def file_import_map(imports_raw: list[str]) -> dict[str, str]:
    """{tên dùng trong code: package gốc} từ các câu import của file.

    Xử lý cả `import numpy as np` (np -> numpy) và
    `from os.path import join` (join -> os).
    """
    mapping: dict[str, str] = {}
    for raw in imports_raw or []:
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or _root_of(alias.name)
                    mapping[local] = _root_of(alias.name)
            elif isinstance(node, ast.ImportFrom):
                base = _root_of(node.module or "")
                for alias in node.names:
                    local = alias.asname or alias.name
                    mapping[local] = base or local
    return mapping


def names_used_in_function(source: str) -> set[str]:
    """Mọi tên ĐƯỢC ĐỌC trong thân hàm.

    Gồm `Name` (biến/hàm) và gốc của `Attribute` (`cv2.blur` -> `cv2`). Cũng
    tính import viết TRONG thân hàm -- chúng là phụ thuộc thật của chính hàm.
    """
    try:
        tree = ast.parse(textwrap.dedent(source or ""))
    except (SyntaxError, ValueError, RecursionError):
        # Hàm không parse được (Python 2, source bị cắt): trả rỗng thay vì
        # đoán. Rỗng nghĩa là "không thấy phụ thuộc nào", tức KHÔNG chặn --
        # đúng hướng an toàn đã nêu ở docstring đầu file.
        return set()

    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            cur = node
            while isinstance(cur, ast.Attribute):
                cur = cur.value
            if isinstance(cur, ast.Name):
                used.add(cur.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                used.add(alias.asname or _root_of(alias.name))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                used.add(alias.asname or alias.name)
    return used


def function_dependency_roots(source: str, imports_raw: list[str]) -> set[str]:
    """Package gốc mà CHÍNH hàm này thật sự dùng.

    Giao giữa "tên xuất hiện trong thân hàm" và "tên do import của file mang
    vào". Nhờ phép giao này, `import cv2` ở đầu file không còn ảnh hưởng tới
    hàm không dùng cv2.
    """
    mapping = file_import_map(imports_raw)
    used = names_used_in_function(source)
    return {mapping[name] for name in used if name in mapping}


def blocked_reasons(roots: set[str]) -> dict[str, str]:
    """{package bị chặn: lý do} trong số các root của hàm."""
    return {r: BLOCKED_ROOTS[r] for r in sorted(roots) if r in BLOCKED_ROOTS}


def analyze(source: str, imports_raw: list[str]) -> dict:
    """Kết quả đầy đủ cho một hàm, dạng ghi được vào JSON."""
    roots = function_dependency_roots(source, imports_raw)
    blocked = blocked_reasons(roots)
    return {
        "dependency_roots": sorted(roots),
        "blocked_roots": sorted(blocked),
        "blocked_reasons": blocked,
        "is_blocked": bool(blocked),
        # Ghi cả số import của FILE để thấy rõ phép giao đã lọc bớt bao nhiêu --
        # đây chính là bằng chứng lỗi "chặn oan" đã hết.
        "n_file_imports": len(file_import_map(imports_raw)),
    }
