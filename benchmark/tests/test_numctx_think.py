"""Muc 3 + 6: kiem chung loc <think>, va log dung 2 model + 2 num_ctx."""
import logging
import sys
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from stage4_llm_transpile.decision_agent import (  # noqa: E402
    _parse_llm_decision,
    strip_think_blocks,
)

print("=" * 72)
print("TEST 1 -- Loc khoi <think> truoc khi parse")
print("=" * 72)

# Truong hop chinh theo yeu cau: <think> chua REJECT, ket luan that la ACCEPT.
TRICKY = """<think>
Hmm, speedup chi 1.05x thoi. Co le nen REJECT vi qua it.
Nhung ma... code dung, va nhanh hon that. CONTINUE hay STOP nhi?
Thoi, REJECT thi hoi kho khan qua.
</think>

## Decision
ACCEPT

## Continue
STOP

## Next strategy
none
"""
accepted, cont, strat = _parse_llm_decision(TRICKY)
print(f"  <think> chua 'REJECT', ket luan that 'ACCEPT' -> parse ra: "
      f"accepted={accepted}, continue={cont}")
assert accepted is True, "Da doc nham chu REJECT trong khoi <think>!"
assert cont is False
print("  OK: parse dung ACCEPT, khong bi khoi <think> danh lua")

# The dong BI CAT (response bi cat vi cham gioi han token).
TRUNCATED = """<think>
Toi dang can nhac. Co the REJECT vi speedup thap. Nhung
"""
cleaned = strip_think_blocks(TRUNCATED)
print(f"\n  The dong bi cat -> con lai {len(cleaned)} ky tu: {cleaned!r}")
assert "REJECT" not in cleaned, "Van con noi dung <think> sau khi loc!"
print("  OK: the dong bi thieu van loc sach")

# The dong mo coi.
ORPHAN = "suy nghi bi cat o dau REJECT</think>\n\n## Decision\nACCEPT\n"
acc2, _, _ = _parse_llm_decision(ORPHAN)
print(f"\n  The dong mo coi -> accepted={acc2}")
assert acc2 is True
print("  OK: the dong mo coi xu ly duoc")

# Khong co <think> thi khong duoc lam hong gi.
NORMAL = "## Decision\nREJECT\n\n## Continue\nCONTINUE\n\n## Next strategy\nDung SIMD"
acc3, cont3, strat3 = _parse_llm_decision(NORMAL)
assert acc3 is False and cont3 is True and strat3 == "Dung SIMD"
print("\n  OK: response khong co <think> van parse binh thuong")

print()
print("=" * 72)
print("TEST 2 -- Canh bao khi prompt vuot num_ctx")
print("=" * 72)
from stage4_llm_transpile.model_backend import LocalModelBackend  # noqa: E402

be = LocalModelBackend(model="x", base_url="http://localhost:11434")
long_msg = [{"role": "user", "content": "x" * 40000}]  # ~11428 token uoc tinh
import io  # noqa: E402

buf = io.StringIO()
h = logging.StreamHandler(buf)
logging.getLogger("benchmark.stage4_llm_transpile.model_backend").addHandler(h)
be._warn_if_prompt_too_long(long_msg, num_ctx=4096, model_name="x")
be._warn_if_prompt_too_long(long_msg, num_ctx=32768, model_name="x")
logging.getLogger("benchmark.stage4_llm_transpile.model_backend").removeHandler(h)
out = buf.getvalue()
n_warn = out.count("CẮT BỚT")
print(f"  prompt ~11428 token: num_ctx=4096 -> canh bao, num_ctx=32768 -> khong")
print(f"  so canh bao thuc te: {n_warn}")
assert n_warn == 1, f"phai canh bao dung 1 lan, thuc te {n_warn}"
print("  OK: chi canh bao khi that su vuot nguong")

print()
print("=" * 72)
print("TEST 3 -- Pipeline that: dung 2 model + 2 num_ctx")
print("=" * 72)

CALLS = []


class MockBackend:
    name = "mock"

    def __init__(self):
        self.model = "KHONG-DUOC-DUNG"

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        role = "decision" if (system and "ĐÁNH GIÁ" in system) else "generator"
        CALLS.append({"role": role, "model": kwargs.get("model"),
                      "num_ctx": kwargs.get("num_ctx"),
                      "think": kwargs.get("think"),
                      "temperature": kwargs.get("temperature"),
                      "seed": kwargs.get("seed")})
        if role == "decision":
            # Co tinh nhet <think> chua REJECT de kiem tra lop loc trong
            # duong chay that, khong chi trong unit test.
            return ("<think>Co le REJECT chang?</think>\n"
                    "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone")
        return "## Rust code\n```rust\nfn f() {}\n```\n\n## Optimization strategy\nx."

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}], model=model)


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
for c in CALLS:
    print(f"    role={c['role']:<10} model={c['model']:<28} "
          f"num_ctx={c['num_ctx']} think={c['think']}")

gen = [c for c in CALLS if c["role"] == "generator"]
dec = [c for c in CALLS if c["role"] == "decision"]
assert gen and dec, "thieu loi goi cua 1 trong 2 vai tro"
assert {c["model"] for c in gen} == {"devstral-small-2:latest"}, \
    f"generator model sai: {{c['model'] for c in gen}}"
assert {c["model"] for c in dec} == {"qwen3:14b"}, "decision model sai"
assert {c["num_ctx"] for c in gen} == {32768}, f"generator num_ctx sai: {[c['num_ctx'] for c in gen]}"
assert {c["num_ctx"] for c in dec} == {16384}, f"decision num_ctx sai: {[c['num_ctx'] for c in dec]}"
assert {c["think"] for c in dec} == {False}, "decision phai gui think=False"
print("\n  OK: generator=devstral-small-2 num_ctx=32768 | "
      "decision=qwen3:14b num_ctx=16384 think=False")

# Trong duong chay that, response decision co <think> chua REJECT -> phai ACCEPT.
import json  # noqa: E402

newest = max((BENCH / "results").glob("pipeline_summary_*.json"),
             key=lambda p: p.stat().st_mtime)
data = json.loads(newest.read_text(encoding="utf-8"))
d0 = data["stages"]["stage6"]["decisions"][0]
print(f"  quyet dinh tu pipeline that: accepted={d0['accepted']} (response co <think> chua REJECT)")
assert d0["accepted"] is True, "Pipeline that doc nham REJECT trong <think>!"
print("  OK: lop loc <think> hoat dong trong duong chay that")

print()
print("=" * 72)
print("TEST num_ctx + think: PASS")
print("=" * 72)
