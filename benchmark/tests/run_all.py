"""Chạy TOÀN BỘ test của benchmark bằng một lệnh.

    python tests/run_all.py                 # chạy hết (bỏ nhóm cần dataset thật)
    python tests/run_all.py --fast           # chỉ nhóm nhanh, không dựng venv
    python tests/run_all.py --only ablation  # chạy các test có tên khớp
    python tests/run_all.py --list           # xem danh sách và phân nhóm

Không dùng `pytest` để gom: các file này là script kiểm chứng end-to-end, tự
`sys.exit(1)` khi thất bại và tự in báo cáo riêng. Gói chúng vào pytest sẽ làm
mất chính phần output đáng đọc nhất.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BENCH = TESTS_DIR.parent

# --- Phân nhóm theo CHI PHÍ, để còn chọn được lượt chạy nhanh -----------------
# "fast"    : thuần trong tiến trình, vài giây.
# "venv"    : dựng venv riêng cho repo giả -> hàng chục giây tới vài phút.
# "dataset" : CẦN dataset RepoTransBench thật (REPOTRANSBENCH_ROOT).
GROUPS: dict[str, str] = {
    "test_portability.py": "fast",
    "test_gen.py": "fast",
    "test_numctx_think.py": "fast",
    "test_libsafety.py": "fast",
    "test_intake_fix.py": "fast",
    "test_scalene.py": "fast",
    "test_two_models.py": "fast",
    "test_loop_correctness.py": "fast",
    "test_rebuild_measure.py": "fast",
    "test_retry_loop.py": "fast",
    "test_partbc.py": "fast",
    "test_full_mock.py": "fast",
    "test_phase_ab.py": "venv",
    "test_no_silent_fallback.py": "venv",
    "test_phase_g.py": "venv",
    "test_legacy_and_exit.py": "venv",
    "test_ablation.py": "venv",
    "test_confounded.py": "venv",
    "test_resume_budget.py": "venv",
    "test_real_dataset_ab.py": "dataset",
}

TIMEOUTS = {"fast": 600, "venv": 2400, "dataset": 2400}


def discover() -> list[Path]:
    return sorted(TESTS_DIR.glob("test_*.py"))


def group_of(path: Path) -> str:
    return GROUPS.get(path.name, "fast")


def run_one(path: Path, env: dict) -> tuple[bool, float, str]:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUTS[group_of(path)], cwd=str(BENCH), env=env,
        )
    except subprocess.TimeoutExpired:
        return False, time.perf_counter() - t0, "TIMEOUT"
    out = (proc.stdout or "") + (proc.stderr or "")
    # Lấy dòng kết luận cuối mà chính test tự in ra.
    verdict = ""
    for line in reversed(out.splitlines()):
        if line.startswith("###"):
            verdict = line.strip()
            break
    return proc.returncode == 0, time.perf_counter() - t0, verdict or out.strip()[-160:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="chỉ nhóm 'fast'")
    parser.add_argument("--only", default=None, help="chỉ test có tên chứa chuỗi này")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--with-dataset", action="store_true",
                        help="chạy cả nhóm cần dataset RepoTransBench thật")
    args = parser.parse_args(argv)

    tests = discover()
    if args.only:
        tests = [t for t in tests if args.only in t.name]
    if args.fast:
        tests = [t for t in tests if group_of(t) == "fast"]
    if not args.with_dataset:
        tests = [t for t in tests if group_of(t) != "dataset"]

    if args.list:
        for t in discover():
            print(f"  {group_of(t):<8} {t.name}")
        return 0

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONPATH", str(BENCH))

    print("=" * 86)
    print(f"CHẠY {len(tests)} TEST  (bỏ nhóm 'dataset'"
          f"{' -- dùng --with-dataset để bật' if not args.with_dataset else ''})")
    print("=" * 86)

    results = []
    for t in tests:
        print(f"  [{group_of(t):<7}] {t.name:<32} ", end="", flush=True)
        ok, secs, verdict = run_one(t, env)
        results.append((t.name, ok, secs, verdict))
        print(f"{'PASS' if ok else 'FAIL'}  {secs:>6.1f}s   {verdict[:60]}")

    n_fail = sum(1 for _n, ok, _s, _v in results if not ok)
    total = sum(s for _n, _o, s, _v in results)
    print("-" * 86)
    print(f"  {len(results) - n_fail}/{len(results)} PASS, tổng {total:.0f}s")
    if n_fail:
        print("\nTHẤT BẠI:")
        for name, ok, _s, verdict in results:
            if not ok:
                print(f"  - {name}: {verdict[:200]}")
                print(f"    chạy lại: python tests/{name}")
        return 1
    print("\n### TOÀN BỘ TEST: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
