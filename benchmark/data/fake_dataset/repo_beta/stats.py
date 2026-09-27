"""Repo GIẢ thứ hai -- xem chú thích trong repo_alpha/image_ops.py."""
from __future__ import annotations

import math


def mean(values):
    """Vỏ mỏng bọc hàm dựng sẵn -> Gate nên gán 'skip' hoặc gần vậy."""
    return sum(values) / len(values)


def histogram(values, bins=16):
    """Vòng lặp lồng + rẽ nhánh -> ứng viên dịch sang Rust."""
    counts = [0] * bins
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    for v in values:
        for b in range(bins):
            lower = lo + span * b / bins
            upper = lo + span * (b + 1) / bins
            if lower <= v < upper:
                counts[b] += 1
                break
    return counts


def entropy(values):
    """Gọi histogram -> tạo cạnh trong Program Call Graph."""
    counts = histogram(values)
    total = sum(counts) or 1
    result = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            result -= p * math.log(p)
    return result
