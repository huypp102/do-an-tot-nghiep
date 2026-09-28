"""Hàm Tầng 1 trên str/bytes -- kiểm tra chữ ký không phải số vẫn đi hết
đường ống (ghi đối số, phát lại, phân tầng, sinh Rust)."""
from __future__ import annotations


def checksum(text):
    """Tổng kiểm đơn giản trên chuỗi -- Tầng 1 (str -> int)."""
    total = 0
    for i, ch in enumerate(text):
        total = (total + (i + 1) * ord(ch)) % 1000003
    return total


def count_vowels(text):
    """Tầng 1 (str -> int)."""
    return sum(1 for ch in text.lower() if ch in "aeiou")
