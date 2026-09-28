"""Chay TOAN BO run_pipeline.py voi llm.enabled=true + num_agents=2 bang
backend GIA (may nay khong co GPU/Ollama). Muc dich: chung minh Stage 3/4/5/6
noi dung nhau, khong loi import/cu phap, va 2 agent tach vai tro that su."""
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

ensure_utf8_stdio()

RUST_OK = """use pyo3::prelude::*;

#[pyfunction]
fn f(image: Vec<u8>, width: usize, height: usize) -> PyResult<Vec<u8>> {
    let mut out = vec![0u8; width * height];
    for i in 0..out.len() { out[i] = image[i]; }
    Ok(out)
}
"""

SYSTEMS_SEEN = []


class MockBackend:
    name = "mock-local"

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        SYSTEMS_SEEN.append((system or "")[:40])
        if system and "ĐÁNH GIÁ" in system:
            return "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone"
        return f"## Rust code\n```rust\n{RUST_OK}```\n\n## Optimization strategy\nToi uu vong lap."

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}])


# --- Vá get_model_backend de khong goi model that -------------------------
import stage4_llm_transpile.model_backend as mb  # noqa: E402

mb.get_model_backend = lambda cfg: MockBackend()

import run_pipeline  # noqa: E402

# --- Bat LLM + 2 agent + mode=repo tren repo gia, KHONG sua config.yaml ---
original_load = run_pipeline.load_config


def patched_load(*a, **k):
    cfg = original_load(*a, **k)
    cfg["llm"]["enabled"] = True
    cfg["llm"]["backend"] = "local"
    cfg["llm"]["num_agents"] = 2
    cfg["target"]["mode"] = "repo"
    # Cac test nay kiem thu duong CHAY CU (run_once). Tu khi co PHA A..F,
    # target.mode=repo mac dinh di duong DONG (repo_pipeline.py), nen phai
    # tat repo_oracle de giu nguyen pham vi kiem thu cua chung.
    cfg.setdefault("repo_oracle", {})["enabled"] = False
    cfg["target"]["source"] = str(BENCH / "data" / "fake_dataset" / "repo_alpha")
    cfg["graph"]["top_k_hotspots"] = 2
    return cfg


run_pipeline.load_config = patched_load

print("=" * 70)
print("CHAY run_pipeline.py: llm.enabled=true, backend=local(mock), num_agents=2")
print("=" * 70)
code = run_pipeline.main()
print(f"\nrun_pipeline.main() -> exit code {code}")

print()
print("=" * 70)
print("KIEM CHUNG TACH VAI TRO")
print("=" * 70)
gen_calls = [s for s in SYSTEMS_SEEN if "Rust/PyO3" in s]
dec_calls = [s for s in SYSTEMS_SEEN if "hieu nang" in s or "hiệu năng" in s]
print(f"Tong so loi goi model: {len(SYSTEMS_SEEN)}")
print(f"  - voi system prompt GENERATOR: {len(gen_calls)}")
print(f"  - voi system prompt DECISION : {len(dec_calls)}")
uniq = sorted(set(SYSTEMS_SEEN))
print("Cac system prompt khac nhau da dung:")
for u in uniq:
    print(f"    {u!r}")
assert len(uniq) == 2, f"Phai co DUNG 2 system prompt khac nhau, thuc te {len(uniq)}"
assert gen_calls and dec_calls, "Thieu loi goi cua 1 trong 2 agent"
print("\nOK: dung 2 vai tro tach biet, moi ben 1 system prompt rieng")
assert code == 0
print("\n" + "=" * 70)
print("TEST FULL PIPELINE (MOCK): PASS")
print("=" * 70)
