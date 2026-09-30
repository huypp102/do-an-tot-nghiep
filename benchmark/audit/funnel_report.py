"""LẦN CHẠY CHẨN ĐOÁN, mục E -- báo cáo NGẮN, đây là thứ DUY NHẤT người đọc
cần mở (không đọc JSON thô). Gộp nhiều repo_summary_*.json thành 1 file
markdown (+ CSV cho bảng ngưỡng, dễ mở lại bằng spreadsheet).

Cách chạy:
    python audit/funnel_report.py --results-dir results/run_20261001_120000
    python audit/funnel_report.py --results-dir results/run_XXX --out audit/report.md

4 PHẦN (đúng đặc tả mục E):
  1. Phễu theo giai đoạn: số hàm còn lại sau mỗi giai đoạn + lý do loại.
  2. Bảng ngưỡng: mỗi quy tắc chặn bao nhiêu hàm, và đổi ngưỡng thì đổi bn.
  3. Hàm được CHẤP NHẬN nhưng SẼ bị loại bởi quy tắc bóng (B1-B4, mục B7).
  4. Bảng vòng sửa: số hàm biên dịch được ở vòng 0..max_retries.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCHMARK_ROOT))

from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

import funnel as funnel_mod  # noqa: E402
import outcomes  # noqa: E402
from audit.shadow_rules import B3_MIN_DISTINCT_INPUTS  # noqa: E402


def load_repo_summaries(results_dir: Path) -> list[dict]:
    """Đọc mọi `repo_summary_*.json` trực tiếp trong `results_dir` (không đệ
    quy -- mỗi lượt chạy thật ghi phẳng vào 1 thư mục run, xem
    run_experiment1.py::run_main_phase)."""
    out: list[dict] = []
    for p in sorted(results_dir.glob("repo_summary_*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[funnel_report] bỏ qua {p.name} (đọc lỗi: {exc})", file=sys.stderr)
    return out


# --------------------------------------------------------------- Phần 1
def section_funnel(summaries: list[dict]) -> str:
    funnels = []
    for s in summaries:
        rf = funnel_mod.RepoFunnel(label=s.get("label", "?"))
        baseline = (s.get("stages") or {}).get("baseline_tests") or {}
        rf.baseline_pass = bool(baseline.get("n_passed"))
        for h in s.get("hotspots", []):
            name = h.get("function", "?")
            rf.hotspots[name] = funnel_mod.funnel_from_record(_DictRecord(h))
            reason = h.get("reason")
            if reason and reason != outcomes.MEASURED:
                rf.drop_reasons[name] = f"{reason}: {(h.get('detail') or '')[:160]}"
            if h.get("vacuous"):
                rf.vacuous.add(name)
            if h.get("confounded"):
                rf.confounded.add(name)
        funnels.append(rf)
    agg = funnel_mod.aggregate(funnels)
    table = funnel_mod.format_funnel_table(agg, funnels)
    return "```\n" + table + "\n```\n"


class _DictRecord:
    """`funnel.funnel_from_record()` đọc thuộc tính của `HotspotRecord` --
    bọc dict đã đọc lại từ JSON thành object có thuộc tính tương đương, để
    KHÔNG phải import lại toàn bộ repo_pipeline.py (nặng, cần venv riêng)."""

    def __init__(self, h: dict):
        self.reason = h.get("reason")
        self.detail = h.get("detail", "")
        self.n_captured_calls = h.get("n_captured_calls", 0)
        self.tier = h.get("tier", "")
        self.compiled = h.get("compiled")
        self.build_status = h.get("build_status", "")
        self.correctness = h.get("correctness") or {}
        self.regression_free_contrib = h.get("regression_free_contrib", False)
        self.accepted_round = h.get("accepted_round")
        self.vacuous = h.get("vacuous", False)


# --------------------------------------------------------------- Phần 2
def section_threshold_table(summaries: list[dict]) -> tuple[str, list[dict]]:
    """Trả về (markdown, rows) -- `rows` để ghi CSV riêng."""
    all_hotspots = [h for s in summaries for h in s.get("hotspots", [])]
    n_total = len(all_hotspots)

    reason_counts = Counter(h.get("reason") or outcomes.MEASURED for h in all_hotspots)
    rows: list[dict] = []
    for reason in outcomes.HOTSPOT_REASONS:
        n = reason_counts.get(reason, 0)
        if n == 0 and reason != outcomes.MEASURED:
            continue
        rows.append({
            "rule": reason, "n_blocked": n,
            "pct_of_total": round(100 * n / n_total, 1) if n_total else 0.0,
            "help": outcomes.REASON_HELP.get(reason, ""),
            "sensitivity": "",
        })

    # --- Nhạy theo top_k_translate: rút rank FuncRank từ detail đã ghi sẵn
    # ("xếp hạng FuncRank #N") -- KHÔNG chạy lại FuncRank, chỉ đọc số đã có.
    import re

    ranks = []
    for h in all_hotspots:
        if h.get("reason") != outcomes.EXCLUDED_BY_TOP_K:
            continue
        m = re.search(r"FuncRank #(\d+)", h.get("detail") or "")
        if m:
            ranks.append(int(m.group(1)))
    if ranks:
        n_blocked_now = sum(1 for h in all_hotspots if h.get("reason") == outcomes.EXCLUDED_BY_TOP_K)
        for k in (3, 5, 8, 10, 15):
            n_would_cut = sum(1 for r in ranks if r > k)
            delta = n_would_cut - n_blocked_now
            rows.append({
                "rule": f"top_k_translate={k}", "n_blocked": n_would_cut,
                "pct_of_total": round(100 * n_would_cut / n_total, 1) if n_total else 0.0,
                "help": "mô phỏng offline từ rank FuncRank đã ghi -- không chạy lại FuncRank",
                "sensitivity": (
                    "mốc thật (top_k_translate=5)" if k == 5
                    else f"{delta:+d} hàm so với mốc thật"
                ),
            })

    # --- Nhạy theo ngưỡng B3 (số đầu vào khác nhau tối thiểu).
    accepted = [h for h in all_hotspots if h.get("accepted_round") is not None]
    for threshold in (1, 2, 3, 4, 5):
        n_would_flag = sum(
            1 for h in accepted
            if h.get("n_distinct_inputs") is not None and h["n_distinct_inputs"] < threshold
        )
        rows.append({
            "rule": f"B3_min_distinct_inputs={threshold}", "n_blocked": n_would_flag,
            "pct_of_total": round(100 * n_would_flag / len(accepted), 1) if accepted else 0.0,
            "help": "trong SỐ HÀM ĐÃ ACCEPT -- mốc thật dùng trong B7 là "
                    f"{B3_MIN_DISTINCT_INPUTS}",
            "sensitivity": "mốc thật" if threshold == B3_MIN_DISTINCT_INPUTS else "",
        })

    lines = ["| quy tắc | chặn bao nhiêu hàm | % / tổng | ghi chú |",
             "|---|---:|---:|---|"]
    for r in rows:
        note = r["help"] + (f" ({r['sensitivity']})" if r["sensitivity"] else "")
        lines.append(f"| {r['rule']} | {r['n_blocked']} | {r['pct_of_total']}% | {note} |")
    return "\n".join(lines) + "\n", rows


# --------------------------------------------------------------- Phần 3
def section_shadow_accepted(summaries: list[dict]) -> str:
    rows = []
    for s in summaries:
        for h in s.get("hotspots", []):
            wr = h.get("shadow_would_reject") or []
            if h.get("accepted_round") is not None and wr:
                rows.append((s.get("label", "?"), h.get("function", "?"), wr))
    if not rows:
        return "(không hàm nào được chấp nhận mà quy tắc bóng nào loại nó)\n"
    lines = ["| repo | hàm | quy tắc bóng sẽ loại |", "|---|---|---|"]
    for repo, fn, reasons in rows:
        lines.append(f"| {repo} | {fn} | {'; '.join(reasons)} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- Phần 4
def section_compile_rounds(summaries: list[dict]) -> str:
    hist: Counter[str] = Counter()
    for s in summaries:
        for h in s.get("hotspots", []):
            if h.get("compiled") is None and h.get("compile_attempts", 0) == 0:
                continue  # chưa từng đưa vào Stage 5 (vd bị loại trước đó)
            key = "null" if h.get("compiled_at_round") is None else str(h["compiled_at_round"])
            hist[key] += 1
    if not hist:
        return "(không có hotspot nào qua Stage 5 trong lượt này)\n"
    rounds_sorted = sorted(
        (k for k in hist if k != "null"), key=int
    ) + (["null"] if "null" in hist else [])
    lines = ["| vòng biên dịch được | số hàm |", "|---|---:|"]
    for k in rounds_sorted:
        label = "không bao giờ" if k == "null" else k
        lines.append(f"| {label} | {hist[k]} |")
    return "\n".join(lines) + "\n"


def build_report(summaries: list[dict]) -> tuple[str, list[dict]]:
    threshold_md, threshold_rows = section_threshold_table(summaries)
    n_repos = len(summaries)
    n_hotspots = sum(len(s.get("hotspots", [])) for s in summaries)
    parts = [
        "# Báo cáo chẩn đoán -- lần chạy 4",
        "",
        f"{n_repos} repo, {n_hotspots} hotspot. Đây là thứ DUY NHẤT cần đọc -- "
        "không đọc JSON thô (`repo_summary_*.json`) trừ khi cần truy ngược 1 "
        "trường hợp cụ thể.",
        "",
        "## 1. Phễu theo giai đoạn",
        "",
        section_funnel(summaries),
        "## 2. Bảng ngưỡng (mỗi quy tắc chặn bao nhiêu hàm)",
        "",
        threshold_md,
        "",
        "## 3. Hàm được CHẤP NHẬN nhưng quy tắc bóng (B1-B4) sẽ loại",
        "",
        section_shadow_accepted(summaries),
        "## 4. Bảng vòng sửa lỗi biên dịch",
        "",
        section_compile_rounds(summaries),
    ]
    return "\n".join(parts), threshold_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=str,
                         help="thư mục run (chứa repo_summary_*.json)")
    parser.add_argument("--out", type=str, default=None,
                         help="mặc định: <results-dir>/funnel_report.md")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.is_dir():
        print(f"[funnel_report] không phải thư mục: {results_dir}", file=sys.stderr)
        return 1

    summaries = load_repo_summaries(results_dir)
    if not summaries:
        print(f"[funnel_report] không thấy repo_summary_*.json nào trong {results_dir}",
              file=sys.stderr)
        return 1

    report_md, threshold_rows = build_report(summaries)

    out_path = Path(args.out) if args.out else results_dir / "funnel_report.md"
    out_path.write_text(report_md, encoding="utf-8")

    csv_path = out_path.with_suffix(".thresholds.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["rule", "n_blocked", "pct_of_total", "help", "sensitivity"],
        )
        writer.writeheader()
        writer.writerows(threshold_rows)

    print(f"[funnel_report] đã ghi {out_path} và {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
