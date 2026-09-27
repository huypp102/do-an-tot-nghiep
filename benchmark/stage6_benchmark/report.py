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
    raw_files = sorted(d.glob("raw_*.json"))
    if not raw_files:
        raise FileNotFoundError(f"Không tìm thấy file raw_*.json nào trong {d}")
    latest = raw_files[-1]
    with latest.open("r", encoding="utf-8") as f:
        all_results = json.load(f)
    table = build_report(all_results)
    print(f"(từ {latest.name})\n")
    print(table)
    return table


if __name__ == "__main__":
    regenerate_latest()
