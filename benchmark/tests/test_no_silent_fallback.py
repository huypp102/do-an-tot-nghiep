"""PHA E -- chung minh KHONG co fallback am tham.

Yeu cau: "Rust raise exception thi tinh la test fail, KHONG duoc am tham quay
ve Python." Neu che loi o day thi mot ban dich sai se bao 'pass' va toan bo so
lieu correctness thanh vo nghia -- nen phai co bang chung truc tiep.

Cach kiem: chay bo test cua repo_gamma 2 lan voi plugin hoan doi:
  luot 1: extension gia tra KET QUA DUNG   -> test phai PASS
  luot 2: extension gia NEM RuntimeError   -> test phai FAIL
Cung mot ham, cung bo test; chi khac hanh vi cua 'Rust'.
"""
import json
import logging
import os
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


def _work_root(tag: str) -> str:
    """Thư mục làm việc cho test, TRUNG TÍNH giữa Linux và Windows.

    Ưu tiên `RTB_WORK_DIR` (máy dev này ổ C: hay hết chỗ nên nên trỏ sang D:),
    không thì dùng thư mục tạm của hệ điều hành.
    """
    import os as _os
    import tempfile as _tf
    from pathlib import Path as _P

    base = _os.environ.get("RTB_WORK_DIR") or _tf.gettempdir()
    return str(_P(base) / tag)

from stage1_profiling.module_resolve import resolve_module  # noqa: E402
from stage5_compiler_in_the_loop.repo_runner import (  # noqa: E402
    cleanup_venv,
    create_venv,
    install_repo,
    prepare_work_copy,
    regression_free,
    run_pytest,
)

WORK_ROOT = Path(_work_root("rtb_work_e"))
REPO = BENCH / "data" / "fake_dataset" / "repo_gamma"

work = prepare_work_copy(REPO, WORK_ROOT)
py, err = create_venv(work)
assert py is not None, err
ok, err = install_repo(py, work)
assert ok, err

module, root, err = resolve_module(work / "mypkg" / "ops.py", work)
assert not err, err

GOOD = (
    "def sum_squares(values):\n"
    "    return float(sum(float(v) * float(v) for v in values))\n"
)
RAISES = (
    "def sum_squares(values):\n"
    "    raise RuntimeError('ban Rust gia: co tinh nem loi')\n"
)

capture_dir = work / ".rtb_capture"
swap_specs = [{
    "function_name": "sum_squares",
    "module": module, "qualname": "sum_squares", "import_root": root,
    "ext_module": "fake_rsext", "ext_func": "sum_squares",
    "ext_root": str(work),
}]
env = {
    "PYTHONPATH": os.pathsep.join([str(BENCH), str(work)]),
    "RTB_SWAP_TARGETS": json.dumps(swap_specs),
    "RTB_CAPTURE_OUT": str(capture_dir),
}

results = {}
for label, impl in (("hybrid_good", GOOD), ("hybrid_raises", RAISES)):
    (work / "fake_rsext.py").write_text(impl, encoding="utf-8")
    r = run_pytest(py, work, label=label, plugin_args=["-p", "stage1_profiling.capture_plugin"],
                   extra_env=env, results_dir=capture_dir)
    results[label] = r
    rep = capture_dir / "swap_report.json"
    swapped = json.loads(rep.read_text(encoding="utf-8")) if rep.exists() else {}
    print(f"\n  [{label}] ok={r.ok} pass={r.n_passed}/{r.n_total} "
          f"fail={r.n_failed} error={r.n_errors}")
    print(f"      swap_report = {swapped}")

# Baseline (khong hoan doi) de so REGRESSION_FREE.
baseline = run_pytest(py, work, label="baseline_plain", results_dir=capture_dir)
print(f"\n  [baseline] pass={baseline.n_passed}/{baseline.n_total}")

cleanup_venv(work, keep=False)

errs = []
good, bad = results["hybrid_good"], results["hybrid_raises"]

# 1. Ban DUNG -> khong hoi quy.
if not (good.ok and good.n_failed == 0 and good.n_errors == 0):
    errs.append(f"ban Rust DUNG ma test van do: {good.n_failed} fail, {good.n_errors} error")
if regression_free(baseline, good) is not True:
    errs.append("ban Rust DUNG ma REGRESSION_FREE khong phai True")

# 2. Ban NEM LOI -> PHAI co test fail. Day la diem cot loi.
n_broken = bad.n_failed + bad.n_errors
if n_broken == 0:
    errs.append(
        "ban Rust NEM LOI ma KHONG test nao fail -> dang co fallback am tham!"
    )
if regression_free(baseline, bad) is not False:
    errs.append(f"ban Rust NEM LOI ma REGRESSION_FREE={regression_free(baseline, bad)} "
                "(phai la False)")

lost = sorted(set(baseline.passed_ids) - set(bad.passed_ids))
print(f"\n  test mat khi Rust nem loi ({len(lost)}): {lost}")
print(f"  REGRESSION_FREE: ban dung={regression_free(baseline, good)}  "
      f"ban nem loi={regression_free(baseline, bad)}")

print()
if errs:
    print(f"### THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### KHONG CO FALLBACK AM THAM: PASS")
