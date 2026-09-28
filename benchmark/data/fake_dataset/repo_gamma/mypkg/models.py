"""Lớp tự định nghĩa -- để kiểm thử Tầng 2 (kernel extraction) của Pha D.

Mọi thuộc tính là kiểu gốc, nên `deep_compare.classify_tier` phải xếp các hàm
nhận `Point` vào `TIER2_KERNEL`: Rust nhận các trường kiểu gốc, một shim
Python mỏng tháo đối tượng ra rồi đóng gói kết quả lại.
"""
from __future__ import annotations


class Point:
    def __init__(self, x: float, y: float, label: str = "p"):
        self.x = float(x)
        self.y = float(y)
        self.label = str(label)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Point):
            return NotImplemented
        return (self.x, self.y, self.label) == (other.x, other.y, other.label)

    def __repr__(self) -> str:
        return f"Point({self.x}, {self.y}, {self.label!r})"
