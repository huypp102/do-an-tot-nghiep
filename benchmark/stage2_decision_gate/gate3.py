"""PHA 4.2 + 4.4 -- Decision Gate BA TẦNG ĐỘC LẬP.

VÌ SAO KHÔNG CỘNG ĐIỂM THÀNH MỘT SỐ: gộp ba chuyện khác loại vào một điểm số
tổng cho phép một mặt tốt bù cho một mặt hỏng. Ví dụ hàm cực nóng nhưng phụ
thuộc `cv2` sẽ "đủ điểm" dù dịch nó chắc chắn vô ích. Ba tầng dưới đây được
kiểm ĐỒNG THỜI bằng AND: một tầng fail là loại ngay, bất kể hai tầng kia tốt
đến đâu.

    hotspot_level    -- hàm này có NÓNG không?
                        HIGH / MEDIUM / LOW_CONFIDENCE / LOW
    feasibility      -- dịch nó sang Rust có KHẢ THI không?
                        FEASIBLE / TEST_ONLY / BLOCKED
    confidence_level -- ta có TIN được hai kết luận trên không?
                        HIGH / MEDIUM / LOW

`LOW_CONFIDENCE` của tầng 1 cố ý khác `LOW`: `LOW` nghĩa là "đo được và nó
không nóng", còn `LOW_CONFIDENCE` nghĩa là "không đo được nên không biết". Gộp
hai thứ đó lại là mất đúng thông tin cần cho quyết định.

PHA 4.2: giữ SONG SONG `funcrank_static` và `funcrank_dynamic`, không gộp
thành một `effective_funcrank` rồi bỏ một cái. Hai con số trả lời hai câu khác
nhau: static nói "hàm này nằm ở vị trí trung tâm trong cấu trúc gọi hàm",
dynamic nói "hàm này thật sự tốn thời gian khi chạy". Một hàm static cao +
dynamic thấp là hàm được gọi khắp nơi nhưng rẻ -- biết được điều đó mới ra
quyết định đúng.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("benchmark.stage2_decision_gate.gate3")

# --- Tầng 1: hotspot_level -------------------------------------------------
HOTSPOT_HIGH = "HIGH"
HOTSPOT_MEDIUM = "MEDIUM"
HOTSPOT_LOW_CONFIDENCE = "LOW_CONFIDENCE"
HOTSPOT_LOW = "LOW"

# --- Tầng 2: feasibility --------------------------------------------------
FEASIBLE = "FEASIBLE"
TEST_ONLY = "TEST_ONLY"
BLOCKED = "BLOCKED"

# --- Tầng 3: confidence_level --------------------------------------------
CONF_HIGH = "HIGH"
CONF_MEDIUM = "MEDIUM"
CONF_LOW = "LOW"

# --- Quyết định cuối ------------------------------------------------------
SELECT = "SELECT"
REVIEW = "REVIEW"
KEEP_PYTHON = "KEEP_PYTHON"
REJECT_BLOCKED = "REJECT_BLOCKED"
DECISIONS = (SELECT, REVIEW, KEEP_PYTHON, REJECT_BLOCKED)

# --- translation_unit ----------------------------------------------------
UNIT_FUNCTION = "FUNCTION"
UNIT_BATCH_CALLER = "BATCH_CALLER"

# Ngưỡng. Đặt tên hằng để đổi được ở một chỗ và để báo cáo trích dẫn được.
RUNTIME_SHARE_HIGH_PCT = 5.0    # >= 5% self-time tổng -> rõ ràng là nóng
RUNTIME_SHARE_MEDIUM_PCT = 1.0
FUNCRANK_QUANTILE_HIGH = 0.90   # top 10% theo FuncRank
FUNCRANK_QUANTILE_MEDIUM = 0.70
MAX_UNRESOLVED_CALLS_FOR_CONFIDENCE = 3
BATCH_MIN_CALLS = 10_000        # gọi rất nhiều lần...
BATCH_MAX_US_PER_CALL = 10.0    # ...mà mỗi lần rất rẻ -> nên dịch cả vòng gọi


@dataclass
class GateVerdict:
    """Kết quả gate cho MỘT hàm. Ba tầng giữ riêng, không gộp."""

    function_name: str
    function_id: str = ""
    file: str = ""
    # Tầng 1
    hotspot_level: str = HOTSPOT_LOW
    hotspot_reason: str = ""
    funcrank_static: float | None = None
    funcrank_dynamic: float | None = None
    rank_source: str = ""
    funcrank_quantile: float | None = None
    direct_runtime_share_pct: float | None = None
    # Tầng 2
    feasibility: str = FEASIBLE
    feasibility_reason: str = ""
    dependency_roots: list[str] = field(default_factory=list)
    blocked_roots: list[str] = field(default_factory=list)
    code_role: str = "PRODUCTION"
    # Tầng 3
    confidence_level: str = CONF_MEDIUM
    confidence_reason: str = ""
    graph_confidence: str = ""
    unresolved_call_count: int = 0
    # Kết luận
    decision: str = KEEP_PYTHON
    decision_reason: str = ""
    translation_unit: str = UNIT_FUNCTION
    translation_unit_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "function": self.function_name,
            "function_id": self.function_id,
            "file": self.file,
            "hotspot_level": self.hotspot_level,
            "hotspot_reason": self.hotspot_reason,
            "funcrank_static": self.funcrank_static,
            "funcrank_dynamic": self.funcrank_dynamic,
            "rank_source": self.rank_source,
            "funcrank_quantile": self.funcrank_quantile,
            "direct_runtime_share_pct": self.direct_runtime_share_pct,
            "feasibility": self.feasibility,
            "feasibility_reason": self.feasibility_reason,
            "dependency_roots": self.dependency_roots,
            "blocked_roots": self.blocked_roots,
            "code_role": self.code_role,
            "confidence_level": self.confidence_level,
            "confidence_reason": self.confidence_reason,
            "graph_confidence": self.graph_confidence,
            "unresolved_call_count": self.unresolved_call_count,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "translation_unit": self.translation_unit,
            "translation_unit_reason": self.translation_unit_reason,
            "simple_label": simple_label(self.decision),
        }


# ---------------------------------------------------------------------------
# PHA 4.5 -- tương thích ngược
# ---------------------------------------------------------------------------
def simple_label(decision: str) -> str:
    """Map quyết định 4 mức -> 3 nhãn CŨ mà `run_pipeline.py` và
    `stage3_context_packaging` đang dùng.

    Nhờ map này, không chỗ nào ngoài gate phải sửa. Thông tin chi tiết KHÔNG
    mất: cả 4 trường của 3 tầng vẫn nằm trong `GateVerdict` và được xuất ra
    context JSON.
    """
    from stage2_decision_gate.gate import LABEL_CANDIDATE, LABEL_SKIP, LABEL_VECTORIZE

    if decision == SELECT:
        return LABEL_CANDIDATE
    if decision == REVIEW:
        # REVIEW = đáng xem nhưng chưa chắc; để `candidate` thì pipeline vẫn
        # thử dịch, và số liệu thật sẽ trả lời thay cho phỏng đoán.
        return LABEL_CANDIDATE
    if decision == REJECT_BLOCKED:
        return LABEL_SKIP
    return LABEL_VECTORIZE   # KEEP_PYTHON


# ---------------------------------------------------------------------------
# Tầng 1 -- hotspot_level
# ---------------------------------------------------------------------------
def _quantile_of(value: float | None, sorted_values: list[float]) -> float | None:
    """Vị trí phân vị của `value` trong tập đã sắp tăng. None nếu không tính được."""
    if value is None or not sorted_values:
        return None
    n_below = sum(1 for v in sorted_values if v < value)
    return n_below / len(sorted_values)


def classify_hotspot_level(
    share_pct: float | None,
    quantile: float | None,
    has_dynamic: bool,
) -> tuple[str, str]:
    """Tầng 1. Ưu tiên số liệu ĐỘNG; không có thì lùi về phân vị FuncRank tĩnh.

    Không có cả hai -> `LOW_CONFIDENCE`, KHÔNG phải `LOW`: ta không biết, và
    nói "không nóng" khi chưa đo là một kết luận sai lệch.
    """
    if share_pct is not None:
        if share_pct >= RUNTIME_SHARE_HIGH_PCT:
            return HOTSPOT_HIGH, (
                f"chiếm {share_pct:.2f}% self-time (>= {RUNTIME_SHARE_HIGH_PCT}%), đo được thật"
            )
        if share_pct >= RUNTIME_SHARE_MEDIUM_PCT:
            return HOTSPOT_MEDIUM, (
                f"chiếm {share_pct:.2f}% self-time (>= {RUNTIME_SHARE_MEDIUM_PCT}%)"
            )
        return HOTSPOT_LOW, f"chỉ chiếm {share_pct:.2f}% self-time khi chạy thật"

    if quantile is None:
        return HOTSPOT_LOW_CONFIDENCE, (
            "không có số liệu runtime, cũng không xếp được phân vị FuncRank"
        )
    if quantile >= FUNCRANK_QUANTILE_HIGH:
        return HOTSPOT_HIGH, (
            f"không có runtime, nhưng FuncRank tĩnh ở phân vị {quantile:.0%} "
            f"(>= {FUNCRANK_QUANTILE_HIGH:.0%})"
        )
    if quantile >= FUNCRANK_QUANTILE_MEDIUM:
        return HOTSPOT_MEDIUM, (
            f"không có runtime, FuncRank tĩnh ở phân vị {quantile:.0%}"
        )
    if not has_dynamic:
        return HOTSPOT_LOW_CONFIDENCE, (
            f"không có runtime cho hàm nào trong repo; FuncRank tĩnh chỉ ở phân "
            f"vị {quantile:.0%} nên chưa kết luận được là không nóng"
        )
    return HOTSPOT_LOW, (
        f"repo có runtime nhưng hàm này không xuất hiện, và FuncRank tĩnh chỉ "
        f"ở phân vị {quantile:.0%}"
    )


# ---------------------------------------------------------------------------
# Tầng 2 -- feasibility
# ---------------------------------------------------------------------------
def classify_feasibility(dep: dict, code_role: str) -> tuple[str, str]:
    """Tầng 2. Dựa trên phụ thuộc CẤP HÀM (Pha 4.3) và vai trò của code."""
    if code_role != "PRODUCTION":
        return TEST_ONLY, f"code_role={code_role} -- không phải code sản phẩm"
    if dep.get("is_blocked"):
        reasons = dep.get("blocked_reasons") or {}
        detail = "; ".join(f"{k}: {v}" for k, v in reasons.items())
        return BLOCKED, f"phụ thuộc package chặn ({detail})"
    roots = dep.get("dependency_roots") or []
    return FEASIBLE, (
        f"phụ thuộc thật của hàm: {roots or 'không có'} -- không có package chặn"
    )


# ---------------------------------------------------------------------------
# Tầng 3 -- confidence_level
# ---------------------------------------------------------------------------
def classify_confidence(graph_conf_level: str, unresolved: int) -> tuple[str, str]:
    """Tầng 3. Tổ hợp chất lượng graph (mức repo) + số lời gọi mù (mức hàm)."""
    from stage0_graph.confidence import HIGH as G_HIGH, MEDIUM as G_MEDIUM

    few_unresolved = unresolved <= MAX_UNRESOLVED_CALLS_FOR_CONFIDENCE
    if graph_conf_level == G_HIGH and few_unresolved:
        return CONF_HIGH, (
            f"graph_confidence=HIGH và chỉ {unresolved} lời gọi không resolve được"
        )
    if graph_conf_level in (G_HIGH, G_MEDIUM) and few_unresolved:
        return CONF_MEDIUM, (
            f"graph_confidence={graph_conf_level}, {unresolved} lời gọi mù"
        )
    if not few_unresolved:
        return CONF_LOW, (
            f"{unresolved} lời gọi trong hàm không resolve được "
            f"(> {MAX_UNRESOLVED_CALLS_FOR_CONFIDENCE}) -- context quanh hàm không đáng tin"
        )
    return CONF_LOW, f"graph_confidence={graph_conf_level}"


# ---------------------------------------------------------------------------
# Quyết định cuối -- AND cả 3 tầng
# ---------------------------------------------------------------------------
def decide(hotspot_level: str, feasibility: str, confidence_level: str) -> tuple[str, str]:
    """Kiểm ĐỒNG THỜI 3 tầng. Một tầng fail là loại ngay, không bù trừ."""
    # Không khả thi -> loại, bất kể nóng đến đâu. Đặt trước tiên vì đây là
    # điều kiện cứng nhất: dịch một lớp vỏ gọi OpenCV thì không có gì để nhanh hơn.
    if feasibility == BLOCKED:
        return REJECT_BLOCKED, "feasibility=BLOCKED -- dịch sang Rust không thể/vô ích"
    if feasibility == TEST_ONLY:
        return REJECT_BLOCKED, "feasibility=TEST_ONLY -- không phải code sản phẩm"

    # Không nóng -> giữ Python, dù khả thi và dù ta rất tin số liệu.
    if hotspot_level == HOTSPOT_LOW:
        return KEEP_PYTHON, "hotspot_level=LOW -- đo được và nó không nóng"

    if hotspot_level == HOTSPOT_HIGH:
        if confidence_level == CONF_HIGH:
            return SELECT, "nóng rõ, khả thi, và số liệu đáng tin -- cả 3 tầng đạt"
        if confidence_level == CONF_MEDIUM:
            return SELECT, "nóng rõ, khả thi, confidence MEDIUM -- vẫn đủ để chọn"
        return REVIEW, (
            "nóng rõ và khả thi NHƯNG confidence=LOW -- cần xem lại trước khi tin"
        )

    if hotspot_level == HOTSPOT_MEDIUM:
        if confidence_level == CONF_HIGH:
            return SELECT, "nóng vừa nhưng số liệu rất đáng tin -- chọn"
        return REVIEW, f"nóng vừa, confidence={confidence_level} -- cần xem lại"

    # LOW_CONFIDENCE: chưa đo được nên chưa biết nóng hay không.
    return REVIEW, (
        "hotspot_level=LOW_CONFIDENCE -- chưa có số liệu để kết luận, cần xem lại "
        "(bật graph.build_mode=dynamic để có runtime)"
    )


def classify_translation_unit(
    decision: str, call_count: int, self_time_ms: float | None
) -> tuple[str, str]:
    """`BATCH_CALLER` cho hàm được gọi RẤT NHIỀU lần mà mỗi lần RẤT RẺ.

    Với hàm như vậy, dịch riêng nó sang Rust gần như vô ích: chi phí vượt biên
    Python↔Rust mỗi lời gọi (~1 µs) sẽ ăn hết phần tiết kiệm được. Thứ đáng
    dịch là VÒNG LẶP GỌI nó. Đây là gợi ý cho Stage 4, không phải loại bỏ.
    """
    if decision != SELECT:
        return UNIT_FUNCTION, ""
    if not call_count or call_count < BATCH_MIN_CALLS or self_time_ms is None:
        return UNIT_FUNCTION, ""
    us_per_call = (self_time_ms * 1000.0) / call_count
    if us_per_call < BATCH_MAX_US_PER_CALL:
        return UNIT_BATCH_CALLER, (
            f"gọi {call_count} lần, chỉ {us_per_call:.2f} µs/lần "
            f"(< {BATCH_MAX_US_PER_CALL} µs) -- chi phí vượt biên Python↔Rust mỗi "
            f"lời gọi sẽ ăn hết phần tiết kiệm; nên dịch VÒNG LẶP GỌI thay vì hàm"
        )
    return UNIT_FUNCTION, f"{us_per_call:.2f} µs/lần -- đủ lớn để dịch riêng hàm"


# ---------------------------------------------------------------------------
# HÀM CHÍNH
# ---------------------------------------------------------------------------
def evaluate_functions(
    graph,
    function_names: list[str] | None = None,
    profile=None,
) -> dict[str, GateVerdict]:
    """Chạy gate 3 tầng cho danh sách hàm. Trả về {tên hàm: GateVerdict}.

    `profile` là kết quả profiling động (stage1_profiling) nếu có -- dùng để
    lấy `direct_runtime_share_pct` và `dynamic_call_count`.
    """
    from stage0_graph.confidence import compute_graph_confidence
    from stage0_graph.rank import func_rank_static
    from stage0_graph.test_filter import classify as classify_test
    from stage2_decision_gate.dependency_roots import analyze as analyze_deps

    graph_conf = compute_graph_confidence(graph)
    static_scores = func_rank_static(graph)
    test_ids = classify_test(graph)

    # Phân vị tính trên TOÀN BỘ hàm trong graph, không chỉ trên top-K: phân vị
    # trong một tập đã lọc sẵn là con số vô nghĩa.
    sorted_static = sorted(static_scores.values())

    # FuncRank động: giữ RIÊNG, không gộp vào static (Pha 4.2).
    dynamic_scores: dict[str, float] = {}
    try:
        from stage0_graph.rank import func_rank_dynamic

        dynamic_scores = func_rank_dynamic(graph) or {}
    except Exception as exc:  # noqa: BLE001 -- không có dữ liệu động là bình thường
        logger.debug("Không tính được funcrank_dynamic: %s", exc)
    sorted_dynamic = sorted(dynamic_scores.values())
    has_dynamic = bool(dynamic_scores)

    wanted = set(function_names) if function_names else None
    verdicts: dict[str, GateVerdict] = {}

    for fid, fn in (graph.functions or {}).items():
        if wanted is not None and fn.name not in wanted:
            continue

        file_node = (graph.files or {}).get(fn.file)
        imports_raw = list(getattr(file_node, "imports_raw", None) or [])
        dep = analyze_deps(fn.source, imports_raw)
        code_role = "TEST" if fid in test_ids else "PRODUCTION"

        share = fn.dynamic_time_pct
        f_static = static_scores.get(fid)
        f_dynamic = dynamic_scores.get(fid)
        quantile = (
            _quantile_of(f_dynamic, sorted_dynamic) if f_dynamic is not None
            else _quantile_of(f_static, sorted_static)
        )

        v = GateVerdict(
            function_name=fn.name, function_id=fid, file=fn.file,
            funcrank_static=f_static, funcrank_dynamic=f_dynamic,
            rank_source=("dynamic" if f_dynamic is not None else "static_fallback"),
            funcrank_quantile=quantile,
            direct_runtime_share_pct=share,
            dependency_roots=dep["dependency_roots"],
            blocked_roots=dep["blocked_roots"],
            code_role=code_role,
            graph_confidence=graph_conf["level"],
            unresolved_call_count=getattr(fn, "unresolved_call_count", 0),
        )
        v.hotspot_level, v.hotspot_reason = classify_hotspot_level(
            share, quantile, has_dynamic
        )
        v.feasibility, v.feasibility_reason = classify_feasibility(dep, code_role)
        v.confidence_level, v.confidence_reason = classify_confidence(
            graph_conf["level"], v.unresolved_call_count
        )
        v.decision, v.decision_reason = decide(
            v.hotspot_level, v.feasibility, v.confidence_level
        )
        self_time_ms = None
        if profile is not None:
            self_time_ms = (getattr(profile, "self_time", None) or {}).get(fn.name)
            if self_time_ms is not None:
                self_time_ms *= 1000.0   # profile lưu theo giây
        v.translation_unit, v.translation_unit_reason = classify_translation_unit(
            v.decision, fn.dynamic_call_count, self_time_ms
        )

        # Trùng tên: giữ bản có quyết định "mạnh" hơn, cùng logic như gate cũ.
        priority = {REJECT_BLOCKED: 0, KEEP_PYTHON: 1, REVIEW: 2, SELECT: 3}
        prev = verdicts.get(fn.name)
        if prev is None or priority[v.decision] > priority[prev.decision]:
            verdicts[fn.name] = v

        logger.info(
            "Gate3[%s] %s (%s:%d) hotspot=%s feasibility=%s confidence=%s unit=%s -- %s",
            v.decision, fn.name, fn.file, fn.lineno_start, v.hotspot_level,
            v.feasibility, v.confidence_level, v.translation_unit, v.decision_reason,
        )

    # Hàm không có trong graph: giữ hành vi CŨ (không chặn nhầm).
    if wanted:
        for name in sorted(wanted - set(verdicts)):
            logger.warning(
                "Gate3: không tìm thấy hàm '%s' trong graph -- mặc định REVIEW "
                "để không chặn nhầm.", name,
            )
            verdicts[name] = GateVerdict(
                function_name=name, decision=REVIEW,
                decision_reason="không tìm thấy trong graph -- không chặn nhầm",
                hotspot_level=HOTSPOT_LOW_CONFIDENCE,
                confidence_level=CONF_LOW,
                graph_confidence=graph_conf["level"],
            )

    counts: dict[str, int] = {}
    for v in verdicts.values():
        counts[v.decision] = counts.get(v.decision, 0) + 1
    logger.info(
        "Gate3 tổng kết: %s | graph_confidence=%s | có runtime: %s",
        counts, graph_conf["level"], has_dynamic,
    )
    return verdicts


def summarize(verdicts: dict[str, GateVerdict], graph_confidence: dict | None = None) -> dict:
    """Tóm tắt để ghi vào kết quả."""
    def _count(attr: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in verdicts.values():
            key = getattr(v, attr)
            out[key] = out.get(key, 0) + 1
        return out

    return {
        "n_functions": len(verdicts),
        "graph_confidence": graph_confidence or {},
        "by_decision": _count("decision"),
        "by_hotspot_level": _count("hotspot_level"),
        "by_feasibility": _count("feasibility"),
        "by_confidence_level": _count("confidence_level"),
        "by_translation_unit": _count("translation_unit"),
        "verdicts": {name: v.as_dict() for name, v in sorted(verdicts.items())},
    }
