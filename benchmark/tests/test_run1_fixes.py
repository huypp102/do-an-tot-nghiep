"""Kiem chung 3 van de phat hien tu LUOT CHAY THUC NGHIEM 1 dau tien.

  1B. compiler_output phai chua chan doan DAY DU tu stdout JSON, khong chi dong
      tom tat o stderr.
  2.  repo_status = ALL_HOTSPOTS_FAILED_COMPILE khi generated > 0 nhung
      compiled == 0 (khong con gop chung voi NO_MEASURABLE_HOTSPOT).
  3.  Ham test bi loai khoi candidate_pool NGAY TU DAU, ke ca khi nam cung file
      voi code san pham (repo_eta mo phong BBuf_onnx_learn).
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


def _work_root(tag: str) -> str:
    base = os.environ.get("RTB_WORK_DIR") or tempfile.gettempdir()
    return str(Path(base) / tag)


errs = []

# ===========================================================================
print("=" * 78)
print("VAN DE 1B -- compiler_output phai co chan doan DAY DU tu stdout JSON")
print("=" * 78)

import stage5_compiler_in_the_loop.compiler_loop as cl  # noqa: E402

# Nguyen van loi thuc te tu chan_doan2.txt, dong goi lai theo dinh dang
# `cargo check --message-format=json` (moi dong 1 JSON, chan doan day du nam
# trong field `rendered`).
RENDERED_E0599 = (
    "error[E0599]: no method named `clear` found for reference `&PyList` in the current scope\n"
    " --> src/lib.rs:6:16\n"
    "  |\n"
    "6 |     directives.clear();\n"
    "  |                ^^^^^ method not found in `&PyList`\n"
)
RENDERED_E0277 = (
    "error[E0277]: the trait bound `&PyList: pyo3::impl_::extract_argument::"
    "PyFunctionArgument<'_, '_>` is not satisfied\n"
    "   --> src/lib.rs:5:29\n"
    "    |\n"
    "  5 | fn clear_kernel(directives: &PyList) -> PyResult<()> {\n"
    "    |                             ^ the trait `PyClass` is not implemented for `&PyList`\n"
)
FAKE_STDOUT = "\n".join([
    json.dumps({"reason": "compiler-message", "message": {
        "level": "error", "code": {"code": "E0599"}, "rendered": RENDERED_E0599}}),
    json.dumps({"reason": "compiler-message", "message": {
        "level": "error", "code": {"code": "E0277"}, "rendered": RENDERED_E0277}}),
    json.dumps({"reason": "build-finished", "success": False}),
])
# stderr chi co dong tom tat -- day la CAI BAY cua ban cu.
FAKE_STDERR = "error: could not compile `clear_rsext` (lib) due to 2 previous errors"


class _Proc:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


_orig_run = cl.subprocess.run
_orig_cargo = cl.cargo_available
cl.cargo_available = lambda: True
cl.subprocess.run = lambda *a, **k: _Proc(101, FAKE_STDOUT, FAKE_STDERR)

crate = Path(tempfile.mkdtemp(prefix="rtb_fake_crate_"))
(crate / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
res = cl.compile_and_classify(crate)

cl.subprocess.run = _orig_run
cl.cargo_available = _orig_cargo

print(f"  ok={res.ok} error_class={res.error_class} codes={res.error_codes} "
      f"n_errors={res.n_errors}")
print(f"  do dai compiler_output = {len(res.output)} ky tu")
checks = {
    "co ma loi E0599": "error[E0599]" in res.output,
    "co ma loi E0277": "error[E0277]" in res.output,
    "co doan code gay loi": "directives.clear();" in res.output,
    "co goi y kieu dung": "PyFunctionArgument" in res.output,
    "van giu dong tom tat stderr": "could not compile" in res.output,
    "KHONG chi co dong tom tat": len(res.output) > len(FAKE_STDERR) + 50,
}
for name, ok in checks.items():
    print(f"  {'OK ' if ok else 'SAI'} {name}")
    if not ok:
        errs.append(f"1B: {name}")
if res.error_codes != ["E0277", "E0599"]:
    errs.append(f"1B: ma loi parse sai: {res.error_codes}")

# ===========================================================================
print()
print("=" * 78)
print("VAN DE 2 -- ALL_HOTSPOTS_FAILED_COMPILE thay vi NO_MEASURABLE_HOTSPOT")
print("=" * 78)

import outcomes  # noqa: E402

cases = [
    ("sinh duoc 3, compile 0", 3, 0, outcomes.ALL_HOTSPOTS_FAILED_COMPILE),
    ("chua sinh duoc gi (0)", 0, 0, outcomes.NO_MEASURABLE_HOTSPOT),
    ("khong truyen so lieu", None, None, outcomes.NO_MEASURABLE_HOTSPOT),
    ("sinh 3, compile 2 (van 0 MEASURED)", 3, 2, outcomes.NO_MEASURABLE_HOTSPOT),
]
for label, gen, comp, want in cases:
    got = outcomes.decide_repo_status(
        ["COMPILE_FAILED", "COMPILE_FAILED"], n_generated=gen, n_compiled=comp
    )
    ok = got == want
    print(f"  {'OK ' if ok else 'SAI'} {label:<36} -> {got}")
    if not ok:
        errs.append(f"2: {label}: {got}, mong doi {want}")

# Loi moi truong phai THANG nhan moi.
got = outcomes.decide_repo_status(["COMPILE_FAILED"], baseline_failed=True,
                                  n_generated=3, n_compiled=0)
print(f"  {'OK ' if got == outcomes.BASELINE_FAILED else 'SAI'} "
      f"baseline_failed thang nhan moi     -> {got}")
if got != outcomes.BASELINE_FAILED:
    errs.append(f"2: baseline_failed bi nhan moi lan at: {got}")

# Nhan moi KHONG duoc coi la thanh cong.
if outcomes.is_success(outcomes.ALL_HOTSPOTS_FAILED_COMPILE):
    errs.append("2: ALL_HOTSPOTS_FAILED_COMPILE bi coi la thanh cong")
else:
    print("  OK  ALL_HOTSPOTS_FAILED_COMPILE khong tinh la thanh cong")
if outcomes.ALL_HOTSPOTS_FAILED_COMPILE not in outcomes.REPO_STATUSES:
    errs.append("2: nhan moi chua co trong REPO_STATUSES")

# ===========================================================================
print()
print("=" * 78)
print("VAN DE 3 -- ham test KHONG duoc chiem cho trong candidate_pool")
print("=" * 78)

from stage0_graph.builder import build_graph, discover_python_files  # noqa: E402
from stage0_graph.rank import top_k_functions  # noqa: E402
from stage0_graph.test_filter import exclude_test_functions  # noqa: E402

REPO = BENCH / "data" / "fake_dataset" / "repo_eta"
files = discover_python_files(REPO)
graph = build_graph(files, REPO)
excluded, stats = exclude_test_functions(graph)

print(f"  repo_eta: {stats['n_functions_total']} ham, loai "
      f"{stats['n_test_functions_excluded']} ({stats['pct_excluded']}%)")
print(f"  ly do: {stats['by_reason']}")

pool_before = [fn.name for fn, _ in top_k_functions(graph, 30)]
pool_after = [fn.name for fn, _ in top_k_functions(graph, 30, exclude_ids=excluded)]
print(f"\n  pool KHI CHUA loc ({len(pool_before)}): {sorted(pool_before)}")
print(f"  pool SAU KHI loc ({len(pool_after)}): {sorted(pool_after)}")

TEST_NAMES = {"test_add", "test_sub", "test_mul", "test_div", "test_div_zero",
              "test_sum_list", "test_sum", "setUp", "tearDown",
              "test_add_basic", "test_sub_basic", "test_mul_basic",
              "test_div_basic", "test_sum_list_basic"}
leaked = sorted(set(pool_after) & TEST_NAMES)
print(f"\n  ham test con lot vao pool: {leaked or 'KHONG'}")
if leaked:
    errs.append(f"3: ham test con trong pool: {leaked}")

PRODUCT = {"add", "sub", "mul", "div", "sum_list"}
missing = sorted(PRODUCT - set(pool_after))
print(f"  ham san pham bi loai oan: {missing or 'KHONG'}")
if missing:
    errs.append(f"3: loc qua tay, mat ham san pham: {missing}")

if not (set(pool_before) & TEST_NAMES):
    errs.append("3: pool truoc khi loc da khong co ham test -> fixture khong kiem duoc gi")
else:
    print(f"  OK  fixture co thuc: {len(set(pool_before) & TEST_NAMES)} ham test "
          f"da bi loai khoi pool")

# Graph KHONG duoc bi sua: Stage 3 con can biet test nao goi hotspot.
if stats["n_functions_total"] != len(graph.functions):
    errs.append("3: bo loc da sua graph (khong duoc phep)")
else:
    print("  OK  graph khong bi sua (Stage 3 van thay duoc lien ket tu test)")

print()
if errs:
    print(f"### 3 VAN DE LUOT CHAY 1: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### 3 VAN DE LUOT CHAY 1: PASS")
