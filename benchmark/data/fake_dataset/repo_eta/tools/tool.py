"""Repo GIẢ mô phỏng đúng hình dạng của `BBuf_onnx_learn` trong RepoTransBench:
hàm TEST nằm CÙNG FILE với code sản phẩm.

Trên lượt chạy thật, repo đó có 14/23 (60%) ứng viên hotspot là hàm test. Đây
là fixture để chứng minh bộ lọc bắt được cả trường hợp khó nhất -- hàm test
không nằm trong `tests/`, nên lọc theo đường dẫn là không đủ.

Kỳ vọng: `candidate_pool` chỉ chứa `add`, `sub`, `mul`, `div`, `sum_list`;
KHÔNG chứa bất kỳ tên nào bắt đầu bằng `test_`, cũng không chứa `setUp`.
"""
from __future__ import annotations

import unittest


# ----------------------------------------------------- code SẢN PHẨM (giữ lại)
def add(a, b):
    return float(a) + float(b)


def sub(a, b):
    return float(a) - float(b)


def mul(a, b):
    return float(a) * float(b)


def div(a, b):
    if b == 0:
        raise ZeroDivisionError("chia cho 0")
    return float(a) / float(b)


def sum_list(values):
    total = 0.0
    for v in values:
        total += float(v)
    return total


# ------------------------------------------- hàm TEST lẫn trong file sản phẩm
# Đây chính là kiểu gây nhiễu candidate_pool ở repo thật: chúng nằm cùng file
# với code sản phẩm nên lọc theo đường dẫn `tests/` không bắt được.
def test_add():
    assert add(2, 3) == 5.0


def test_sub():
    assert sub(5, 3) == 2.0


def test_mul():
    assert mul(2, 3) == 6.0


def test_div():
    assert div(6, 3) == 2.0


def test_div_zero():
    try:
        div(1, 0)
    except ZeroDivisionError:
        return
    raise AssertionError("phải ném ZeroDivisionError")


def test_sum_list():
    assert sum_list([1, 2, 3]) == 6.0


class ToolCase(unittest.TestCase):
    def setUp(self):
        self.values = [1.0, 2.0, 3.0]

    def tearDown(self):
        self.values = None

    def test_sum(self):
        self.assertEqual(sum_list(self.values), 6.0)
