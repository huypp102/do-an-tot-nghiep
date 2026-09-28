"""Repo GIẢ -- mỗi hàm nhắm vào ĐÚNG MỘT lý do của `outcomes.HOTSPOT_REASONS`.

    sum_squares        -> ghi được, Tầng 1 (list[float])        => MEASURED
    scale_point        -> ghi được, Tầng 2 (đối tượng Point)    => MEASURED
    accumulate_inplace -> sửa đối số TẠI CHỖ, trả về None       => MEASURED
                          (kiểm tra việc chụp trạng thái đối số SAU lời gọi)
    count_rows         -> nhận sqlite3.Connection               => UNREPLAYABLE_ARGS
    unused_helper      -> không test nào gọi                    => NOT_COVERED_BY_TESTS
    jittered_mean      -> dùng random, không tất định           => NONDETERMINISTIC
    walk_values        -> generator                             => UNSUPPORTED_KIND

Cố ý KHÔNG dùng numpy: repo trong RepoTransBench phần lớn là code stdlib, và
việc này cũng kiểm tra được rằng pipeline không âm thầm giả định có numpy.
"""
from __future__ import annotations

import random
import sqlite3

from mypkg.models import Point


def sum_squares(values):
    """Tầng 1: chỉ nhận list số -> Rust thuần được."""
    total = 0.0
    for v in values:
        total += float(v) * float(v)
    return total


def scale_point(point, factor):
    """Tầng 2: nhận đối tượng, mọi thuộc tính là kiểu gốc."""
    return Point(point.x * factor, point.y * factor, point.label)


def accumulate_inplace(buffer, addend):
    """Sửa đối số TẠI CHỖ và trả về None.

    Hàm kiểu này là lý do plugin phải chụp trạng thái đối số SAU lời gọi: nếu
    chỉ so giá trị trả về thì mọi bản dịch (kể cả bản không làm gì) đều "khớp"
    vì đều trả None.
    """
    for i in range(len(buffer)):
        buffer[i] += addend
    return None


def count_rows(conn, table):
    """Nhận `sqlite3.Connection` -- không pickle được, nên không phát lại được."""
    cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def unused_helper(values):
    """Không test nào gọi -> không có đối số thật để ghi."""
    return [v * 2 for v in values]


def jittered_mean(values):
    """Không tất định: mỗi lần gọi cho một kết quả khác."""
    if not values:
        return 0.0
    base = sum(float(v) for v in values) / len(values)
    return base + random.random()


def walk_values(values):
    """Generator -- kết quả sinh dần, không ghi/phát lại được như một lời gọi."""
    for v in values:
        yield float(v) * 3.0
