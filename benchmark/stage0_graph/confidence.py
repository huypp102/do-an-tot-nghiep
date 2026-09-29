"""PHA 4.1 -- `graph_confidence`: graph này đáng tin đến đâu.

VÌ SAO CẦN: mọi kết luận của Decision Gate đều dựa trên PCG. Nếu PCG toàn cạnh
đoán (heuristic) hoặc bỏ mù nhiều lời gọi, thì "hotspot này quan trọng" cũng
chỉ là phỏng đoán. Không có chỉ số này thì không phân biệt được "gate tự tin
chọn" với "gate chọn bừa vì không có gì tốt hơn".

QUAN TRỌNG: nhãn này **KHÔNG loại repo nào khỏi thực nghiệm**. Nó chỉ đi vào
tầng `confidence_level` của Decision Gate (Pha 4.4) và vào báo cáo. Loại repo
vì graph xấu là chọn mẫu theo chất lượng công cụ của chính mình, không phải
theo tính chất của bài toán.

CÔNG THỨC (lấy trực tiếp từ code của teammate, giữ nguyên hệ số để số liệu
hai bên so được với nhau):

    edge_quality = exact_ratio + 0.6 * heuristic_ratio

    unresolved_ratio <= 0.15 và edge_quality >= 0.70  -> HIGH
    unresolved_ratio <= 0.40 và edge_quality >= 0.40  -> MEDIUM
    còn lại                                           -> LOW
    không có hàm nào                                  -> OUT_OF_SCOPE

Hệ số 0.6 nghĩa là cạnh đoán được tính khoảng 60% giá trị của cạnh chắc chắn --
có ích, nhưng không bằng.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("benchmark.stage0_graph.confidence")

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"
OUT_OF_SCOPE = "OUT_OF_SCOPE"

GRAPH_CONFIDENCE_LEVELS = (HIGH, MEDIUM, LOW, OUT_OF_SCOPE)

HEURISTIC_WEIGHT = 0.6
HIGH_MAX_UNRESOLVED = 0.15
HIGH_MIN_QUALITY = 0.70
MEDIUM_MAX_UNRESOLVED = 0.40
MEDIUM_MIN_QUALITY = 0.40


def edge_quality(exact_ratio: float, heuristic_ratio: float) -> float:
    """Chất lượng cạnh: cạnh chắc chắn tính đủ, cạnh đoán tính 60%."""
    return float(exact_ratio) + HEURISTIC_WEIGHT * float(heuristic_ratio)


def compute_graph_confidence(graph) -> dict:
    """Trả về dict đủ để ghi vào kết quả và dùng ở Decision Gate."""
    n_functions = len(getattr(graph, "functions", None) or {})
    stats = dict(getattr(graph, "call_resolution", None) or {})

    if n_functions == 0:
        return {
            "level": OUT_OF_SCOPE,
            "reason": "graph không có hàm nào -- ngoài phạm vi phân tích",
            "n_functions": 0,
            "edge_quality": 0.0,
            **stats,
        }

    exact_ratio = float(stats.get("exact_ratio", 0.0))
    heuristic_ratio = float(stats.get("heuristic_ratio", 0.0))
    unresolved_ratio = float(stats.get("unresolved_ratio", 0.0))
    quality = edge_quality(exact_ratio, heuristic_ratio)

    if stats.get("n_in_scope_calls", 0) == 0:
        # Có hàm nhưng KHÔNG lời gọi nào trong scope: các hàm độc lập với nhau.
        # Không phải graph xấu, nhưng cũng không có cấu trúc nào để tin --
        # PageRank trên graph không cạnh cho điểm đồng đều mọi hàm.
        level = LOW
        reason = (
            "không có lời gọi nào giữa các hàm trong scope -- PCG không có cạnh, "
            "FuncRank tĩnh khi đó chỉ là thứ tự id, không mang thông tin"
        )
    elif unresolved_ratio <= HIGH_MAX_UNRESOLVED and quality >= HIGH_MIN_QUALITY:
        level = HIGH
        reason = (
            f"unresolved {unresolved_ratio:.0%} <= {HIGH_MAX_UNRESOLVED:.0%} và "
            f"edge_quality {quality:.2f} >= {HIGH_MIN_QUALITY}"
        )
    elif unresolved_ratio <= MEDIUM_MAX_UNRESOLVED and quality >= MEDIUM_MIN_QUALITY:
        level = MEDIUM
        reason = (
            f"unresolved {unresolved_ratio:.0%} <= {MEDIUM_MAX_UNRESOLVED:.0%} và "
            f"edge_quality {quality:.2f} >= {MEDIUM_MIN_QUALITY}"
        )
    else:
        level = LOW
        reason = (
            f"unresolved {unresolved_ratio:.0%} hoặc edge_quality {quality:.2f} "
            f"không đạt ngưỡng MEDIUM"
        )

    out = {
        "level": level,
        "reason": reason,
        "n_functions": n_functions,
        "edge_quality": round(quality, 4),
        **stats,
    }
    logger.info(
        "graph_confidence = %s (%d hàm, exact %.0f%%, heuristic %.0f%%, "
        "unresolved %.0f%%, edge_quality %.2f) -- %s",
        level, n_functions, exact_ratio * 100, heuristic_ratio * 100,
        unresolved_ratio * 100, quality, reason,
    )
    return out
