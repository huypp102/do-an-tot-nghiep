"""Kiem chung VONG LAP retry cua Stage 5 bang compiler GIA (may nay khong co
cargo nen khong the test bang cargo that)."""
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from stage5_compiler_in_the_loop import loop_runner  # noqa: E402
from stage5_compiler_in_the_loop.compiler_loop import (  # noqa: E402
    BORROW_LIFETIME_ERROR,
    CompileResult,
)

SCRATCH = BENCH / "results" / ".stage5_scratch_crate"
SCRATCH.mkdir(parents=True, exist_ok=True)


class FakeGenerator:
    """Gia lap GeneratorAgent: moi lan duoc goi tra ve code 'da sua'."""

    def __init__(self):
        self.fix_calls = []

    def fix_compile_error(self, function_name, compiler_output, error_class="OTHER"):
        self.fix_calls.append((function_name, error_class))
        n = len(self.fix_calls)
        return {"ok": True, "function_name": function_name,
                "rust_code": f"// ban sua lan {n}\nfn main() {{}}\n"}


def make_fake_compiler(fail_times: int):
    """Tra ve ham gia lap: that bai `fail_times` lan dau, sau do thanh cong."""
    state = {"n": 0}

    def fake(crate_dir, timeout_sec=300):
        state["n"] += 1
        if state["n"] <= fail_times:
            return CompileResult(
                ok=False, error_class=BORROW_LIFETIME_ERROR,
                error_codes=["E0502"], n_errors=1,
                output="error[E0502]: cannot borrow `x` as mutable",
            )
        return CompileResult(ok=True, n_errors=0)

    return fake


def scenario(name, fail_times, max_retries=3):
    original = loop_runner.compile_and_classify
    loop_runner.compile_and_classify = make_fake_compiler(fail_times)
    try:
        gen = FakeGenerator()
        out = loop_runner.run_compile_loop_for(
            "edge_det", "fn main() {}", gen, SCRATCH, max_retries=max_retries
        )
    finally:
        loop_runner.compile_and_classify = original
    print(f"\n--- {name} ---")
    print(f"  compiled={out.compiled}  passed_first_try={out.passed_first_try}")
    print(f"  attempts(cargo check)={out.attempts}  fix_rounds(goi generator)={out.fix_rounds}")
    print(f"  error_classes={out.error_classes}")
    print(f"  generator duoc goi voi: {gen.fix_calls}")
    return out


print("=" * 70)
print("TEST VONG LAP RETRY STAGE 5 (compiler gia)")
print("=" * 70)

a = scenario("A. Thanh cong NGAY lan dau (Pass@1)", fail_times=0)
assert a.compiled and a.passed_first_try and a.attempts == 1 and a.fix_rounds == 0

b = scenario("B. Loi 2 lan roi sua duoc (DSR@1 thanh cong)", fail_times=2)
assert b.compiled and not b.passed_first_try
assert b.attempts == 3 and b.fix_rounds == 2
assert b.error_classes == [BORROW_LIFETIME_ERROR] * 2

c = scenario("C. Loi mai khong sua duoc (het max_retries=3)", fail_times=99)
assert not c.compiled and not c.passed_first_try
assert c.fix_rounds == 3, f"phai goi generator dung 3 lan, thuc te {c.fix_rounds}"
assert c.attempts == 4, f"phai chay cargo check 4 lan (1 dau + 3 sua), thuc te {c.attempts}"

print()
print("=" * 70)
print("SO LIEU Pass@1 / DSR@1")
print("=" * 70)
m = loop_runner.compute_metrics([a, b, c])
for k, v in m.items():
    print(f"  {k}: {v}")
# a pass ngay -> Pass@1 = 1/3
assert abs(m["pass_at_1"] - 1 / 3) < 1e-9, m["pass_at_1"]
# trong 2 ca fail lan dau (b,c), chi b sua duoc -> DSR@1 = 1/2
assert abs(m["dsr_at_1"] - 0.5) < 1e-9, m["dsr_at_1"]
print("\nOK: Pass@1 = 1/3 (a), DSR@1 = 1/2 (b sua duoc, c thi khong)")

print()
print("=" * 70)
print("TAT CA TEST VONG LAP RETRY: PASS")
print("=" * 70)
