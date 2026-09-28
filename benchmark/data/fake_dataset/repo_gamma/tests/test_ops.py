"""Bộ test của repo giả. Cố ý KHÔNG gọi `unused_helper` -- đó là cách duy nhất
để kiểm chứng lý do NOT_COVERED_BY_TESTS.

Dùng `from mypkg.ops import ...` ở một chỗ và `import mypkg.ops` ở chỗ khác,
để kiểm tra `_patch_everywhere` bắt được CẢ HAI dạng tham chiếu.
"""
import sqlite3

import mypkg.ops
import pytest
from mypkg.models import Point
from mypkg.ops import accumulate_inplace, sum_squares


def test_sum_squares_basic():
    assert sum_squares([1, 2, 3]) == pytest.approx(14.0)


def test_sum_squares_empty():
    assert sum_squares([]) == pytest.approx(0.0)


def test_sum_squares_via_module_ref():
    # Gọi qua tham chiếu module, khác với `from ... import` ở trên.
    assert mypkg.ops.sum_squares([2.5, 0.5]) == pytest.approx(6.5)


def test_scale_point():
    got = mypkg.ops.scale_point(Point(1.0, 2.0, "a"), 3.0)
    assert got == Point(3.0, 6.0, "a")


def test_accumulate_inplace_mutates():
    buf = [1.0, 2.0, 3.0]
    assert accumulate_inplace(buf, 10.0) is None
    assert buf == [11.0, 12.0, 13.0]


def test_count_rows_with_sqlite():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    assert mypkg.ops.count_rows(conn, "t") == 3
    conn.close()


def test_jittered_mean_in_range():
    got = mypkg.ops.jittered_mean([2.0, 4.0])
    assert 3.0 <= got < 4.0


def test_walk_values():
    assert list(mypkg.ops.walk_values([1, 2])) == [3.0, 6.0]
