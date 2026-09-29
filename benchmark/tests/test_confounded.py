"""PHAN 2.5 -- kiem chung nhan CONFOUNDED.

Dat num_ctx NAM GIUA do dai 2 nhanh: nhanh `graph` bi cat, nhanh `none` thi
khong. Hotspot bi cat o BAT KY nhanh nao phai bi gan CONFOUNDED va LOAI khoi
moi phep so sanh theo cap -- neu khong, "graph te hon" co the chi la "graph bi
cat mat system prompt".

NGUONG DUOC TINH DONG, khong hard-code: prompt doi do thi ngưỡng cung phai doi.
Ban truoc ghim 550 (dung voi prompt luc do: graph 620 / none 486), roi khoi
huong dan API PyO3 lam CA HAI prompt dai hon nguong -> ca hai bi cat va test
that bai vi ly do chang lien quan gi toi CONFOUNDED.
"""
import json
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
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

IMPL = {
    "tally": "def tally(values):\n    return float(sum(float(v) for v in values))\n",
    "always_used": (
        "def always_used(values):\n"
        "    return max([float(v) for v in values]) if values else 0.0\n"
    ),
    "sum_squares": (
        "def sum_squares(values):\n"
        "    return float(sum(float(v) * float(v) for v in values))\n"
    ),
    "accumulate_inplace": (
        "def accumulate_inplace(buffer, addend):\n"
        "    buffer[:] = [x + addend for x in buffer]\n"
        "    return None\n"
    ),
}
KERNEL = {"scale_point": "def scale_point_kernel(x, y, f):\n    return (x * f, y * f)\n"}
SHIM = {
    "scale_point": (
        "from mypkg.models import Point\n"
        "import scale_point_rsext\n"
        "def scale_point(point, factor):\n"
        "    x, y = scale_point_rsext.scale_point_kernel(point.x, point.y, factor)\n"
        "    return Point(x, y, point.label)\n"
    ),
}


class MockBackend:
    name = "mock"

    def chat(self, messages, system=None, **kwargs):
        text = messages[-1]["content"] if messages else ""
        role = "decision" if (system and "ĐÁNH GIÁ" in system) else "generator"
        if role == "decision":
            return "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone"
        name = ""
        for cand in list(IMPL) + list(KERNEL):
            if f"`{cand}`" in text:
                name = cand
                break
        if name in KERNEL:
            return ("## Rust code\n```rust\n// kernel\n```\n\n"
                    "## Python shim\n```python\n" + SHIM[name] + "```\n\n"
                    "## Optimization strategy\nx.")
        return ("## Rust code\n```rust\n// " + (name or "?") + "\n```\n\n"
                "## Optimization strategy\nx.")

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}])


import stage4_llm_transpile.model_backend as mb  # noqa: E402
import stage5_compiler_in_the_loop.compiler_loop as cl  # noqa: E402
import stage5_compiler_in_the_loop.crate_builder as cb  # noqa: E402

mb.get_model_backend = lambda cfg: MockBackend()
cl.cargo_available = lambda: True
cl.compile_and_classify = lambda crate_dir, timeout_sec=300: cl.CompileResult(ok=True)


def fake_build_crate(result, venv_python, timeout_sec=600):
    body = KERNEL.get(result.function_name) or IMPL.get(result.function_name)
    if body is None:
        result.status = cb.BUILD_FAILED
        result.output = "builder gia khong co ban cai dat"
        return result
    (Path(result.ext_root) / f"{result.ext_module}.py").write_text(body, encoding="utf-8")
    result.status = cb.BUILT_OK
    result.output = "builder gia"
    return result


cb.build_crate = fake_build_crate
cb.toolchain_available = lambda: (True, "")

import input.intake as intake  # noqa: E402
import run_pipeline  # noqa: E402

_orig = intake.resolve_dataset_repos
ONLY = {"repo_gamma", "repo_zeta"}
intake.resolve_dataset_repos = lambda root, mx=0: [
    p for p in _orig(root, 0) if p.name in ONLY
]
run_pipeline.resolve_dataset_repos = intake.resolve_dataset_repos

original_load = run_pipeline.load_config


def _midpoint_num_ctx() -> int:
    """Tinh num_ctx NAM GIUA do dai prompt 2 nhanh, tu chinh prompt that.

    Khong hard-code so: prompt doi thi nguong tu doi theo. Ban truoc ghim 550
    (dung voi prompt luc do: graph 620 / none 486 token), roi khoi huong dan API
    PyO3 lam CA HAI prompt vuot 550 -> ca hai bi cat, va test that bai vi ly do
    chang lien quan gi toi CONFOUNDED.
    """
    from stage4_llm_transpile.generator_agent import build_signature_prompt

    kw = dict(
        function_name="sum_squares",
        hotspot_source="def sum_squares(values):\n    return sum(v * v for v in values)\n",
        context_text="ham goi hotspot: test_sum_squares_basic; hotspot goi: float",
        tier="TIER1_NATIVE",
        observed_types="  - doi so vi tri #0: list[int](n=3)",
        ext_module="sum_squares_rsext",
        io_examples=[{"function": "sum_squares", "args_repr": ["[1, 2, 3]"],
                      "result_repr": "14.0"}],
    )
    n_graph = int(len(build_signature_prompt(**kw, include_graph_context=True)) / 3.5)
    n_none = int(len(build_signature_prompt(**kw, include_graph_context=False)) / 3.5)
    assert n_graph > n_none, f"nhanh graph phai dai hon: {n_graph} vs {n_none}"
    mid = (n_graph + n_none) // 2
    print(f"  do dai prompt uoc tinh: graph={n_graph} token, none={n_none} token")
    print(f"  -> dat num_ctx={mid} (nam giua) de CHI nhanh graph bi cat")
    return mid


NUM_CTX = _midpoint_num_ctx()


def patched(*a, **k):
    cfg = original_load(*a, **k)
    cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
    # NGUONG COT LOI cua phep thu: giua do dai prompt cua 2 nhanh, tinh dong.
    cfg["llm"]["local"]["generator_num_ctx"] = NUM_CTX
    cfg["dataset"]["enabled"] = True
    cfg["dataset"]["source_root"] = str(BENCH / "data" / "fake_dataset")
    cfg["graph"]["candidate_pool"] = 30
    cfg["graph"]["top_k_translate"] = 5
    cfg["benchmark"].update({"iterations": 5, "warmup": 2})
    cfg["decision_gate"]["enabled"] = False
    cfg["repo_oracle"]["enabled"] = True
    cfg["repo_oracle"]["work_root"] = _work_root("rtb_work_cf")
    cfg["repo_oracle"]["keep_venv"] = False
    cfg["ablation"]["enabled"] = True
    return cfg


run_pipeline.load_config = patched

errs = []
print("=" * 78)
print(f"CONFOUNDED -- num_ctx={NUM_CTX}")
print("=" * 78)
code = run_pipeline.main()
print(f"\n### EXIT CODE = {code}")

results = BENCH / "results"
newest_ds = max(results.glob("dataset_summary_*.json"), key=lambda p: p.stat().st_mtime)
ts = json.loads(newest_ds.read_text(encoding="utf-8"))["timestamp"]
gamma = json.loads((results / f"repo_summary_{ts}_repo_gamma.json").read_text(encoding="utf-8"))

print("\n-- token / truncated / confounded theo tung nhanh --")
for arm, data in (gamma.get("arms") or {}).items():
    for h in data["hotspots"]:
        if h.get("prompt_tokens"):
            print(f"  {arm:<7} {h['function']:<22} tokens={h['prompt_tokens']:<6} "
                  f"truncated={h['truncated']} confounded={h['confounded']}")

ab_js = results / f"ablation_report_{ts}.json"
if not ab_js.exists():
    errs.append("thieu ablation_report json")
else:
    paired = json.loads(ab_js.read_text(encoding="utf-8"))
    print(f"\n  n_common={paired['n_common']} n_confounded={paired['n_confounded']}")
    print(f"  CONFOUNDED: {paired['confounded'][:8]}")
    print(f"  n_generated_both (dung de so sanh) = {paired['n_generated_both']}")
    if paired["n_confounded"] == 0:
        errs.append("num_ctx=550 ma KHONG hotspot nao bi gan CONFOUNDED")
    for m, c in paired["comparisons"].items():
        if c["n_pairs"] > paired["n_generated_both"]:
            errs.append(f"{m}: {c['n_pairs']} cap > n_generated_both -> CONFOUNDED chua bi loai")
    g = (paired.get("per_arm_summary") or {}).get("graph", {})
    n = (paired.get("per_arm_summary") or {}).get("none", {})
    print(f"  bi cat: graph={g.get('n_truncated')} none={n.get('n_truncated')}")
    if not (g.get("n_truncated", 0) > n.get("n_truncated", 0)):
        errs.append(f"mong doi graph bi cat NHIEU hon none: "
                    f"{g.get('n_truncated')} vs {n.get('n_truncated')}")

print()
if errs:
    print(f"### CONFOUNDED: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### CONFOUNDED: PASS")
