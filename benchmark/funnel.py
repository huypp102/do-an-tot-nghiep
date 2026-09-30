"""PHẦN 1.2 -- Báo cáo PHỄU: mỗi bước có mẫu số rõ ràng.

VÌ SAO CẦN: một bảng chỉ ghi "3 hotspot MEASURED" không nói được điều quan
trọng nhất -- 3 trên bao nhiêu, và rơi ở đâu. Trên dataset thật, số hotspot
rụng ở bước ghi/phát lại đối số lớn hơn nhiều so với bước biên dịch, và đó là
kết luận đáng viết vào luận văn hơn cả tỉ lệ Pass@1.

CÁC BƯỚC (theo đúng thứ tự pipeline; mỗi bước là TẬP CON của bước trước):

    repo              repo được đưa vào chạy
    baseline_pass     bộ test Python gốc chạy được và pass >= 1 test
    hotspot_found     FuncRank chọn được hotspot (đếm theo HOTSPOT từ đây)
    replayable        ghi + phát lại được đối số thật, và tất định
    tier_supported    thuộc Tầng 1 hoặc Tầng 2
    generated         Generator Agent sinh được code Rust
    compiled          `cargo check` OK (sau tối đa max_retries vòng sửa)
    correct_fn        output khớp bản Python trên đối số thật
    regression_free   bộ test repo sau khi thay Rust không mất test nào
    accepted          Decision Agent chấp nhận, và KHÔNG rỗng (xem VACUOUS)

HAI TỈ LỆ cho mỗi bước, cố ý ghi cả hai vì chúng trả lời 2 câu khác nhau:
    vs_prev   -- so với bước NGAY TRƯỚC: "bước này làm rụng bao nhiêu?"
    vs_first  -- so với bước ĐẦU: "cuối cùng còn lại bao nhiêu phần?"
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Thứ tự CỐ ĐỊNH của phễu. Mọi báo cáo đọc từ đây để không chỗ nào lệch thứ tự.
REPO_STEPS = ("repo", "baseline_pass")
HOTSPOT_STEPS = (
    "hotspot_found", "replayable", "tier_supported", "generated",
    "compiled", "correct_fn", "regression_free", "accepted",
)
ALL_STEPS = REPO_STEPS + HOTSPOT_STEPS

STEP_HELP: dict[str, str] = {
    "repo": "repo được đưa vào chạy",
    "baseline_pass": "bộ test Python GỐC chạy được và pass >= 1 test",
    "hotspot_found": "FuncRank chọn được hotspot (từ đây đếm theo hotspot)",
    "replayable": "ghi + phát lại được đối số thật, và tất định",
    "tier_supported": "thuộc Tầng 1 (kiểu gốc) hoặc Tầng 2 (kernel+shim)",
    "generated": "Generator Agent sinh được code Rust",
    "compiled": "cargo check OK (sau tối đa max_retries vòng sửa)",
    "correct_fn": "output khớp bản Python trên đối số thật",
    "regression_free": "bộ test repo không mất test nào sau khi thay Rust",
    "accepted": "Decision Agent chấp nhận, VÀ hàm Rust thật sự được gọi",
}

# Nhãn phụ, KHÔNG phải bước phễu -- chúng loại hotspot khỏi MỘT phép tính cụ
# thể chứ không nói hotspot thất bại.
VACUOUS = "VACUOUS"
"""Bộ test repo chạy xanh nhưng hàm Rust KHÔNG được gọi lần nào
(`rust_call_count == 0`) -> "đúng một cách rỗng". Bị loại khỏi tỉ lệ
regression-free, vì test xanh ở đây không chứng minh được gì về bản Rust."""

CONFOUNDED = "CONFOUNDED"
"""Prompt bị cắt vì vượt `num_ctx` ở ÍT NHẤT MỘT nhánh ablation -> tách khỏi
phép so sánh chính (xem ablation.py)."""


@dataclass
class RepoFunnel:
    """Phễu của MỘT repo."""

    label: str
    baseline_pass: bool = False
    # {tên hotspot: {bước: bool}} -- giữ theo hotspot để truy ngược được
    hotspots: dict[str, dict[str, bool]] = field(default_factory=dict)
    # Lý do loại từng hotspot, để log/báo cáo (yêu cầu 1.1).
    drop_reasons: dict[str, str] = field(default_factory=dict)
    vacuous: set[str] = field(default_factory=set)
    confounded: set[str] = field(default_factory=set)

    def count(self, step: str) -> int:
        if step == "repo":
            return 1
        if step == "baseline_pass":
            return 1 if self.baseline_pass else 0
        return sum(1 for flags in self.hotspots.values() if flags.get(step))

    def denominator(self, step: str) -> int:
        """Mẫu số của bước: số phần tử đã qua bước NGAY TRƯỚC."""
        idx = ALL_STEPS.index(step)
        if idx == 0:
            return 1
        return self.count(ALL_STEPS[idx - 1])

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "counts": {s: self.count(s) for s in ALL_STEPS},
            "denominators": {s: self.denominator(s) for s in ALL_STEPS},
            "hotspots": self.hotspots,
            "drop_reasons": self.drop_reasons,
            "vacuous": sorted(self.vacuous),
            "confounded": sorted(self.confounded),
        }


def funnel_from_record(rec, gate_label_ok: bool = True) -> dict[str, bool]:
    """Suy ra các cờ phễu của MỘT hotspot từ `repo_pipeline.HotspotRecord`.

    Cố ý dùng dữ liệu ĐÃ GHI (reason, tier, compiled, correctness, rounds) chứ
    không thêm trạng thái mới: một bước là True khi có BẰNG CHỨNG nó đã xong,
    không phải khi "không thấy lỗi".
    """
    import outcomes
    from stage1_profiling.deep_compare import TIER_KERNEL, TIER_NATIVE

    reason = rec.reason or ""
    flags: dict[str, bool] = {s: False for s in HOTSPOT_STEPS}
    flags["hotspot_found"] = True

    # replayable: đã ghi được >=1 lời gọi và KHÔNG bị loại ở nhóm lý do của Pha B.
    blocked_at_replay = {
        outcomes.NOT_COVERED_BY_TESTS, outcomes.UNREPLAYABLE_ARGS,
        outcomes.NONDETERMINISTIC, outcomes.UNRESOLVABLE_IMPORT,
    }
    if reason in blocked_at_replay:
        return flags
    flags["replayable"] = rec.n_captured_calls > 0

    if not flags["replayable"]:
        return flags
    flags["tier_supported"] = rec.tier in (TIER_NATIVE, TIER_KERNEL)
    if not flags["tier_supported"] or reason == outcomes.UNSUPPORTED_KIND:
        flags["tier_supported"] = False
        return flags

    # Hotspot bị Decision Gate gạt, HOẶC hợp lệ nhưng ngoài hạn mức
    # top_k_translate -- cả hai đều KHÔNG phải thất bại, chỉ không thuộc
    # phạm vi dịch lượt này. Dừng phễu ở đây.
    if reason in (outcomes.GATE_SKIPPED, outcomes.EXCLUDED_BY_TOP_K) or not gate_label_ok:
        return flags

    if reason == outcomes.LLM_FAILED:
        return flags
    flags["generated"] = True

    if reason == outcomes.COMPILE_FAILED:
        return flags
    # `compiled is None` = Stage 5 bị bỏ qua (thiếu cargo). Không suy bừa là
    # đã compile: dùng build_status làm bằng chứng thay thế.
    if rec.compiled is True:
        flags["compiled"] = True
    elif rec.compiled is None and rec.build_status in ("BUILT_OK",):
        flags["compiled"] = True
    if not flags["compiled"]:
        return flags

    statuses = {v: (e or {}).get("status") for v, e in (rec.correctness or {}).items()}
    if statuses and all(s == "MATCH" for s in statuses.values()):
        flags["correct_fn"] = True
    if not flags["correct_fn"]:
        return flags

    flags["regression_free"] = bool(rec.regression_free_contrib)
    if not flags["regression_free"]:
        return flags

    # accepted: Decision Agent chấp nhận ở ít nhất 1 vòng VÀ không rỗng.
    flags["accepted"] = bool(rec.accepted_round) and not rec.vacuous
    return flags


def build_repo_funnel(label: str, baseline_ok: bool, records: dict) -> RepoFunnel:
    """Dựng phễu cho 1 repo từ dict {tên: HotspotRecord}."""
    rf = RepoFunnel(label=label, baseline_pass=baseline_ok)
    for name, rec in records.items():
        rf.hotspots[name] = funnel_from_record(rec)
        if rec.reason and rec.reason != "MEASURED":
            rf.drop_reasons[name] = f"{rec.reason}: {(rec.detail or '')[:200]}"
        if rec.vacuous:
            rf.vacuous.add(name)
        if getattr(rec, "confounded", False):
            rf.confounded.add(name)
    return rf


def aggregate(funnels: list[RepoFunnel]) -> dict:
    """Tổng hợp phễu qua nhiều repo."""
    totals = {s: 0 for s in ALL_STEPS}
    for f in funnels:
        for s in ALL_STEPS:
            totals[s] += f.count(s)
    out: dict = {"n_repos": len(funnels), "counts": totals, "steps": []}
    # MỖI ĐƠN VỊ CÓ MỐC RIÊNG. Chia số hotspot cho số repo sẽ ra tỉ lệ vô
    # nghĩa (vd 23 hotspot / 5 repo = 460%), nên bước đơn vị "hotspot" lấy
    # `hotspot_found` làm mốc, bước đơn vị "repo" lấy `repo`.
    base_repo = totals["repo"] or 0
    base_hotspot = totals["hotspot_found"] or 0
    prev = None
    for s in ALL_STEPS:
        n = totals[s]
        unit = "repo" if s in REPO_STEPS else "hotspot"
        # Bước chuyển đơn vị (repo -> hotspot) không có tỉ lệ vs_prev.
        same_unit = s != "hotspot_found"
        base = base_repo if unit == "repo" else base_hotspot
        out["steps"].append({
            "step": s,
            "help": STEP_HELP[s],
            "n": n,
            "denominator_prev": prev if same_unit else None,
            "ratio_vs_prev": (n / prev) if (prev and same_unit) else None,
            "baseline_unit": base,
            "ratio_vs_first": (n / base) if base else None,
            "unit": unit,
        })
        prev = n
    out["n_vacuous"] = sum(len(f.vacuous) for f in funnels)
    out["n_confounded"] = sum(len(f.confounded) for f in funnels)
    return out


def format_funnel_table(agg: dict, per_repo: list[RepoFunnel] | None = None) -> str:
    """Bảng phễu dạng text cho báo cáo."""
    lines: list[str] = []
    lines.append("PHỄU THỰC NGHIỆM (mỗi bước là tập con của bước trước)")
    lines.append("=" * 104)
    header = f"{'bước':<18}{'đơn vị':<9}{'n':>6}{'mẫu số':>8}{'vs bước trước':>15}{'vs mốc':>10}  giải thích"
    lines.append(header)
    lines.append("-" * len(header))
    for row in agg.get("steps", []):
        prev = row["denominator_prev"]
        rp = row["ratio_vs_prev"]
        rf = row["ratio_vs_first"]
        lines.append(
            f"{row['step']:<18}{row['unit']:<9}{row['n']:>6}"
            f"{('-' if prev is None else str(prev)):>8}"
            f"{('-' if rp is None else f'{rp * 100:.0f}%'):>15}"
            f"{('-' if rf is None else f'{rf * 100:.0f}%'):>10}"
            f"  {row['help']}"
        )
    lines.append("")
    lines.append(
        f"Nhãn phụ: VACUOUS = {agg.get('n_vacuous', 0)} hotspot "
        "(test repo xanh nhưng hàm Rust KHÔNG được gọi -> loại khỏi tỉ lệ "
        f"regression-free) | CONFOUNDED = {agg.get('n_confounded', 0)} hotspot "
        "(prompt bị cắt ở ít nhất 1 nhánh -> tách khỏi so sánh ablation)."
    )
    lines.append(
        "Tỉ lệ 'vs bước trước' để trống ở `hotspot_found` vì đổi đơn vị "
        "(repo -> hotspot). 'vs mốc' dùng mốc RIÊNG theo đơn vị: bước đơn vị "
        "repo so với tổng số repo, bước đơn vị hotspot so với `hotspot_found`."
    )

    if per_repo:
        lines.append("")
        lines.append("PHỄU THEO TỪNG REPO")
        lines.append("=" * 104)
        h2 = f"{'repo':<26}{'base':>6}" + "".join(f"{s[:9]:>11}" for s in HOTSPOT_STEPS)
        lines.append(h2)
        lines.append("-" * len(h2))
        for f in per_repo:
            lines.append(
                f"{f.label[:25]:<26}{('yes' if f.baseline_pass else 'NO'):>6}"
                + "".join(f"{f.count(s):>11}" for s in HOTSPOT_STEPS)
            )
        lines.append("")
        lines.append("LÝ DO LOẠI TỪNG HOTSPOT")
        lines.append("-" * 104)
        any_drop = False
        for f in per_repo:
            for name, why in f.drop_reasons.items():
                any_drop = True
                lines.append(f"  {f.label}/{name}: {why}")
        if not any_drop:
            lines.append("  (không hotspot nào bị loại)")
    return "\n".join(lines)
