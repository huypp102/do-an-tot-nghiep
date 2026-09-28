"""Repo GIẢ cho lý do BASELINE_FAILED.

Bản Python NGUYÊN BẢN đã sai sẵn (chưa ai đụng tới Rust). Pipeline phải phát
hiện điều đó, gán `BASELINE_FAILED` cho repo và LOẠI nó khỏi so sánh -- không
được tính lỗi có sẵn này cho bản hybrid.
"""
from __future__ import annotations


def add(a, b):
    return a - b  # SAI có chủ ý: bộ test dưới đây sẽ fail
