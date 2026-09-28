"""Tổng hợp kết quả benchmark thành bảng mean/median/std/speedup so với
python_pure (baseline)."""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from config_loader import ensure_utf8_stdio  # noqa: E402

# Phải gọi TRƯỚC mọi print() -- tránh UnicodeEncodeError khi in báo cáo tiếng
# Việt trên Windows console dùng codepage cp1252 mặc định.
ensure_utf8_stdio()

BASELINE_VERSION = "python_pure"


def summarize(durations: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(durations),
        "median": statistics.median(durations),
        "std": statistics.pstdev(durations) if len(durations) > 1 else 0.0,
        "n": len(durations),
    }


def build_report(all_results: dict[str, dict[str, list[float]] | None]) -> str:
    """all_results: {version: {function_name: [durations_sec]} | None}
    (None nghĩa là version đó bị bỏ qua, vd rust_pure chưa build)."""
    functions: list[str] = []
    for per_fn in all_results.values():
        if per_fn:
            for name in per_fn:
                if name not in functions:
                    functions.append(name)

    versions = list(all_results.keys())
    lines: list[str] = []
    lines.append("BENCHMARK REPORT: python_pure vs rust_pure vs hybrid_pyo3")
    lines.append("=" * 72)

    baseline = all_results.get(BASELINE_VERSION) or {}

    for fn_name in functions:
        lines.append(f"\n[{fn_name}]")
        header = f"{'version':<16}{'mean(ms)':>12}{'median(ms)':>12}{'std(ms)':>12}{'n':>6}{'speedup':>10}"
        lines.append(header)
        lines.append("-" * len(header))

        baseline_mean = None
        if fn_name in baseline:
            baseline_mean = summarize(baseline[fn_name])["mean"]

        for version in versions:
            per_fn = all_results.get(version)
            if not per_fn or fn_name not in per_fn:
                lines.append(f"{version:<16}{'(bỏ qua / chưa build)':>56}")
                continue
            s = summarize(per_fn[fn_name])
            speedup_str = "-"
            if baseline_mean and s["mean"] > 0:
                speedup_str = f"{baseline_mean / s['mean']:.2f}x"
            lines.append(
                f"{version:<16}{s['mean']*1000:>12.3f}{s['median']*1000:>12.3f}"
                f"{s['std']*1000:>12.3f}{s['n']:>6}{speedup_str:>10}"
            )

    lines.append("")
    lines.append(
        "Ghi chú: speedup = mean(python_pure) / mean(version). Với logic dummy "
        "hiện tại, số liệu này KHÔNG phản ánh tốc độ thuật toán thật -- chỉ để "
        "xác nhận pipeline đo lường chạy đúng end-to-end."
    )
    return "\n".join(lines)


def build_whole_scope_report(results_sec: dict[str, float | None], order: list[str]) -> str:
    """Báo cáo cho lượt đo WHOLE-SCOPE (chỉ có khi target.mode=file/repo):
    tổng thời gian gọi 1 LẦN toàn bộ chuỗi hàm top-K theo thứ tự topological
    của PCG, cho từng phiên bản. Khác `build_report` (per-function, có
    warmup + N lần lặp, có speedup) -- đây chỉ là 1 con số tổng / phiên bản.
    """
    lines: list[str] = []
    lines.append("WHOLE-SCOPE REPORT: gọi toàn bộ chuỗi hàm top-K 1 lần (thứ tự PCG topological)")
    lines.append("=" * 72)
    lines.append("Thứ tự gọi: " + " -> ".join(order) if order else "(rỗng)")
    lines.append("")

    baseline = results_sec.get(BASELINE_VERSION)
    header = f"{'version':<16}{'total(ms)':>14}{'speedup':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for version, seconds in results_sec.items():
        if seconds is None:
            lines.append(f"{version:<16}{'(bỏ qua / chưa build)':>24}")
            continue
        speedup_str = "-"
        if baseline and seconds > 0:
            speedup_str = f"{baseline / seconds:.2f}x"
        lines.append(f"{version:<16}{seconds*1000:>14.3f}{speedup_str:>10}")

    lines.append("")
    lines.append(
        "Ghi chú: đây là 1 lần chạy DUY NHẤT cho cả chuỗi (không warmup/lặp N "
        "lần như bảng per-function ở trên) -- mô phỏng use-case gọi hết các "
        "hàm trong 1 file/repo 1 lượt, khác với đo từng hàm rời rạc."
    )
    return "\n".join(lines)


def build_hotspot_summary(rows: list[dict]) -> str:
    """BẢNG TỔNG KẾT CHÍNH của pipeline -- đúng 4 chỉ số cho mỗi hotspot:

        correctness   output Rust có khớp Python không (MATCH/MISMATCH/ERROR)
        pass@1        Stage 5: biên dịch được NGAY lần đầu, không cần sửa
        speedup       python_pure / rust_pure (vòng tốt nhất)
        rounds        số vòng tối ưu tốc độ đã chạy (biết hàm nào dừng sớm,
                      hàm nào chạy hết trần optimization_loop.max_rounds)

    Các chỉ số chi tiết khác (DSR@1 theo từng vòng, phân loại lỗi biên dịch,
    ...) vẫn được tính và lưu trong JSON phụ, nhưng KHÔNG hiện ở bảng này để
    người đọc tập trung vào 4 con số quan trọng nhất.

    rows: list dict có khoá function, correctness, pass_at_1, speedup,
          rounds, stopped_by_cap.
    """
    lines: list[str] = []
    lines.append("TỔNG KẾT THEO HOTSPOT")
    lines.append("=" * 72)
    header = (
        f"{'hotspot':<22}{'correctness':>13}{'pass@1':>9}{'speedup':>10}{'rounds':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    if not rows:
        lines.append("(không có hotspot nào)")
        return "\n".join(lines)

    for r in rows:
        correctness = r.get("correctness") or "n/a"
        p1 = r.get("pass_at_1")
        p1_text = "n/a" if p1 is None else ("yes" if p1 else "no")
        sp = r.get("speedup")
        sp_text = "n/a" if sp is None else f"{sp:.2f}x"
        rounds = r.get("rounds")
        rounds_text = "n/a" if rounds is None else str(rounds)
        if r.get("stopped_by_cap"):
            rounds_text += "*"
        lines.append(
            f"{str(r.get('function', '?')):<22}{correctness:>13}{p1_text:>9}"
            f"{sp_text:>10}{rounds_text:>9}"
        )

    lines.append("")
    lines.append(
        "correctness: MATCH = output Rust khớp Python trên mọi sample "
        "(np.allclose rtol=1e-5 atol=1e-8); MISMATCH thì KHÔNG đo tốc độ."
    )
    lines.append(
        "pass@1: yes = Stage 5 biên dịch được ngay lần đầu, không cần Generator "
        "Agent sửa lần nào."
    )
    lines.append("rounds: số vòng tối ưu tốc độ đã chạy; dấu * = dừng vì chạm trần max_rounds.")
    return "\n".join(lines)


def build_round_table(hotspot_rows: list[dict]) -> str:
    """Bảng chi tiết THEO TỪNG VÒNG tối ưu, kèm `build_status`.

    Cột build_status cho biết số đo của vòng đó là trên code NÀO:
        INITIAL           vòng 1, đo trên extension đang có sẵn
        REBUILT_OK        đã build lại từ code Generator Agent vừa sinh
        BUILD_FAILED      build lại lỗi -> vòng lặp dừng, KHÔNG báo speedup
        SKIPPED_NO_CARGO  máy không có cargo/maturin -> không build lại được

    Cột này sinh ra để tránh lặp lại đúng lỗi đã gặp: trước đây code mới chỉ
    được ghi ra file nháp mà không build lại, nên mọi vòng đo lại đúng bản
    build cũ và cho speedup giống hệt nhau.
    """
    lines: list[str] = []
    lines.append("CHI TIẾT THEO VÒNG TỐI ƯU")
    lines.append("=" * 72)
    header = f"{'hotspot':<22}{'vòng':>6}{'build_status':>20}"
    lines.append(header)
    lines.append("-" * len(header))

    any_row = False
    for r in hotspot_rows:
        for entry in r.get("build_status_by_round") or []:
            any_row = True
            lines.append(
                f"{str(r.get('function', '?')):<22}{entry.get('round', '?'):>6}"
                f"{str(entry.get('build_status', '?')):>20}"
            )
    if not any_row:
        lines.append("(không có vòng tối ưu nào chạy)")

    lines.append("")
    lines.append(
        "Chỉ vòng có build_status=REBUILT_OK mới là số đo trên code MỚI sinh; "
        "INITIAL là bản build sẵn có trước khi tối ưu."
    )
    return "\n".join(lines)


def regenerate_latest(results_dir: Path | None = None) -> str:
    """Đọc file raw_*.json mới nhất trong results/ và in lại report (không
    cần chạy lại benchmark)."""
    d = results_dir or (BENCHMARK_ROOT / "results")
    # Pattern `*raw_*.json` (không phải `raw_*.json`): `bench.py` ghi
    # `raw_<ts>.json` còn `run_pipeline.py` ghi `pipeline_raw_<ts>.json`. Pattern
    # cũ neo vào đầu tên file nên không bao giờ thấy file của run_pipeline --
    # tiện ích này khi đó chỉ dùng được cho output của bench.py.
    raw_files = sorted(
        d.glob("*raw_*.json"), key=lambda p: (p.stat().st_mtime, p.name)
    )
    if not raw_files:
        raise FileNotFoundError(
            f"Không tìm thấy file *raw_*.json nào trong {d} "
            f"(bench.py ghi raw_<ts>.json, run_pipeline.py ghi pipeline_raw_<ts>.json)"
        )
    latest = raw_files[-1]
    with latest.open("r", encoding="utf-8") as f:
        all_results = json.load(f)
    table = build_report(all_results)
    print(f"(từ {latest.name})\n")
    print(table)
    return table


if __name__ == "__main__":
    regenerate_latest()


# ===========================================================================
# PHA F -- bảng cho chế độ REPO ĐỘNG.
#
# Khác các bảng ở trên (chế độ legacy, workload là 1 ảnh):
#   * speedup báo cáo là của VÒNG ĐƯỢC ACCEPT cuối cùng -- tức phiên bản sẽ
#     thật sự được dùng -- KHÔNG phải MAX qua các vòng (lỗ hổng #4). Best-of
#     vẫn được lưu nhưng ở khoá riêng, có nhãn rõ ràng.
#   * mọi hotspot đều có mặt kèm LÝ DO, kể cả hotspot không đo được -- chấm
#     dứt việc hotspot lặng lẽ biến mất khỏi bảng.
# ===========================================================================
def build_hotspot_reason_table(hotspots: list[dict]) -> str:
    """Bảng per-hotspot: lý do cuối cùng + tầng + correctness + speedup."""
    lines: list[str] = []
    lines.append("HOTSPOT: LÝ DO CUỐI CÙNG + KẾT QUẢ")
    lines.append("=" * 100)
    header = (
        f"{'hotspot':<20}{'lý do':<22}{'tầng':<12}{'corr rust/hyb':>15}"
        f"{'speedup accept':>16}{'vòng':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    if not hotspots:
        lines.append("(không có hotspot nào)")
        return "\n".join(lines)

    for h in hotspots:
        corr = h.get("correctness") or {}

        def _c(version: str, _corr=corr) -> str:
            entry = _corr.get(version) or {}
            return {"MATCH": "OK", "MISMATCH": "LỆCH", "ERROR": "ERR"}.get(
                entry.get("status"), "-")

        acc = h.get("accepted_speedup") or {}
        sp = acc.get("rust_pure", acc.get("hybrid_pyo3"))
        sp_text = "n/a" if sp is None else f"{sp:.2f}x"
        rounds = h.get("rounds") or []
        rounds_text = str(len(rounds)) + ("*" if h.get("stopped_by_cap") else "")
        tier = (h.get("tier") or "-").replace("TIER1_", "1:").replace("TIER2_", "2:")
        lines.append(
            f"{str(h.get('function', '?')):<20}{str(h.get('reason', '?')):<22}"
            f"{tier:<12}{_c('rust_pure') + '/' + _c('hybrid_pyo3'):>15}"
            f"{sp_text:>16}{rounds_text:>6}"
        )

    lines.append("")
    lines.append("lý do: chỉ MEASURED là đo được đầy đủ; giá trị khác giải thích vì sao bị loại.")
    lines.append("tầng: 1=kiểu gốc -> Rust thuần; 2=đối tượng -> kernel Rust + shim Python.")
    lines.append(
        "speedup accept: của VÒNG ĐƯỢC ACCEPT CUỐI CÙNG (phiên bản sẽ dùng thật), "
        "KHÔNG phải max qua các vòng."
    )
    lines.append("vòng: số vòng tối ưu đã chạy; * = dừng vì chạm trần max_rounds.")
    return "\n".join(lines)


def build_round_detail_table(hotspots: list[dict]) -> str:
    """Bảng THEO TỪNG VÒNG: mean/median/std của từng phiên bản + build_status."""
    lines: list[str] = []
    lines.append("CHI TIẾT THEO VÒNG (mean/median/std tính bằng ms)")
    lines.append("=" * 100)
    header = (
        f"{'hotspot':<18}{'vòng':>5}{'build_status':>18}{'phiên bản':<14}"
        f"{'mean':>10}{'median':>10}{'std':>9}{'n':>4}{'speedup':>9}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    any_row = False
    for h in hotspots:
        for entry in h.get("rounds") or []:
            stats = entry.get("stats_ms") or {}
            speedups = entry.get("speedup") or {}
            for version in ("python_pure", "rust_pure", "hybrid_pyo3"):
                s = stats.get(version)
                if not s:
                    continue
                any_row = True
                name = str(h.get("function", "?"))
                bstat = str(entry.get("build_status", "?"))
                rnd = entry.get("round", "?")
                if "error" in s:
                    lines.append(
                        f"{name:<18}{rnd:>5}{bstat:>18}{version:<14}"
                        f"{'(' + str(s['error'])[:36] + ')':>42}"
                    )
                    continue
                sp = speedups.get(version)
                sp_text = "baseline" if version == "python_pure" else (
                    "n/a" if sp is None else f"{sp:.2f}x")
                lines.append(
                    f"{name:<18}{rnd:>5}{bstat:>18}{version:<14}"
                    f"{s['mean_ms']:>10.4f}{s['median_ms']:>10.4f}"
                    f"{s['std_ms']:>9.4f}{s['n']:>4}{sp_text:>9}"
                )
    if not any_row:
        lines.append("(không vòng đo nào chạy được)")
    lines.append("")
    lines.append(
        "Cả 3 phiên bản trong cùng một vòng được đo trong CÙNG một tiến trình, "
        "CÙNG bộ đối số thật, CÙNG vòng lặp warmup+N -> tỉ số hợp lệ ở mọi vòng."
    )
    return "\n".join(lines)


def build_repo_table(repo_rows: list[dict]) -> str:
    """BẢNG CUỐI cho luận văn: MỘT DÒNG MỘT REPO.

    Đúng các cột yêu cầu ở Pha F: repo | status | số hotspot | MEASURED |
    compile_ok | correctness_match_rate (rust, hybrid) | baseline_tests |
    hybrid_tests | REGRESSION_FREE | speedup accept (rust, hybrid) | số vòng.
    """
    lines: list[str] = []
    lines.append("BẢNG KẾT QUẢ THEO REPO")
    lines.append("=" * 118)
    header = (
        f"{'repo':<22}{'status':<23}{'hs':>4}{'meas':>6}{'comp_ok':>8}"
        f"{'corr_rust':>10}{'corr_hyb':>9}{'base_test':>11}{'hyb_test':>10}"
        f"{'reg_free':>9}{'sp_rust':>9}{'sp_hyb':>8}{'vòng':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    def _pct(v) -> str:
        return "n/a" if v is None else f"{v * 100:.0f}%"

    def _x(v) -> str:
        return "n/a" if v is None else f"{v:.2f}x"

    for row in repo_rows:
        m = row.get("metrics") or {}
        corr = m.get("correctness_match_rate") or {}
        sp = m.get("mean_accepted_speedup") or {}
        rf = m.get("regression_free")
        rounds = m.get("mean_rounds")
        rounds_text = "n/a" if rounds is None else f"{rounds:.1f}"
        rf_text = "yes" if rf else ("NO" if rf is False else "n/a")
        lines.append(
            f"{str(row.get('label', '?'))[:21]:<22}"
            f"{str(row.get('repo_status', '?')):<23}"
            f"{m.get('n_hotspots', 0):>4}{m.get('n_measured', 0):>6}"
            f"{_pct(m.get('compile_ok')):>8}"
            f"{_pct(corr.get('rust_pure')):>10}{_pct(corr.get('hybrid_pyo3')):>9}"
            f"{str(m.get('baseline_tests') or 'n/a'):>11}"
            f"{str(m.get('hybrid_tests') or 'n/a'):>10}"
            f"{rf_text:>9}"
            f"{_x(sp.get('rust_pure')):>9}{_x(sp.get('hybrid_pyo3')):>8}"
            f"{rounds_text:>6}"
        )

    lines.append("")
    lines.append(
        "status: OK=mọi hotspot đo được | PARTIAL=một phần | "
        "NO_MEASURABLE_HOTSPOT=không đo được gì | BASELINE_FAILED=bộ test gốc đã "
        "fail sẵn (repo bị LOẠI khỏi so sánh) | INSTALL_FAILED | TIMEOUT."
    )
    lines.append(
        "reg_free (REGRESSION_FREE): tập test pass của hybrid CHỨA TOÀN BỘ tập "
        "pass của baseline. 'NO' = có hồi quy, xem `regressed_tests` trong JSON."
    )
    lines.append(
        "sp_*: trung bình speedup của vòng được ACCEPT, chỉ tính hotspot MEASURED."
    )
    return "\n".join(lines)


def build_dataset_metrics_table(repo_rows: list[dict]) -> str:
    """APR / SR mức DATASET theo định nghĩa RepoTransBench (arXiv:2412.17744).

    APR = TRUNG BÌNH tỉ lệ test pass qua các repo (mỗi repo một phiếu).
    SR  = tỉ lệ repo pass TOÀN BỘ test.

    Repo `BASELINE_FAILED` / `INSTALL_FAILED` bị LOẠI khỏi mẫu chứ không tính
    là 0: đó là lỗi môi trường hoặc lỗi có sẵn của repo, không phải chất lượng
    bản dịch. Tính là 0 sẽ kéo APR xuống vì lý do không liên quan.
    """
    counted = [
        r for r in repo_rows
        if (r.get("metrics") or {}).get("hybrid_pass_rate") is not None
    ]
    excluded = [r for r in repo_rows if r not in counted]

    lines: list[str] = []
    lines.append("APR / SR MỨC DATASET (RepoTransBench)")
    lines.append("=" * 72)
    if not counted:
        lines.append("(không repo nào chạy được cả 2 lượt test -> chưa tính được APR/SR)")
    else:
        n = len(counted)
        b_rates = [(r["metrics"].get("baseline_pass_rate") or 0.0) for r in counted]
        h_rates = [r["metrics"]["hybrid_pass_rate"] for r in counted]
        b_sr = sum(1 for r in counted if r["metrics"].get("baseline_pass_rate") == 1.0)
        h_sr = sum(1 for r in counted if r["metrics"]["hybrid_pass_rate"] == 1.0)
        n_rf = sum(1 for r in counted if r["metrics"].get("regression_free"))
        lines.append(f"  số repo tính vào mẫu : {n}")
        lines.append(f"  APR baseline         : {sum(b_rates) / n * 100:.1f}%")
        lines.append(f"  APR hybrid           : {sum(h_rates) / n * 100:.1f}%")
        lines.append(f"  SR  baseline         : {b_sr}/{n} ({b_sr / n * 100:.1f}%)")
        lines.append(f"  SR  hybrid           : {h_sr}/{n} ({h_sr / n * 100:.1f}%)")
        lines.append(f"  REGRESSION_FREE      : {n_rf}/{n} ({n_rf / n * 100:.1f}%)")
    if excluded:
        lines.append("")
        lines.append(
            f"  bị LOẠI khỏi mẫu ({len(excluded)} repo) -- lý do môi trường/repo, "
            "không phải chất lượng bản dịch:"
        )
        for r in excluded:
            lines.append(f"    - {r.get('label')}: {r.get('repo_status')}")
    return "\n".join(lines)
