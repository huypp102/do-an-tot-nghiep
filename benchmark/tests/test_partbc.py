"""Kiem thu Phan B (tach vai tro 2 agent) va Phan C (Stage 5) bang MOCK
backend -- may nay khong co GPU/Ollama va cung khong co cargo."""
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from stage4_llm_transpile.agent_session import AgentSession  # noqa: E402
from stage4_llm_transpile.decision_agent import (  # noqa: E402
    DECISION_SYSTEM_PROMPT,
    decide_after_benchmark,
)
from stage4_llm_transpile.generator_agent import (  # noqa: E402
    GENERATOR_SYSTEM_PROMPT,
    GeneratorAgent,
)
from stage5_compiler_in_the_loop.compiler_loop import (  # noqa: E402
    BORROW_LIFETIME_ERROR,
    OTHER,
    SYNTAX_ERROR,
    TYPE_ERROR,
    cargo_available,
    classify_error,
    compile_and_classify,
)
from stage5_compiler_in_the_loop.loop_runner import (  # noqa: E402
    compute_metrics,
    run_compile_loop_for,
)

RUST_OK = """use pyo3::prelude::*;

#[pyfunction]
fn edge_det(image: Vec<u8>, width: usize, height: usize) -> PyResult<Vec<u8>> {
    let mut out = vec![0u8; width * height];
    for i in 0..out.len() { out[i] = image[i]; }
    Ok(out)
}
"""


class MockBackend:
    """Ghi lai MOI loi goi de kiem chung 2 agent KHONG chung lich su."""

    name = "mock"

    def __init__(self):
        self.calls = []  # (system_prompt, so_luong_messages, noi_dung_messages)

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        self.calls.append({
            "system": system or "",
            "n_messages": len(messages),
            "contents": [m["content"] for m in messages],
        })
        if system and "ĐÁNH GIÁ" in system:
            return "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone"
        return f"## Rust code\n```rust\n{RUST_OK}```\n\n## Optimization strategy\nGop vong lap."

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}])


print("=" * 70)
print("TEST 1 -- Hai agent co SYSTEM PROMPT khac nhau")
print("=" * 70)
assert "SINH và SỬA code Rust" in GENERATOR_SYSTEM_PROMPT
assert "ĐÁNH GIÁ" in DECISION_SYSTEM_PROMPT
assert GENERATOR_SYSTEM_PROMPT != DECISION_SYSTEM_PROMPT
print("OK: generator = 'SINH va SUA code', decision = 'DANH GIA' -> khac nhau")

print()
print("=" * 70)
print("TEST 2 -- Lich su hoi thoai RIENG BIET (chong lan ngu canh)")
print("=" * 70)
backend = MockBackend()
gen = AgentSession("generator", GENERATOR_SYSTEM_PROMPT, backend)
dec = AgentSession("decision", DECISION_SYSTEM_PROMPT, backend)

gen.send("BI_MAT_CUA_GENERATOR: hay dich ham nay")
gen.send("sua loi giup toi")
dec.send("BI_MAT_CUA_DECISION: danh gia ket qua")

gen_text = " ".join(m["content"] for m in gen.messages)
dec_text = " ".join(m["content"] for m in dec.messages)
print(f"generator co {len(gen.messages)} message, decision co {len(dec.messages)} message")
assert "BI_MAT_CUA_DECISION" not in gen_text, "RO RI: generator thay lich su decision!"
assert "BI_MAT_CUA_GENERATOR" not in dec_text, "RO RI: decision thay lich su generator!"
print("OK: khong ben nao thay noi dung cua ben kia")
assert gen.backend is dec.backend
print("OK: van DUNG CHUNG 1 instance backend (1 ket noi toi model)")

print()
print("=" * 70)
print("TEST 3 -- Phan loai loi bien dich")
print("=" * 70)
cases = [
    ("error[E0308]: mismatched types", TYPE_ERROR),
    ("error[E0502]: cannot borrow `x` as mutable", BORROW_LIFETIME_ERROR),
    ("error[E0382]: borrow of moved value", BORROW_LIFETIME_ERROR),
    ("error: expected one of `,` or `}`", SYNTAX_ERROR),
    ("error: this file contains an unclosed delimiter", SYNTAX_ERROR),
    ("error: linker `cc` not found", OTHER),
    ("error[E0308]: mismatched\nerror[E0502]: borrow", BORROW_LIFETIME_ERROR),  # borrow uu tien
]
for text, want in cases:
    got = classify_error(text)
    mark = "OK " if got == want else "SAI"
    print(f"  {mark} {text[:45]!r:50} -> {got}")
    assert got == want, f"mong doi {want}, nhan {got}"

print()
print("=" * 70)
print("TEST 4 -- Stage 5 khi KHONG co cargo (may nay)")
print("=" * 70)
print(f"cargo_available() = {cargo_available()}")
scratch = BENCH / "results" / ".stage5_scratch_crate"
scratch.mkdir(parents=True, exist_ok=True)
(scratch / "Cargo.toml").write_text(
    '[package]\nname="scratch"\nversion="0.1.0"\nedition="2021"\n', encoding="utf-8"
)
res = compile_and_classify(scratch)
print(f"skipped={res.skipped}")
print(f"ly do: {res.skip_reason[:100]}...")
assert res.skipped and not res.ok
print("OK: bao skip co huong dan, KHONG raise")

print()
print("=" * 70)
print("TEST 5 -- Vong lap Stage 5 + so lieu Pass@1/DSR@1")
print("=" * 70)
agent = GeneratorAgent(backend, output_dir=BENCH / "results" / ".stage5_scratch_crate" / "gen")
outcome = run_compile_loop_for("edge_det", RUST_OK, agent, scratch, max_retries=3)
print(f"outcome: compiled={outcome.compiled} skipped={outcome.skipped} attempts={outcome.attempts}")
m = compute_metrics([outcome])
print(f"metrics: {m}")
assert m["n_skipped"] == 1 and m["pass_at_1"] is None
print("OK: hotspot bi skip khong lam sai lech Pass@1 (tra None thay vi 0.0)")

print()
print("=" * 70)
print("TEST 6 -- Decision Agent dung session RIENG khi num_agents=2")
print("=" * 70)
backend2 = MockBackend()
d = decide_after_benchmark(
    "edge_det", before_sec=0.010, after_sec=0.004, num_agents=2, backend=backend2,
    rust_code=RUST_OK,
)
print(f"decision: accepted={d.accepted} source={d.source} speedup={d.speedup:.2f}x")
assert d.source == "llm" and d.accepted
sys_used = backend2.calls[0]["system"]
assert "ĐÁNH GIÁ" in sys_used, "Decision Agent dung nham system prompt!"
assert "SINH và SỬA" not in sys_used
print("OK: goi model voi DUNG system prompt cua decision, khong lan sang generator")

print()
print("=" * 70)
print("TAT CA TEST PHAN B + C: PASS")
print("=" * 70)
