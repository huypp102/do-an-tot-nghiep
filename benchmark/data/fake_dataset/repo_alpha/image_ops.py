"""Repo GIẢ dùng để kiểm thử nhánh dataset khi chưa có RepoTransBench thật.

Mục đích: xác nhận toàn bộ chain (resolve_dataset_repos -> stage0_graph ->
stage2 gate -> stage6 benchmark) chạy đúng trên nhiều repo, mà KHÔNG cần
dataset thật đã tải về. Xoá thư mục data/fake_dataset/ bất cứ lúc nào cũng
không ảnh hưởng gì tới code.

Cố ý viết 2 kiểu hàm khác nhau để Decision Gate phân loại ra nhãn khác nhau.
"""
from __future__ import annotations


def scale_pixels(pixels, factor):
    """Vòng lặp lồng + index mảng + rẽ nhánh -> Gate nên gán 'candidate'."""
    out = []
    for row in range(len(pixels)):
        new_row = []
        for col in range(len(pixels[row])):
            value = pixels[row][col] * factor
            if value > 255:
                new_row.append(255)
            elif value < 0:
                new_row.append(0)
            else:
                new_row.append(value)
        out.append(new_row)
    return out


def normalize(values):
    """Biến đổi elementwise -> Gate nên gợi ý numpy vectorization."""
    total = sum(values)
    return [v / total for v in values]


def run(pixels):
    """Hàm gọi 2 hàm trên -> tạo cạnh thật trong Program Call Graph."""
    scaled = scale_pixels(pixels, 2)
    return normalize(scaled[0])
