"""3 kich ban theo yeu cau:
  1. Rust SAI ket qua -> MISMATCH, khong do speedup, khong lap vong toi uu.
  2. Rust dung, mock luon tra CONTINUE -> dung dung o vong 3 (tran cung).
  3. Rust dung, mock tra STOP o vong 2 -> dung som o vong 2.
"""
import logging
import sys
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

RUST_SRC = "use pyo3::prelude::*;\n#[pyfunction]\nfn f() {}\n"


class MockBackend:
    """decision_script: chuoi quyet dinh tra ve theo tung vong."""

    name = "mock"

    def __init__(self, decision_script):
        self.decision_script = list(decision_script)
        self.decision_calls = 0
        self.generator_calls = 0

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        if system and "ĐÁNH GIÁ" in system:
            i = min(self.decision_calls, len(self.decision_script) - 1)
            verdict = self.decision_script[i]
            self.decision_calls += 1
            return (
                f"## Decision\nACCEPT\n\n## Continue\n{verdict}\n\n"
                f"## Next strategy\n{'Dung SIMD' if verdict == 'CONTINUE' else 'none'}"
            )
        self.generator_calls += 1
        return f"## Rust code\n```rust\n{RUST_SRC}```\n\n## Optimization strategy\nvong moi."

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}])


class AnyFnRegistry(dict):
    """Registry gia: tra ve CUNG 1 ham cho bat ky ten hotspot nao (vi ten
    hotspot do FuncRank chon, khong biet truoc)."""

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def get(self, key, default=None):
        return self._fn

    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        return self._fn


def run_scenario(title, rust_fn, decision_script, expect_rounds, expect_correctness):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)

    import stage4_llm_transpile.model_backend as mb
    backend = MockBackend(decision_script)
    mb.get_model_backend = lambda cfg: backend

    # May nay khong co cargo/maturin -> rebuild that se tra SKIPPED_NO_CARGO
    # va lam DUNG vong lap (dung thiet ke: khong do lai ban cu roi bao speedup
    # gia). Test nay kiem thu TRAN max_rounds nen gia lap rebuild thanh cong.
    import stage5_compiler_in_the_loop.rebuild as rb
    import stage6_benchmark.measure_subprocess as ms
    rb.rebuild_from_draft = lambda d, c, t=300: rb.RebuildResult(rb.REBUILT_OK, "fake")
    rb.restore_original_lib = lambda c: False
    ms.measure_in_subprocess = lambda **kw: ([0.001] * 5, "")

    import run_pipeline
    from stage6_benchmark import bench

    # Gia lap ca 2 phia da san sang: python lam chuan, rust la ban dich.
    py_fn = lambda img: np.zeros_like(np.asarray(img), dtype=np.uint8)  # noqa: E731
    bench.python_pure_pipeline.PIPELINE_REGISTRY = AnyFnRegistry(py_fn)
    bench.rust_pure_pipeline.PIPELINE_REGISTRY = AnyFnRegistry(rust_fn)
    bench.rust_pure_pipeline.HAS_EXT = True

    original_load = run_pipeline.load_config

    def patched(*a, **k):
        cfg = original_load(*a, **k)
        cfg["llm"]["enabled"] = True
        cfg["llm"]["backend"] = "local"
        cfg["llm"]["num_agents"] = 2
        # mode=repo de Stage 3/4 chay -> co Generator Agent cho vong toi uu.
        cfg["target"]["mode"] = "repo"
        # Cac test nay kiem thu duong CHAY CU (run_once). Tu khi co PHA A..F,
        # target.mode=repo mac dinh di duong DONG (repo_pipeline.py), nen phai
        # tat repo_oracle de giu nguyen pham vi kiem thu cua chung.
        cfg.setdefault("repo_oracle", {})["enabled"] = False
        cfg["target"]["source"] = str(BENCH / "data" / "fake_dataset" / "repo_alpha")
        cfg["graph"]["top_k_hotspots"] = 1  # dung 1 hotspot de dem vong cho chuan
        cfg["benchmark"]["iterations"] = 3
        cfg["benchmark"]["warmup"] = 1
        cfg["optimization_loop"]["max_rounds"] = 3
        cfg["compiler_loop"]["enabled"] = False  # khong co cargo tren may nay
        # Tat Decision Gate: test nay kiem thu VONG LAP, khong kiem thu gate.
        # (De bat, hotspot duoc chon co the bi gan nhan vectorization -> Stage
        # 3/4 bi bo qua dung thiet ke, khong con Generator Agent de lap vong.)
        cfg["decision_gate"]["enabled"] = False
        return cfg

    run_pipeline.load_config = patched
    try:
        code = run_pipeline.main()
    finally:
        run_pipeline.load_config = original_load

    # Doc lai summary vua ghi
    import json
    results = BENCH / "results"
    newest = max(results.glob("pipeline_summary_*.json"), key=lambda p: p.stat().st_mtime)
    data = json.loads(newest.read_text(encoding="utf-8"))
    hs = data["stages"]["stage6"]["hotspots"][0]
    corr = (data["stages"].get("correctness") or {}).get(list((data["stages"].get("correctness") or {}).keys() or ["?"])[0], {})

    print(f"\n  ket qua: correctness={hs['correctness']} speedup={hs['speedup']} "
          f"rounds={hs['rounds']} stopped_by_cap={hs['stopped_by_cap']}")
    print(f"  so lan goi Decision Agent : {backend.decision_calls}")
    print(f"  so lan goi Generator Agent: {backend.generator_calls}")
    print(f"  correctness detail: {corr.get('detail','')[:80]}")

    assert code == 0
    assert hs["correctness"] == expect_correctness, \
        f"mong doi {expect_correctness}, nhan {hs['correctness']}"
    assert hs["rounds"] == expect_rounds, \
        f"mong doi rounds={expect_rounds}, nhan {hs['rounds']}"
    return hs, backend


# ---------------------------------------------------------------- KICH BAN 1
wrong_rust = lambda img: np.ones_like(np.asarray(img), dtype=np.uint8)  # noqa: E731
hs1, be1 = run_scenario(
    "KICH BAN 1 -- Rust SAI ket qua ngay tu dau",
    rust_fn=wrong_rust, decision_script=["CONTINUE"],
    expect_rounds=0, expect_correctness="MISMATCH",
)
assert hs1["speedup"] is None, "MISMATCH ma van do speedup!"
assert be1.decision_calls == 0, "MISMATCH ma van goi Decision Agent!"
# Generator duoc goi DUNG 1 lan o Stage 4 (dich lan dau, truoc khi kiem tra
# correctness) -- hop le. Dieu can chung minh la vong TOI UU khong chay:
# rounds=0 va Decision Agent chua tung duoc goi.
assert be1.generator_calls == 1, f"chi duoc goi 1 lan o Stage 4, thuc te {be1.generator_calls}"
print("  OK: MISMATCH chan truoc -- khong do speedup, khong vao vong toi uu")

# ---------------------------------------------------------------- KICH BAN 2
correct_rust = lambda img: np.zeros_like(np.asarray(img), dtype=np.uint8)  # noqa: E731
hs2, be2 = run_scenario(
    "KICH BAN 2 -- Rust dung, mock LUON tra CONTINUE",
    rust_fn=correct_rust, decision_script=["CONTINUE"],
    expect_rounds=3, expect_correctness="MATCH",
)
assert hs2["stopped_by_cap"] is True, "Phai danh dau dung vi cham tran"
assert be2.decision_calls == 3, f"Phai goi Decision dung 3 lan, thuc te {be2.decision_calls}"
print("  OK: dung dung o vong 3, khong lap vong 4")

# ---------------------------------------------------------------- KICH BAN 3
hs3, be3 = run_scenario(
    "KICH BAN 3 -- Rust dung, mock tra STOP o vong 2",
    rust_fn=correct_rust, decision_script=["CONTINUE", "STOP"],
    expect_rounds=2, expect_correctness="MATCH",
)
assert hs3["stopped_by_cap"] is False, "Dung som thi khong phai do cham tran"
assert be3.decision_calls == 2, f"Phai goi Decision dung 2 lan, thuc te {be3.decision_calls}"
print("  OK: dung som o vong 2, khong bi ep chay du 3 vong")

print("\n" + "=" * 72)
print("CA 3 KICH BAN: PASS")
print("=" * 72)
