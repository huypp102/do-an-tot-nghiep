"""PHA A + B -- kiem chung tren repo gia repo_gamma.

Moi ham phai ra DUNG 1 ly do:
    sum_squares        -> None (ghi duoc)      TIER1_NATIVE
    scale_point        -> None (ghi duoc)      TIER2_KERNEL
    accumulate_inplace -> None (ghi duoc)      TIER1_NATIVE
    count_rows         -> UNREPLAYABLE_ARGS
    unused_helper      -> NOT_COVERED_BY_TESTS
    jittered_mean      -> NONDETERMINISTIC
    walk_values        -> UNSUPPORTED_KIND
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

from stage1_profiling.module_resolve import resolve_module  # noqa: E402
from stage1_profiling.replay import run_replay_checks  # noqa: E402
from stage5_compiler_in_the_loop.repo_runner import (  # noqa: E402
    baseline_failed,
    build_child_env,
    cleanup_venv,
    create_venv,
    install_repo,
    prepare_work_copy,
    run_pytest,
)

WORK_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "rtb_work_test"
REPO = BENCH / "data" / "fake_dataset" / "repo_gamma"

FUNCS = [
    "sum_squares", "scale_point", "accumulate_inplace",
    "count_rows", "unused_helper", "jittered_mean", "walk_values",
]

EXPECTED = {
    "sum_squares": (None, "TIER1_NATIVE"),
    "scale_point": (None, "TIER2_KERNEL"),
    "accumulate_inplace": (None, "TIER1_NATIVE"),
    "count_rows": ("UNREPLAYABLE_ARGS", None),
    "unused_helper": ("NOT_COVERED_BY_TESTS", None),
    "jittered_mean": ("NONDETERMINISTIC", None),
    "walk_values": ("UNSUPPORTED_KIND", None),
}

print("=" * 72)
print("PHA A -- copy repo, tao venv, cai dat, chay pytest")
print("=" * 72)

work = prepare_work_copy(REPO, WORK_ROOT)
print(f"  work_dir = {work}")
assert (work / "mypkg" / "ops.py").exists()
assert not (work / "__pycache__").exists(), "phai bo thu muc rac"

py, err = create_venv(work)
assert py is not None, f"tao venv that bai: {err}"
ok, err = install_repo(py, work)
assert ok, f"cai dat that bai: {err}"

# --- module:qualname cho tung hotspot ---
specs = []
for name in FUNCS:
    module, root, err = resolve_module(work / "mypkg" / "ops.py", work)
    assert not err, f"resolve_module loi: {err}"
    specs.append({
        "function_name": name, "module": module, "qualname": name,
        "import_root": root,
    })
print(f"  module resolve -> {specs[0]['module']} (goc import: {specs[0]['import_root']})")
assert specs[0]["module"] == "mypkg.ops", specs[0]["module"]

capture_dir = work / ".rtb_capture"
baseline = run_pytest(
    py, work, label="baseline",
    plugin_args=["-p", "stage1_profiling.capture_plugin"],
    extra_env={
        "PYTHONPATH": os.pathsep.join([str(BENCH), str(work)]),
        "RTB_CAPTURE_TARGETS": json.dumps(specs),
        "RTB_CAPTURE_OUT": str(capture_dir),
        "RTB_CAPTURE_MAX_CALLS": "20",
    },
)
print(f"\n  baseline: ok={baseline.ok} {baseline.n_passed}/{baseline.n_total} "
      f"pass_rate={baseline.pass_rate} SR={baseline.success_rate_flag}")
assert baseline.ok, f"khong chay duoc pytest: {baseline.error}"
failed, why = baseline_failed(baseline)
assert not failed, f"baseline khong duoc coi la fail: {why}"

print("\n" + "=" * 72)
print("PHA B -- doc capture_index.json")
print("=" * 72)
index = json.loads((capture_dir / "capture_index.json").read_text(encoding="utf-8"))
for m in sorted(index, key=lambda d: d["function_name"]):
    print(f"  {m['function_name']:<20} reason={str(m.get('reason')):<24} "
          f"n_calls={m.get('n_calls_captured')} types={m.get('observed_arg_types')}")

print("\n" + "=" * 72)
print("PHA B -- phat lai 2 lan + phan tang (trong venv repo)")
print("=" * 72)
verdicts, err = run_replay_checks(py, work, capture_dir, BENCH)
assert not err, f"phat lai loi: {err}"

n_bad = 0
for name in FUNCS:
    v = verdicts.get(name)
    if v is None:
        print(f"  {name:<20} THIEU trong ket qua")
        n_bad += 1
        continue
    exp_reason, exp_tier = EXPECTED[name]
    mark = "OK " if v.reason == exp_reason else "SAI"
    if v.reason != exp_reason:
        n_bad += 1
    print(f"  {mark} {name:<20} reason={str(v.reason):<22} tier={v.tier:<14} "
          f"n_calls={v.n_calls}")
    if v.detail:
        print(f"        -> {v.detail[:110]}")
    if exp_tier and v.tier != exp_tier:
        print(f"        !! tier mong doi {exp_tier}, nhan {v.tier}")
        n_bad += 1

cleanup_venv(work, keep=False)
print()
if n_bad:
    print(f"### PHA A+B: THAT BAI ({n_bad} sai lech)")
    sys.exit(1)
print("### PHA A+B: PASS -- moi ham ra dung 1 ly do")
