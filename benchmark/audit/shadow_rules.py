"""LẦN CHẠY CHẨN ĐOÁN, mục B7 -- với MỖI hàm đã được CHẤP NHẬN (accepted_round
không None), ghi rõ quy tắc BÓNG nào (B1-B4) SẼ loại nó nếu được thi hành
nghiêm. CHẾ ĐỘ BÓNG: chỉ liệt kê, KHÔNG đổi accepted_round/reason nào.

Cố ý CHỈ B1-B4 (không B5/B6): B5 (PIL tobytes) và B6 (NOT_COVERED_BY_TESTS
làm giàu) chỉ có ý nghĩa cho hàm đã BỊ LOẠI từ trước (NONDETERMINISTIC /
NOT_COVERED_BY_TESTS) -- một hàm ĐÃ được accept thì hai lý do đó không áp
dụng được cho nó nữa.
"""
from __future__ import annotations

# Ngưỡng B3: "quá ít đầu vào khác nhau" -- CÙNG số min_replayable_hotspots=2
# dùng ở nơi khác của pipeline (repo_pipeline.py/run_experiment1.py), để
# thống nhất "thế nào là đủ đa dạng". Đổi ở ĐÂY nếu quét offline (mục E,
# bảng ngưỡng) thấy 2 quá chặt/lỏng -- không cần sửa logic accept thật.
B3_MIN_DISTINCT_INPUTS = 2


def would_shadow_reject(rec) -> list[str]:
    """`rec`: `repo_pipeline.HotspotRecord`. Trả về danh sách lý do (rỗng nếu
    không quy tắc bóng nào loại nó) -- mỗi phần tử tự giải thích được, không
    cần tra ngược code."""
    reasons: list[str] = []

    hc = rec.shadow_hardcoding or {}
    if hc.get("flagged"):
        reasons.append(f"B1_hardcoding: {', '.join(hc.get('reasons', []) or [])}")

    sm = rec.shadow_mutation or {}
    if sm.get("status") == "TESTED":
        bad = [
            v for v, r in (sm.get("per_version") or {}).items()
            if r.get("status") != "MATCH"
        ]
        if bad:
            reasons.append(f"B2_mutation_fail: {', '.join(bad)}")

    if rec.n_distinct_inputs is not None and rec.n_distinct_inputs < B3_MIN_DISTINCT_INPUTS:
        reasons.append(
            f"B3_too_few_distinct_inputs: {rec.n_distinct_inputs} < {B3_MIN_DISTINCT_INPUTS}"
        )

    if rec.hybrid_slower:
        reasons.append("B4_hybrid_slower")

    return reasons
