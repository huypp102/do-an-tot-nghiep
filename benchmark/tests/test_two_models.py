"""Yeu cau 4: xac nhan Generator Agent goi DUNG generator_model va Decision
Agent goi DUNG decision_model -- 2 ten khac nhau, khong lan lon."""
import json
import logging
import sys
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

GEN_MODEL = "devstral:24b"
DEC_MODEL = "qwen3:8b"
RUST_SRC = "use pyo3::prelude::*;\n#[pyfunction]\nfn f() {}\n"

CALLS = []  # (model, role_tu_system_prompt)


class MockBackend:
    name = "mock"

    def __init__(self):
        self.model = "KHONG-DUOC-DUNG-MAC-DINH"

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        role = "decision" if (system and "ĐÁNH GIÁ" in system) else "generator"
        CALLS.append((kwargs.get("model"), role))
        if role == "decision":
            return "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone"
        return f"## Rust code\n```rust\n{RUST_SRC}```\n\n## Optimization strategy\nx."

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}], model=model)


print("=" * 72)
print("TEST 1 -- resolve_model_for_role doc dung config")
print("=" * 72)
from stage4_llm_transpile.model_backend import (  # noqa: E402
    DECISION_ROLE,
    GENERATOR_ROLE,
    resolve_model_for_role,
)

cfg_local = {"llm": {"backend": "local", "local": {
    "generator_model": GEN_MODEL, "decision_model": DEC_MODEL}}}
assert resolve_model_for_role(cfg_local, GENERATOR_ROLE) == GEN_MODEL
assert resolve_model_for_role(cfg_local, DECISION_ROLE) == DEC_MODEL
print(f"  generator -> {resolve_model_for_role(cfg_local, GENERATOR_ROLE)}")
print(f"  decision  -> {resolve_model_for_role(cfg_local, DECISION_ROLE)}")

# Tuong thich nguoc: chi co khoa `model` cu -> ca 2 vai tro dung chung.
cfg_legacy = {"llm": {"backend": "local", "local": {"model": "llama3.2:1b"}}}
assert resolve_model_for_role(cfg_legacy, GENERATOR_ROLE) == "llama3.2:1b"
assert resolve_model_for_role(cfg_legacy, DECISION_ROLE) == "llama3.2:1b"
print("  OK: config cu chi co `model:` van chay (ca 2 vai tro dung chung)")

print()
print("=" * 72)
print("TEST 2 -- Chay pipeline that, kiem tra model thuc su duoc goi")
print("=" * 72)

import stage4_llm_transpile.model_backend as mb  # noqa: E402
import stage5_compiler_in_the_loop.rebuild as rb  # noqa: E402
import stage6_benchmark.measure_subprocess as ms  # noqa: E402

mb.get_model_backend = lambda cfg: MockBackend()
rb.rebuild_from_draft = lambda d, c, t=300: rb.RebuildResult(rb.REBUILT_OK, "fake")
rb.restore_original_lib = lambda c: False
ms.measure_in_subprocess = lambda **kw: ([0.001] * 5, "")

import run_pipeline  # noqa: E402
from stage6_benchmark import bench  # noqa: E402


class AnyFn(dict):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def get(self, k, default=None):
        return self._fn

    def __contains__(self, k):
        return True

    def __getitem__(self, k):
        return self._fn


fn = lambda img: np.zeros_like(np.asarray(img), dtype=np.uint8)  # noqa: E731
bench.python_pure_pipeline.PIPELINE_REGISTRY = AnyFn(fn)
bench.rust_pure_pipeline.PIPELINE_REGISTRY = AnyFn(fn)
bench.rust_pure_pipeline.HAS_EXT = True

original_load = run_pipeline.load_config


def patched(*a, **k):
    cfg = original_load(*a, **k)
    cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
    cfg["llm"]["local"]["generator_model"] = GEN_MODEL
    cfg["llm"]["local"]["decision_model"] = DEC_MODEL
    cfg["target"]["mode"] = "repo"
    # Cac test nay kiem thu duong CHAY CU (run_once). Tu khi co PHA A..F,
    # target.mode=repo mac dinh di duong DONG (repo_pipeline.py), nen phai
    # tat repo_oracle de giu nguyen pham vi kiem thu cua chung.
    cfg.setdefault("repo_oracle", {})["enabled"] = False
    cfg["target"]["source"] = str(BENCH / "data" / "fake_dataset" / "repo_alpha")
    cfg["graph"]["top_k_hotspots"] = 1
    cfg["benchmark"].update({"iterations": 3, "warmup": 1})
    cfg["optimization_loop"]["max_rounds"] = 2
    cfg["compiler_loop"]["enabled"] = False
    cfg["decision_gate"]["enabled"] = False
    return cfg


run_pipeline.load_config = patched
code = run_pipeline.main()
assert code == 0

print()
print(f"Tong so loi goi model: {len(CALLS)}")
gen_models = {m for m, r in CALLS if r == "generator"}
dec_models = {m for m, r in CALLS if r == "decision"}
for m, r in CALLS:
    print(f"    role={r:<10} model={m}")

print()
print(f"  Model ma GENERATOR dung: {gen_models}")
print(f"  Model ma DECISION  dung: {dec_models}")

assert gen_models == {GEN_MODEL}, f"generator phai dung {GEN_MODEL}, thuc te {gen_models}"
assert dec_models == {DEC_MODEL}, f"decision phai dung {DEC_MODEL}, thuc te {dec_models}"
assert gen_models.isdisjoint(dec_models), "2 vai tro dang dung chung model!"
assert None not in gen_models and None not in dec_models, \
    "co loi goi khong truyen model -> roi ve mac dinh cua backend"
print("\n  OK: 2 vai tro goi 2 model KHAC NHAU, khong lan lon, khong roi ve mac dinh")

print()
print("=" * 72)
print("TEST 2 MODEL RIENG: PASS")
print("=" * 72)
