"""Bộ test thật của repo_eta (trong `tests/`, bị lọc theo đường dẫn).

Gọi cả 5 hàm sản phẩm để chúng ghi/phát lại được đối số -- nhờ vậy fixture này
dùng được cho cả kiểm chứng `ALL_HOTSPOTS_FAILED_COMPILE`.
"""
from tools.tool import add, div, mul, sub, sum_list


def test_add_basic():
    assert add(2, 3) == 5.0


def test_sub_basic():
    assert sub(5, 3) == 2.0


def test_mul_basic():
    assert mul(4, 2.5) == 10.0


def test_div_basic():
    assert div(9, 3) == 3.0


def test_sum_list_basic():
    assert sum_list([1, 2, 3, 4]) == 10.0
