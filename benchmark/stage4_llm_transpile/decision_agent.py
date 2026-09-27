"""Stage 4/6 -- Decision Agent: accept/reject bản Rust SAU khi đã benchmark.

=== PHÂN BIỆT VỚI DECISION GATE (Stage 2) ===
`stage2_decision_gate/gate.py` chạy TRƯỚC khi dịch, quyết định "hàm này có
đáng dịch không" dựa trên đặc điểm tĩnh của code (skip / vectorize /
candidate). Module này chạy SAU khi đã dịch VÀ đã đo tốc độ, quyết định "bản
Rust vừa sinh ra có đáng giữ không". Tên hàm/kiểu dữ liệu ở 2 nơi cố ý khác
hẳn nhau (`classify_functions`/`GateLabel` vs `decide_after_benchmark`/
`AgentDecision`) để không ai nhầm 2 khái niệm.

Phỏng theo POLO (Bai et al., IJCAI-25) Fig.5 "Decision Agent Prompt Template":
tracking thay đổi hiệu năng trước/sau, rồi quyết định (1) accept hay reject,
(2) có tiếp tục tối ưu vòng nữa không, (3) nếu tiếp thì chiến lược kế tiếp.

HAI CHẾ ĐỘ theo `llm.num_agents` trong config.yaml:
  - num_agents = 1 (mặc định): KHÔNG gọi LLM. Dùng rule đơn giản, rẻ và
    xác định: accept nếu speedup (rust_pure so với python_pure) > 1.0, reject
    nếu không. Luôn dừng sau 1 vòng, không tự sinh chiến lược tiếp theo.
  - num_agents = 2: gọi LLM để ra quyết định như POLO mô tả.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("benchmark.stage4_llm_transpile.decision_agent")

ACCEPT_THRESHOLD = 1.0  # speedup > 1.0 => bản Rust nhanh hơn Python => accept

# VAI TRÒ của agent này -- KHÁC HẲN Generator Agent. Gửi kèm mọi lượt gọi,
# trong 1 AgentSession RIÊNG: Decision Agent KHÔNG thấy lịch sử hội thoại
# sinh code của Generator, và ngược lại (xem agent_session.py).
DECISION_SYSTEM_PROMPT = """Bạn là kỹ sư hiệu năng, chuyên ĐÁNH GIÁ kết quả tối ưu \
dựa trên số liệu đo được.

VAI TRÒ DUY NHẤT của bạn: quyết định CHẤP NHẬN hay LOẠI BỎ một bản dịch Rust, \
và có nên tối ưu thêm một vòng nữa không.
- Bạn KHÔNG viết code Rust.
- Bạn KHÔNG sửa lỗi biên dịch. Code đưa tới bạn đã biên dịch được rồi.
- Việc sinh và sửa code thuộc về một agent khác, độc lập với bạn.

Nguyên tắc đánh giá:
1. Căn cứ chính là SỐ LIỆU đo được, không phải cảm nhận về code đẹp/xấu.
2. Nhanh hơn baseline thì mới đáng chấp nhận.
3. Nếu thấy còn dư địa tối ưu rõ ràng, nêu chiến lược cụ thể cho vòng sau.
4. Trả lời đúng định dạng được yêu cầu, không thêm lời dẫn."""


DEFAULT_MAX_ROUNDS = 3  # trần cứng cho vòng tối ưu tốc độ (optimization_loop)


@dataclass
class AgentDecision:
    """Kết quả quyết định (cố ý KHÁC kiểu dữ liệu của Stage 2 Decision Gate)."""

    function_name: str
    accepted: bool
    continue_optimizing: bool = False
    next_strategy: str | None = None
    speedup: float | None = None
    source: str = "rule"  # "rule" (num_agents=1) | "llm" (num_agents=2)
    reason: str = ""
    round_index: int = 1
    stopped_by_cap: bool = False  # True khi bị ép dừng vì chạm trần max_rounds
    raw_response: str | None = field(default=None, repr=False)


PROMPT_TEMPLATE = """**Nhiệm vụ**: Bạn là lập trình viên cao cấp, cần ra quyết định tối ưu \
dựa trên thay đổi hiệu năng ĐO ĐƯỢC.

Hotspot: `{function_name}`

**Hiệu năng trước/sau khi dịch sang Rust**:
- python_pure (baseline): {before_ms}
- rust_pure  (sau khi dịch): {after_ms}
- speedup (baseline / sau): {speedup}

**Code Rust đang xét**:
```rust
{rust_code}
```

**Chiến lược đã áp dụng ở vòng trước**: {previous_strategy}

**Trả lời đúng định dạng sau**:
## Decision
ACCEPT hoặc REJECT

## Continue
CONTINUE hoặc STOP

## Next strategy
<nếu CONTINUE thì nêu chiến lược tối ưu tiếp theo; nếu STOP thì ghi "none">
"""


def _fmt_ms(value: float | None) -> str:
    return "không đo được" if value is None else f"{value * 1000:.3f} ms"


def compute_speedup(before_sec: float | None, after_sec: float | None) -> float | None:
    """speedup = thời gian baseline / thời gian sau khi dịch. >1 nghĩa là nhanh hơn."""
    if not before_sec or not after_sec or after_sec <= 0:
        return None
    return before_sec / after_sec


def _decide_by_rule(
    function_name: str, before_sec: float | None, after_sec: float | None
) -> AgentDecision:
    """num_agents=1: rule thuần, không tốn 1 token LLM nào."""
    speedup = compute_speedup(before_sec, after_sec)
    if speedup is None:
        return AgentDecision(
            function_name=function_name, accepted=False, continue_optimizing=False,
            speedup=None, source="rule",
            reason=("thiếu số liệu benchmark của python_pure hoặc rust_pure "
                    "(vd extension Rust chưa build) -> không đủ căn cứ để accept"),
        )
    accepted = speedup > ACCEPT_THRESHOLD
    return AgentDecision(
        function_name=function_name, accepted=accepted, continue_optimizing=False,
        speedup=speedup, source="rule",
        reason=(f"speedup={speedup:.2f}x {'>' if accepted else '<='} "
                f"{ACCEPT_THRESHOLD} -> {'accept' if accepted else 'reject'} "
                "(num_agents=1: luôn dừng sau 1 vòng)"),
    )


def _apply_round_cap(
    decision: AgentDecision, round_index: int, max_rounds: int
) -> AgentDecision:
    """TRẦN CỨNG cho vòng tối ưu tốc độ.

    Decision Agent được quyền dừng SỚM hơn (accept/reject xong là thôi, đúng
    thiết kế gốc POLO). Nhưng khi đã chạy tới vòng `max_rounds` thì ép dừng,
    BẤT KỂ LLM có đề xuất "CONTINUE" hay không -- tránh vòng lặp chạy mãi và
    đốt token/thời gian không kiểm soát.
    """
    if decision.continue_optimizing and round_index >= max_rounds:
        decision.continue_optimizing = False
        decision.stopped_by_cap = True
        decision.reason += (
            f" | Đã đạt trần optimization_loop.max_rounds={max_rounds}, "
            "dừng dù Decision Agent đề xuất tiếp tục"
        )
        logger.warning(
            "Decision Agent [%s]: đã đạt trần optimization_loop.max_rounds=%d, "
            "DỪNG dù đề xuất tiếp tục.", decision.function_name, max_rounds,
        )
    return decision


def _parse_llm_decision(text: str) -> tuple[bool, bool, str | None]:
    """Đọc (accepted, continue, next_strategy) từ response. Mặc định an toàn:
    không parse được -> reject + stop, để không tự động nhận code đáng ngờ."""
    decision_match = re.search(r"##\s*Decision\s*\n\s*(\w+)", text, flags=re.IGNORECASE)
    accepted = bool(decision_match) and decision_match.group(1).strip().upper() == "ACCEPT"

    cont_match = re.search(r"##\s*Continue\s*\n\s*(\w+)", text, flags=re.IGNORECASE)
    continue_opt = bool(cont_match) and cont_match.group(1).strip().upper() == "CONTINUE"

    strat_match = re.search(
        r"##\s*Next strategy\s*\n(.*?)(?:\n##\s|\Z)", text, flags=re.DOTALL | re.IGNORECASE
    )
    next_strategy = strat_match.group(1).strip() if strat_match else None
    if next_strategy and next_strategy.lower() in {"none", "n/a", "-"}:
        next_strategy = None

    return accepted, continue_opt, next_strategy


def decide_after_benchmark(
    function_name: str,
    before_sec: float | None,
    after_sec: float | None,
    num_agents: int = 1,
    backend: Any | None = None,
    rust_code: str = "",
    previous_strategy: str = "(chưa có)",
    session: Any | None = None,
    round_index: int = 1,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> AgentDecision:
    """HÀM CHÍNH.

    before_sec/after_sec: thời gian trung bình (giây) của python_pure và
        rust_pure cho CÙNG hotspot, lấy từ kết quả stage6_benchmark.
    num_agents: 1 -> rule; 2 -> gọi LLM (cần `backend`).

    round_index/max_rounds: vòng hiện tại và TRẦN CỨNG của vòng tối ưu tốc
        độ (config `optimization_loop.max_rounds`). Decision Agent được tự
        quyết dừng sớm như thiết kế gốc POLO, NHƯNG khi đã chạm trần thì bị
        ép dừng bất kể LLM đề xuất gì -- xem `_apply_round_cap`.

    KHÔNG raise khi LLM lỗi -- tự rơi về rule và ghi rõ lý do, để pipeline
    chạy tiếp được.
    """
    if num_agents < 2:
        decision = _decide_by_rule(function_name, before_sec, after_sec)
        decision.round_index = round_index
        logger.info("Decision Agent[rule] %s: %s", function_name, decision.reason)
        return _apply_round_cap(decision, round_index, max_rounds)

    if backend is None and session is None:
        logger.warning(
            "num_agents=2 nhưng không có model backend -- rơi về rule cho '%s'.",
            function_name,
        )
        fallback = _decide_by_rule(function_name, before_sec, after_sec)
        fallback.reason += " [fallback: num_agents=2 nhưng thiếu backend]"
        fallback.round_index = round_index
        return _apply_round_cap(fallback, round_index, max_rounds)

    from stage4_llm_transpile.model_backend import ModelBackendError

    # Session RIÊNG của Decision Agent. Nếu caller không truyền vào thì tạo
    # mới tại đây -- tuyệt đối KHÔNG dùng lại session của Generator Agent,
    # để 2 vai trò không nhìn thấy lịch sử của nhau.
    if session is None:
        from stage4_llm_transpile.agent_session import AgentSession

        session = AgentSession(
            role_name="decision",
            system_prompt=DECISION_SYSTEM_PROMPT,
            backend=backend,
        )

    speedup = compute_speedup(before_sec, after_sec)
    prompt = PROMPT_TEMPLATE.format(
        function_name=function_name,
        before_ms=_fmt_ms(before_sec),
        after_ms=_fmt_ms(after_sec),
        speedup="không tính được" if speedup is None else f"{speedup:.3f}x",
        rust_code=(rust_code or "(không có code)")[:4000],
        previous_strategy=previous_strategy or "(chưa có)",
    )

    try:
        response = session.send(prompt)
    except ModelBackendError as exc:
        logger.error(
            "Decision Agent: gọi LLM thất bại cho '%s' (%s) -- rơi về rule.",
            function_name, exc,
        )
        fallback = _decide_by_rule(function_name, before_sec, after_sec)
        fallback.reason += f" [fallback vì LLM lỗi: {exc}]"
        fallback.round_index = round_index
        return _apply_round_cap(fallback, round_index, max_rounds)

    accepted, continue_opt, next_strategy = _parse_llm_decision(response)
    decision = AgentDecision(
        function_name=function_name, accepted=accepted, continue_optimizing=continue_opt,
        next_strategy=next_strategy, speedup=speedup, source="llm",
        reason=f"LLM quyết định: {'ACCEPT' if accepted else 'REJECT'}, "
               f"{'CONTINUE' if continue_opt else 'STOP'}",
        round_index=round_index,
        raw_response=response,
    )
    logger.info(
        "Decision Agent[llm] %s (vòng %d/%d): %s",
        function_name, round_index, max_rounds, decision.reason,
    )
    return _apply_round_cap(decision, round_index, max_rounds)
