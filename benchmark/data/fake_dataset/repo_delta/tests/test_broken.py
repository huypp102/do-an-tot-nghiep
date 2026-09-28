"""Mọi test ở đây PHẢI fail trên bản Python nguyên bản -> repo ra BASELINE_FAILED."""
import broken


def test_add_two_three():
    assert broken.add(2, 3) == 5


def test_add_zero():
    assert broken.add(1, 0) == 1
    assert broken.add(0, 5) == 5
