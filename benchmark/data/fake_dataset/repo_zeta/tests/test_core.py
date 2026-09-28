"""Bộ test dựng CỐ Ý để sinh ra một hotspot VACUOUS.

`test_tally_*` bị bỏ qua khi biến `RTB_SWAP_TARGETS` có mặt -- biến đó chỉ được
đặt ở lượt chạy HYBRID. Kết quả:
  * lượt GHI đối số: `tally` được gọi -> phát lại được, đi hết tới bước thay
    bằng Rust;
  * lượt HYBRID: không test nào gọi `tally` -> `rust_call_count == 0` dù bộ
    test vẫn xanh -> pipeline PHẢI gắn VACUOUS và loại nó khỏi tỉ lệ
    regression-free.

`always_used` chạy ở cả hai lượt, làm đối chứng.
"""
import os

import pytest
from zpkg.core import always_used, tally

_SWAP_RUN = bool(os.environ.get("RTB_SWAP_TARGETS"))
skip_on_swap = pytest.mark.skipif(
    _SWAP_RUN,
    reason="mô phỏng code path KHÔNG được đi qua ở lượt hybrid (kiểm chứng VACUOUS)",
)


@skip_on_swap
def test_tally_basic():
    assert tally([1, 2, 3]) == pytest.approx(6.0)


@skip_on_swap
def test_tally_empty():
    assert tally([]) == pytest.approx(0.0)


def test_always_used():
    assert always_used([1.0, 5.0, 2.0]) == pytest.approx(5.0)


def test_always_used_empty():
    assert always_used([]) == pytest.approx(0.0)
