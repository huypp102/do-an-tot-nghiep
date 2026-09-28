from __future__ import annotations


def tally(values):
    """Tầng 1 (list số -> float). Hotspot của repo giả này."""
    total = 0.0
    for v in values:
        total += float(v)
    return total


def always_used(values):
    """Được gọi ở CẢ HAI lượt test -> KHÔNG vacuous. Dùng làm đối chứng, để
    chứng minh nhãn VACUOUS chỉ bám vào hotspot thật sự không được gọi."""
    return max([float(v) for v in values]) if values else 0.0
