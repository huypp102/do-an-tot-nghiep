"""VAN DE 2 (dau-cuoi) -- repo co hotspot phat lai duoc + tier ho tro nhung
KHONG hotspot nao bien dich duoc phai ra `ALL_HOTSPOTS_FAILED_COMPILE`.

Day la tinh huong THAT cua luot chay thuc nghiem dau tien: 9/9 repo co
`generated = 1..5` nhung `compiled = 0`, ma tat ca deu bi gan
`NO_MEASURABLE_HOTSPOT` -- nhan do chi ra "khong tim duoc hotspot nao", sai
hoan toan nguyen nhan.

Dung repo_eta (co ca ham san pham va ham test cung file) + cargo gia LUON tra
loi PyO3 API cu, dung nguyen van loi thuc te tu chan_doan2.txt.
"""
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


def _work_root(tag: str) -> str:
    base = os.environ.get("RTB_WORK_DIR") or tempfile.gettempdir()
    return str(Path(base) / tag)


# Loi NGUYEN VAN tu luot chay that (chan_doan2.txt).
REAL_ERROR = (
    "error[E0277]: the trait bound `&PyList: pyo3::impl_::extract_argument::"
    "PyFunctionArgument<'_, '_>` is not satisfied\n"
    "   --> src/lib.rs:5:29\n"
    "  5 | fn add_kernel(values: &PyList) -> PyResult<f64> {\n"
    "    |                       ^ the trait `PyClass` is not implemented for `&PyList`\n"
)


class MockBackend:
    name = "mock"

    def chat(self, messages, system=None, **kwargs):
        if system and "ĐÁNH GIÁ" in system:
            return "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone"
        # Co tinh tra code theo API CU -> cargo gia se bao loi, y nhu luot that.
        return ("## Rust code\n```rust\nuse pyo3::prelude::*;\n"
                "use pyo3::types::PyList;\n#[pyfunction]\n"
                "fn add_kernel(values: &PyList) -> PyResult<f64> { Ok(0.0) }\n```\n\n"
                "## Optimization strategy\nx.")

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}])


import stage4_llm_transpile.model_backend as mb  # noqa: E402
import stage5_compiler_in_the_loop.compiler_loop as cl  # noqa: E402

mb.get_model_backend = lambda cfg: MockBackend()

# cargo gia: CO cargo, nhung LUON that bai voi loi API cu.
cl.cargo_available = lambda: True
cl.compile_and_classify = lambda crate_dir, timeout_sec=300: cl.CompileResult(
    ok=False, error_class=cl.TYPE_ERROR if hasattr(cl, "TYPE_ERROR") else "TYPE_ERROR",
    error_codes=["E0277"], n_errors=1, output=REAL_ERROR,
)

import input.intake as intake  # noqa: E402
import run_pipeline  # noqa: E402

_orig = intake.resolve_dataset_repos
intake.resolve_dataset_repos = lambda root, mx=0: [
    p for p in _orig(root, 0) if p.name == "repo_eta"
]
run_pipeline.resolve_dataset_repos = intake.resolve_dataset_repos

_orig_load = run_pipeline.load_config


def patched(*a, **k):
    cfg = _orig_load(*a, **k)
    cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
    cfg["dataset"].update({
        "enabled": True, "source_root": str(BENCH / "data" / "fake_dataset")})
    cfg["graph"].update({"candidate_pool": 30, "top_k_translate": 5})
    cfg["benchmark"].update({"iterations": 3, "warmup": 1})
    cfg["decision_gate"]["enabled"] = False
    cfg["compiler_loop"].update({"enabled": True, "max_retries": 1})
    cfg["repo_oracle"].update({
        "enabled": True, "work_root": _work_root("rtb_work_afc"), "keep_venv": False})
    cfg["ablation"]["enabled"] = False
    return cfg


run_pipeline.load_config = patched

errs = []
print("=" * 78)
print("Chay repo_eta voi cargo gia LUON bao loi PyO3 API cu")
print("=" * 78)
code = run_pipeline.main()
print(f"\n### EXIT CODE = {code} (mong doi 2 = khong do duoc gi)")
if code == 0:
    errs.append(f"exit code {code}: khong do duoc gi ma van bao thanh cong")

results = BENCH / "results"
newest = max(results.glob("dataset_summary_*.json"), key=lambda p: p.stat().st_mtime)
ts = json.loads(newest.read_text(encoding="utf-8"))["timestamp"]
d = json.loads((results / f"repo_summary_{ts}_repo_eta.json").read_text(encoding="utf-8"))

counts = (d.get("funnel") or {}).get("counts") or {}
print(f"\n  phễu: hotspot={counts.get('hotspot_found')} "
      f"replayable={counts.get('replayable')} tier={counts.get('tier_supported')} "
      f"generated={counts.get('generated')} compiled={counts.get('compiled')}")
print(f"  repo_status = {d.get('repo_status')}")

import outcomes  # noqa: E402

if counts.get("generated", 0) <= 0:
    errs.append("fixture khong toi duoc buoc sinh code -> khong kiem duoc nhan moi")
if counts.get("compiled", 0) != 0:
    errs.append(f"mong doi compiled=0, thuc te {counts.get('compiled')}")
if d.get("repo_status") != outcomes.ALL_HOTSPOTS_FAILED_COMPILE:
    errs.append(f"repo_status = {d.get('repo_status')}, mong doi "
                f"{outcomes.ALL_HOTSPOTS_FAILED_COMPILE}")
else:
    print("  OK  ra dung ALL_HOTSPOTS_FAILED_COMPILE, khong phai NO_MEASURABLE_HOTSPOT")

# Van de 3 ap dung trong duong chay that: pool khong co ham test.
pool = (d["stages"]["stage0"] or {}).get("funcrank_order") or []
tf = (d["stages"]["stage0"] or {}).get("test_filter") or {}
leaked = [n for n in pool if n.startswith("test_") or n in ("setUp", "tearDown")]
print(f"\n  candidate_pool ({len(pool)}): {sorted(pool)}")
print(f"  bo loc test: loai {tf.get('n_test_functions_excluded')}/"
      f"{tf.get('n_functions_total')} ({tf.get('pct_excluded')}%)")
print(f"  ham test lot vao pool: {leaked or 'KHONG'}")
if leaked:
    errs.append(f"ham test con trong pool duong chay that: {leaked}")

# Ly do tung hotspot phai la COMPILE_FAILED, va detail phai chua loi THAT.
reasons = {h["function"]: h["reason"] for h in d["hotspots"]}
print(f"\n  ly do tung hotspot: {reasons}")
n_cf = sum(1 for r in reasons.values() if r == outcomes.COMPILE_FAILED)
if n_cf == 0:
    errs.append("khong hotspot nao co ly do COMPILE_FAILED")
else:
    print(f"  OK  {n_cf} hotspot co ly do COMPILE_FAILED")
detail = next((h.get("detail", "") for h in d["hotspots"]
               if h["reason"] == outcomes.COMPILE_FAILED), "")
if "E0277" not in detail:
    errs.append(f"detail khong mang ma loi that: {detail[:120]}")
else:
    print("  OK  detail mang ma loi that (E0277)")

print()
if errs:
    print(f"### ALL_HOTSPOTS_FAILED_COMPILE: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### ALL_HOTSPOTS_FAILED_COMPILE: PASS")
