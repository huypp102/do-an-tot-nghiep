"""PHẦN 2 -- Ablation: so sánh THEO CẶP hai nhánh `graph` và `none`.

CÂU HỎI: context lân cận PCG/PSG có thật sự giúp Generator sinh code Rust tốt
hơn, hay chỉ làm prompt dài ra?

THIẾT KẾ (theo lối đối chứng của POLO, một vòng):
  * Hai nhánh chỉ khác ĐÚNG MỘT thứ: có/không khối context lân cận trong
    prompt. Mọi thứ khác dùng chung -- cùng danh sách hotspot, cùng đối số
    phát lại, cùng 2 model, cùng temperature, cùng `MAX_COMPILE_RETRIES`.
  * `optimization_loop.max_rounds = 1` ở cả hai nhánh: đa vòng sẽ trộn tác
    động của vòng tối ưu vào tác động của context, không tách ra được nữa.
  * Bước ghi đối số và bộ test baseline chạy MỘT lần mỗi repo, dùng chung --
    chúng không phụ thuộc prompt nên chạy hai lần chỉ thêm nhiễu và tốn giờ GPU.

SO SÁNH THEO CẶP, không so hai tỉ lệ rời rạc: mỗi hotspot là một cặp
(kết quả nhánh graph, kết quả nhánh none). So tỉ lệ tổng sẽ bị nhiễu bởi việc
hai nhánh có tập hotspot thành công khác nhau; so theo cặp loại được nhiễu đó.

HAI PHÉP TÁCH BẮT BUỘC, nếu thiếu thì kết luận sai:
  * CONFOUNDED -- prompt bị cắt vì vượt `num_ctx` ở ÍT NHẤT MỘT nhánh. Nhánh
    `graph` dài hơn nên bị cắt nhiều hơn; không tách ra thì "graph tệ hơn" có
    thể chỉ là "graph bị cắt mất system prompt".
  * VACUOUS -- bộ test repo không hề gọi hàm Rust, nên `regression_free` của
    hotspot đó không chứng minh gì (xem funnel.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.ablation")

ARM_GRAPH = "graph"
ARM_NONE = "none"
ARM_GRAPH_REPEAT = "graph2"
"""PHA 5 -- NHIỄU NỀN. Chạy lại CHÍNH nhánh `graph` một lượt độc lập thứ hai
(cùng prompt, seed khác) để đo mức bất đồng do NGẪU NHIÊN của LLM.

VÌ SAO BẮT BUỘC CÓ: nếu graph-vs-none bất đồng ở 3 hotspot, con số đó vô nghĩa
khi chưa biết graph-vs-graph cũng bất đồng ở 3 hotspot. Không có nhiễu nền thì
mọi chênh lệch quan sát được đều có thể chỉ là LLM trả lời khác nhau giữa hai
lần gọi -- và đó là kết luận sai kiểu khó phát hiện nhất, vì nó trông như có
phát hiện."""

DEFAULT_ARMS = (ARM_GRAPH, ARM_NONE)
ALL_ARMS = (ARM_GRAPH, ARM_NONE, ARM_GRAPH_REPEAT)

# Các chỉ số được so theo cặp. Thứ tự = thứ tự trong phễu.
PAIRED_METRICS = ("compiled", "correct_fn", "regression_free", "accepted")


def is_enabled(cfg: dict) -> bool:
    return bool((cfg.get("ablation") or {}).get("enabled", False))


def arms(cfg: dict) -> list[str]:
    """Danh sách nhánh sẽ chạy, kể cả nhánh NHIỄU NỀN nếu bật."""
    ab = cfg.get("ablation") or {}
    configured = ab.get("arms") or list(DEFAULT_ARMS)
    unknown = [a for a in configured if a not in ALL_ARMS]
    if unknown:
        logger.warning(
            "ablation.arms có nhánh không hiểu: %s -- chỉ hỗ trợ %s, bỏ qua phần lạ.",
            unknown, list(ALL_ARMS),
        )
    out = [a for a in configured if a in ALL_ARMS] or list(DEFAULT_ARMS)
    if noise_floor_enabled(cfg) and ARM_GRAPH in out and ARM_GRAPH_REPEAT not in out:
        # Đặt CUỐI: nhánh này chỉ có nghĩa khi đã có nhánh graph để so.
        out.append(ARM_GRAPH_REPEAT)
    return out


def noise_floor_enabled(cfg: dict) -> bool:
    return bool((cfg.get("ablation") or {}).get("noise_floor", False))


def noise_floor_max_hotspots(cfg: dict) -> int:
    """Giới hạn số hotspot chạy lượt nhiễu nền. Mỗi hotspot là một lời gọi LLM
    nữa, nên giới hạn để không nhân đôi hoá đơn GPU cho cả dataset."""
    return int((cfg.get("ablation") or {}).get("noise_floor_max_hotspots", 10))


def includes_graph_context(arm: str) -> bool:
    """Cờ DUY NHẤT phân biệt các nhánh.

    `graph2` (nhiễu nền) là bản LẶP LẠI của `graph`, nên nó CÓ context -- prompt
    của nó phải giống `graph` từng ký tự, chỉ khác seed.
    """
    return arm != ARM_NONE


def seed_for(base_seed: int, repo: str, hotspot: str, arm: str, repeat: int) -> int:
    """Seed TẤT ĐỊNH cho một (repo, hotspot, arm, repeat).

    Dùng SHA-256 thay cho `hash()` của Python: `hash()` của str bị ngẫu nhiên
    hoá theo tiến trình (PYTHONHASHSEED), nên cùng cấu hình chạy lại sẽ ra seed
    khác -- đúng thứ phá tính lặp lại mà ablation cần.

    Hai nhánh CỐ Ý nhận seed khác nhau: cùng seed mà prompt khác nhau thì
    không có ý nghĩa gì (seed chỉ cố định dòng ngẫu nhiên cho MỘT prompt), còn
    ràng buộc cần có là "cùng (hotspot, arm, repeat) thì luôn ra cùng seed" --
    tức là chạy lại cho kết quả như cũ.
    """
    key = f"{base_seed}|{repo}|{hotspot}|{arm}|{repeat}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big")


def ext_module_suffix(arm: str) -> str:
    """Hậu tố tên thư mục/crate theo nhánh.

    CHỈ dùng cho đường dẫn trên đĩa, TUYỆT ĐỐI không đưa vào prompt: tên module
    xuất hiện trong prompt, nên nếu khác nhau giữa hai nhánh thì hai prompt sẽ
    khác nhau ở hai biến chứ không phải một.
    """
    return f"_{arm}" if arm else ""


@dataclass
class ArmResult:
    """Kết quả của MỘT nhánh cho MỘT hotspot."""

    arm: str
    flags: dict = field(default_factory=dict)      # {bước phễu: bool}
    reason: str = ""
    prompt_tokens: int | None = None
    truncated: bool = False
    fix_rounds: int = 0
    llm_seconds: float | None = None
    vacuous: bool = False
    speedup: dict = field(default_factory=dict)


@dataclass
class PairedComparison:
    """Bảng 2×2 cho MỘT chỉ số: graph đúng/sai × none đúng/sai."""

    metric: str
    both_ok: int = 0
    graph_only: int = 0      # graph đúng, none sai
    none_only: int = 0       # none đúng, graph sai
    both_fail: int = 0

    @property
    def n_pairs(self) -> int:
        return self.both_ok + self.graph_only + self.none_only + self.both_fail

    @property
    def n_discordant(self) -> int:
        """Số cặp BẤT ĐỒNG -- đây là cỡ mẫu thật của phép so sánh. Cặp mà cả
        hai nhánh giống nhau không mang thông tin nào về chênh lệch."""
        return self.graph_only + self.none_only

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "both_ok": self.both_ok,
            "graph_only": self.graph_only,
            "none_only": self.none_only,
            "both_fail": self.both_fail,
            "n_pairs": self.n_pairs,
            "n_discordant": self.n_discordant,
        }


def compare_two_arms(
    left_side: dict[str, ArmResult],
    right_side: dict[str, ArmResult],
    left_name: str,
    right_name: str,
) -> dict:
    """So sánh THEO CẶP hai nhánh bất kỳ. Dùng cho cả graph-vs-none lẫn
    graph-vs-graph2 (nhiễu nền) -- CÙNG một hàm để hai con số so được với nhau;
    hai hàm riêng là cách chắc chắn nhất để chúng lệch nhau theo thời gian."""
    common = sorted(set(left_side) & set(right_side))
    confounded = [
        n for n in common if left_side[n].truncated or right_side[n].truncated
    ]
    usable = [n for n in common if n not in confounded]
    generated_both = [
        n for n in usable
        if left_side[n].flags.get("generated") and right_side[n].flags.get("generated")
    ]

    comparisons: dict[str, PairedComparison] = {}
    for metric in PAIRED_METRICS:
        pc = PairedComparison(metric=metric)
        for n in generated_both:
            l = bool(left_side[n].flags.get(metric))
            r = bool(right_side[n].flags.get(metric))
            if metric == "regression_free" and (
                left_side[n].vacuous or right_side[n].vacuous
            ):
                continue
            if l and r:
                pc.both_ok += 1
            elif l and not r:
                pc.graph_only += 1      # "left đúng, right sai"
            elif r and not l:
                pc.none_only += 1       # "right đúng, left sai"
            else:
                pc.both_fail += 1
        comparisons[metric] = pc

    return {
        "left": left_name,
        "right": right_name,
        "n_common": len(common),
        "n_confounded": len(confounded),
        "confounded": confounded,
        "n_generated_both": len(generated_both),
        "generated_both": generated_both,
        "comparisons": {m: pc.as_dict() for m, pc in comparisons.items()},
    }


def build_pairs(
    per_arm: dict[str, dict[str, ArmResult]],
    min_discordant: int = 10,
) -> dict:
    """So sánh theo cặp trên các hotspot có mặt ở CẢ HAI nhánh và không CONFOUNDED.

    `per_arm`: {arm: {tên hotspot: ArmResult}}.
    """
    graph_side = per_arm.get(ARM_GRAPH) or {}
    none_side = per_arm.get(ARM_NONE) or {}

    common = sorted(set(graph_side) & set(none_side))
    # CONFOUNDED: bị cắt prompt ở BẤT KỲ nhánh nào.
    confounded = [
        n for n in common
        if graph_side[n].truncated or none_side[n].truncated
    ]
    usable = [n for n in common if n not in confounded]

    # Chỉ so trên hotspot đã SINH ĐƯỢC CODE ở cả hai nhánh: hotspot mà một
    # nhánh còn chưa ra code thì không có gì để so về compile/correct.
    generated_both = [
        n for n in usable
        if graph_side[n].flags.get("generated") and none_side[n].flags.get("generated")
    ]

    comparisons: dict[str, PairedComparison] = {}
    for metric in PAIRED_METRICS:
        pc = PairedComparison(metric=metric)
        for n in generated_both:
            g = bool(graph_side[n].flags.get(metric))
            o = bool(none_side[n].flags.get(metric))
            # VACUOUS chỉ làm mất hiệu lực của regression_free, không của
            # compile/correctness -- nên chỉ loại ở đúng chỉ số đó.
            if metric == "regression_free" and (
                graph_side[n].vacuous or none_side[n].vacuous
            ):
                continue
            if g and o:
                pc.both_ok += 1
            elif g and not o:
                pc.graph_only += 1
            elif o and not g:
                pc.none_only += 1
            else:
                pc.both_fail += 1
        comparisons[metric] = pc

    def _avg(arm_side: dict, attr: str, names: list[str]) -> float | None:
        vals = [getattr(arm_side[n], attr) for n in names]
        vals = [v for v in vals if v is not None]
        return (sum(vals) / len(vals)) if vals else None

    return {
        "n_common": len(common),
        "n_confounded": len(confounded),
        "confounded": confounded,
        "n_generated_both": len(generated_both),
        "generated_both": generated_both,
        "min_discordant_pairs": min_discordant,
        "comparisons": {m: pc.as_dict() for m, pc in comparisons.items()},
        "per_arm_summary": {
            arm: {
                "n_hotspots": len(side),
                "avg_prompt_tokens": _avg(side, "prompt_tokens", sorted(side)),
                "avg_fix_rounds": _avg(side, "fix_rounds", sorted(side)),
                "avg_llm_seconds": _avg(side, "llm_seconds", sorted(side)),
                "n_truncated": sum(1 for n in side if side[n].truncated),
                "n_generated": sum(1 for n in side if side[n].flags.get("generated")),
                "n_accepted": sum(1 for n in side if side[n].flags.get("accepted")),
            }
            for arm, side in per_arm.items()
        },
        # --- PHA 5: NHIỄU NỀN ------------------------------------------------
        "noise_floor": _noise_floor_block(per_arm, comparisons, min_discordant),
    }


def _noise_floor_block(
    per_arm: dict[str, dict[str, ArmResult]],
    effect_comparisons: dict[str, PairedComparison],
    min_discordant: int,
) -> dict:
    """So graph-vs-graph2 (nhiễu) với graph-vs-none (hiệu ứng).

    Kết luận CHỈ được rút ra khi số cặp bất đồng của hiệu ứng LỚN HƠN của nhiễu.
    Bằng nhau hoặc nhỏ hơn -> `KHÔNG PHÂN BIỆT ĐƯỢC VỚI NHIỄU`, và điều đó phải
    được nói thẳng chứ không để người đọc tự suy từ hai bảng rời.
    """
    graph_side = per_arm.get(ARM_GRAPH) or {}
    repeat_side = per_arm.get(ARM_GRAPH_REPEAT) or {}
    if not repeat_side:
        return {
            "available": False,
            "note": (
                "Chưa chạy nhánh nhiễu nền (ablation.noise_floor=false). Không có "
                "nhiễu nền thì KHÔNG biết chênh lệch graph-vs-none có vượt mức "
                "ngẫu nhiên của LLM hay không -- mọi kết luận về tác động của "
                "context đều chưa có cơ sở."
            ),
        }

    noise = compare_two_arms(graph_side, repeat_side, ARM_GRAPH, ARM_GRAPH_REPEAT)
    per_metric: dict[str, dict] = {}
    for metric in PAIRED_METRICS:
        eff = effect_comparisons[metric].n_discordant
        noi = (noise["comparisons"].get(metric) or {}).get("n_discordant", 0)
        if eff > noi:
            verdict = "HIỆU ỨNG VƯỢT NHIỄU"
        elif noise["n_generated_both"] == 0:
            verdict = "KHÔNG ĐO ĐƯỢC NHIỄU (nhánh lặp không sinh được code)"
        else:
            verdict = "KHÔNG PHÂN BIỆT ĐƯỢC VỚI NHIỄU"
        per_metric[metric] = {
            "n_discordant_effect": eff,
            "n_discordant_noise": noi,
            "verdict": verdict,
            "conclusive": eff > noi and eff >= min_discordant,
        }

    return {
        "available": True,
        "comparison": noise,
        "per_metric": per_metric,
        "n_hotspots_repeated": len(repeat_side),
    }


def format_report(paired: dict, cfg: dict | None = None) -> str:
    """Báo cáo ablation dạng text."""
    min_disc = paired.get("min_discordant_pairs", 10)
    lines: list[str] = []
    lines.append("ABLATION: CÓ vs KHÔNG CÓ CONTEXT GRAPH")
    lines.append("=" * 92)
    lines.append(
        "Hai nhánh chỉ khác đúng một thứ: khối context lân cận PCG/PSG trong "
        "prompt của Generator."
    )
    lines.append(
        "Cả hai nhánh đều có: source hotspot, chữ ký, kiểu đối số quan sát "
        "được, ví dụ vào/ra từ test."
    )
    lines.append("")
    lines.append(f"  hotspot có ở cả 2 nhánh        : {paired.get('n_common', 0)}")
    lines.append(
        f"  bị CONFOUNDED (prompt bị cắt) : {paired.get('n_confounded', 0)}"
        + (f"  -> {paired.get('confounded')}" if paired.get("confounded") else "")
    )
    lines.append(f"  sinh được code ở CẢ 2 nhánh   : {paired.get('n_generated_both', 0)}")
    lines.append("")

    header = (
        f"{'chỉ số':<18}{'cặp':>6}{'cả 2 đúng':>11}{'graph đúng/':>13}"
        f"{'none đúng/':>12}{'cả 2 sai':>10}{'bất đồng':>10}"
    )
    lines.append(header)
    lines.append(
        f"{'':<18}{'':>6}{'':>11}{'none sai':>13}{'graph sai':>12}{'':>10}{'':>10}"
    )
    lines.append("-" * len(header))
    for metric in PAIRED_METRICS:
        c = (paired.get("comparisons") or {}).get(metric) or {}
        lines.append(
            f"{metric:<18}{c.get('n_pairs', 0):>6}{c.get('both_ok', 0):>11}"
            f"{c.get('graph_only', 0):>13}{c.get('none_only', 0):>12}"
            f"{c.get('both_fail', 0):>10}{c.get('n_discordant', 0):>10}"
        )
    lines.append("")

    # --- Cỡ mẫu: nói thẳng khi chưa đủ để kết luận -------------------------
    worst = max(
        ((paired.get("comparisons") or {}).get(m) or {}).get("n_discordant", 0)
        for m in PAIRED_METRICS
    ) if PAIRED_METRICS else 0
    if worst < min_disc:
        lines.append(
            f"CỠ MẪU NHỎ: số cặp bất đồng lớn nhất là {worst} < {min_disc}. "
            "KHÔNG kết luận thống kê nào được rút ra từ bảng này -- các con số "
            "trên chỉ là SỐ ĐẾM mô tả. Muốn kết luận thì cần thêm repo hoặc "
            "tăng `ablation.repeats`."
        )
    else:
        lines.append(
            f"Số cặp bất đồng đạt ngưỡng {min_disc}. Có thể áp kiểm định "
            "McNemar trên cặp (graph_only, none_only) cho từng chỉ số; báo cáo "
            "này chỉ cung cấp số đếm, việc kiểm định làm ở bước phân tích."
        )
    lines.append("")

    # --- PHA 5: NHIỄU NỀN, đặt NGAY CẠNH bảng hiệu ứng -------------------
    nf = paired.get("noise_floor") or {}
    lines.append("NHIỄU NỀN: graph vs graph2 (cùng prompt, seed khác)")
    lines.append("-" * 92)
    if not nf.get("available"):
        lines.append("  " + str(nf.get("note", "chưa chạy")))
    else:
        cmp_ = nf.get("comparison") or {}
        lines.append(
            f"  hotspot chạy lặp: {nf.get('n_hotspots_repeated', 0)} | "
            f"sinh được code ở cả 2 lượt: {cmp_.get('n_generated_both', 0)}"
        )
        h = (
            f"{'chỉ số':<18}{'bất đồng HIỆU ỨNG':>20}{'bất đồng NHIỄU':>17}"
            f"   kết luận"
        )
        lines.append(h)
        lines.append("-" * len(h))
        for metric in PAIRED_METRICS:
            row = (nf.get("per_metric") or {}).get(metric) or {}
            lines.append(
                f"{metric:<18}{row.get('n_discordant_effect', 0):>20}"
                f"{row.get('n_discordant_noise', 0):>17}   {row.get('verdict', '?')}"
            )
        lines.append("")
        lines.append(
            "  'bất đồng HIỆU ỨNG' = graph vs none. 'bất đồng NHIỄU' = graph vs "
            "graph2 (chính nó, seed khác)."
        )
        lines.append(
            "  Hiệu ứng KHÔNG lớn hơn nhiễu nghĩa là chênh lệch quan sát được có "
            "thể chỉ do LLM trả lời khác nhau giữa hai lần gọi -- KHÔNG kết luận "
            "gì về tác động của context."
        )
    lines.append("")

    lines.append("SỐ LIỆU TỪNG NHÁNH")
    lines.append("-" * 92)
    h2 = (
        f"{'nhánh':<10}{'hotspot':>9}{'sinh được':>11}{'accepted':>10}"
        f"{'token prompt TB':>17}{'vòng sửa TB':>13}{'giây LLM TB':>13}{'bị cắt':>8}"
    )
    lines.append(h2)
    lines.append("-" * len(h2))
    for arm, side in (paired.get("per_arm_summary") or {}).items():
        def _f(key, fmt="{:.1f}"):
            v = side.get(key)
            return "n/a" if v is None else fmt.format(v)
        lines.append(
            f"{arm:<10}{side.get('n_hotspots', 0):>9}{side.get('n_generated', 0):>11}"
            f"{side.get('n_accepted', 0):>10}{_f('avg_prompt_tokens', '{:.0f}'):>17}"
            f"{_f('avg_fix_rounds'):>13}{_f('avg_llm_seconds', '{:.1f}'):>13}"
            f"{side.get('n_truncated', 0):>8}"
        )
    lines.append("")
    lines.append(
        "Prompt đầy đủ của cả hai nhánh được lưu trong <work_dir>/.rtb_prompts/ "
        "-- diff trực tiếp để kiểm chứng chúng chỉ khác khối context."
    )
    return "\n".join(lines)


def write_reports(paired: dict, results_dir: Path, timestamp: str, cfg: dict | None = None):
    """Ghi `ablation_report.md` + `ablation_report.json`."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    text = format_report(paired, cfg)
    md = results_dir / f"ablation_report_{timestamp}.md"
    js = results_dir / f"ablation_report_{timestamp}.json"
    md.write_text(text, encoding="utf-8")
    js.write_text(
        json.dumps(paired, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return md, js, text
