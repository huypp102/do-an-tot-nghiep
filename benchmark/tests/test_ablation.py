"""PHAN 2 + 4 -- kiem chung ABLATION tren repo gia.

Xac nhan:
  1. Hai prompt chi khac DUNG phan context graph (diff tung dong).
  2. Hai nhanh cach ly: nhanh sau khong dung nham extension cua nhanh truoc.
  3. VACUOUS gan dung (repo_zeta dung co y).
  4. CONFOUNDED gan dung khi num_ctx qua nho.
  5. ablation_report.md/json sinh ra, so sanh theo cap, co ghi chu co mau nho.
"""
import difflib
import json
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
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

CALLS = []
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
        CALLS.append({"role": role, "seed": kwargs.get("seed"),
                      "temperature": kwargs.get("temperature"),
                      "num_ctx": kwargs.get("num_ctx")})
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

BUILDS = []


def fake_build_crate(result, venv_python, timeout_sec=600):
    """Builder gia: ghi module Python dong vai extension. GHI LAI nhanh nao da
    build gi, de kiem tra cach ly."""
    name = result.function_name
    work_dir = Path(result.ext_root)
    body = KERNEL.get(name) or IMPL.get(name)
    if body is None:
        result.status = cb.BUILD_FAILED
        result.output = f"builder gia khong co ban cai dat cho '{name}'"
        return result
    marker = f"# ARM_MARKER={result.tier}|{name}\n"
    (work_dir / f"{result.ext_module}.py").write_text(marker + body, encoding="utf-8")
    BUILDS.append({"function": name, "crate_dir": result.crate_dir,
                   "ext_module": result.ext_module})
    result.status = cb.BUILT_OK
    result.output = "builder gia"
    return result


cb.build_crate = fake_build_crate
cb.toolchain_available = lambda: (True, "")

import run_pipeline  # noqa: E402

original_load = run_pipeline.load_config
WORK = _work_root("rtb_work_ab")


def make_cfg(num_ctx=32768, repos=("repo_gamma", "repo_zeta")):
    def patched(*a, **k):
        cfg = original_load(*a, **k)
        cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
        cfg["llm"]["local"]["generator_num_ctx"] = num_ctx
        cfg["dataset"]["enabled"] = True
        cfg["dataset"]["source_root"] = str(BENCH / "data" / "fake_dataset")
        cfg["graph"]["candidate_pool"] = 30
        cfg["graph"]["top_k_translate"] = 5
        cfg["benchmark"].update({"iterations": 5, "warmup": 2})
        cfg["decision_gate"]["enabled"] = False
        cfg["repo_oracle"]["enabled"] = True
        cfg["repo_oracle"]["work_root"] = WORK
        cfg["repo_oracle"]["keep_venv"] = False
        cfg["ablation"]["enabled"] = True
        cfg["_only_repos"] = list(repos)
        return cfg
    return patched


# Chi chay 2 repo can thiet -- loc trong resolve_dataset_repos.
import input.intake as intake  # noqa: E402

_orig_resolve = intake.resolve_dataset_repos
ONLY = {"repo_gamma", "repo_zeta"}
intake.resolve_dataset_repos = lambda root, mx=0: [
    p for p in _orig_resolve(root, 0) if p.name in ONLY
]
run_pipeline.resolve_dataset_repos = intake.resolve_dataset_repos

errs = []

print("=" * 78)
print("LUOT 1 -- ablation binh thuong (num_ctx du rong)")
print("=" * 78)
run_pipeline.load_config = make_cfg(num_ctx=32768)
code = run_pipeline.main()
print(f"\n### EXIT CODE = {code}")

results = BENCH / "results"
newest_ds = max(results.glob("dataset_summary_*.json"), key=lambda p: p.stat().st_mtime)
ts = json.loads(newest_ds.read_text(encoding="utf-8"))["timestamp"]

# ---------------------------------------------------------------- 1. diff prompt
print("\n" + "=" * 78)
print("KIEM CHUNG 1 -- hai prompt chi khac phan context graph")
print("=" * 78)
prompt_dir = Path(WORK) / "repo_gamma" / ".rtb_prompts"
pairs = 0
for g in sorted(prompt_dir.glob("*.graph.generate.txt")):
    n = g.with_name(g.name.replace(".graph.", ".none."))
    if not n.exists():
        continue
    pairs += 1
    gl = g.read_text(encoding="utf-8").splitlines()
    nl = n.read_text(encoding="utf-8").splitlines()
    diff = list(difflib.unified_diff(nl, gl, lineterm="", n=0))
    added = [d[1:] for d in diff if d.startswith("+") and not d.startswith("+++")]
    removed = [d[1:] for d in diff if d.startswith("-") and not d.startswith("---")]
    joined = "\n".join(added)
    ok_ctx = "Context lân cận của hotspot" in joined
    ok_no_removed = len(removed) == 0
    print(f"  {g.name.split('.')[0]:<22} them {len(added)} dong, xoa {len(removed)} dong"
          f"  | chi them khoi context: {ok_ctx and ok_no_removed}")
    if not ok_ctx:
        errs.append(f"{g.name}: phan them khong phai khoi context graph")
    if not ok_no_removed:
        errs.append(f"{g.name}: nhanh graph BO MAT {len(removed)} dong so voi none: {removed[:3]}")
if pairs == 0:
    errs.append("khong tim thay cap prompt graph/none nao")
else:
    print(f"  -> {pairs} cap prompt duoc doi chieu")

# ------------------------------------------------------------- 2. cach ly nhanh
print("\n" + "=" * 78)
print("KIEM CHUNG 2 -- hai nhanh dung crate RIENG, co xac minh go cai")
print("=" * 78)
gamma = json.loads((results / f"repo_summary_{ts}_repo_gamma.json").read_text(encoding="utf-8"))
crate_dirs = {}
for arm, data in (gamma.get("arms") or {}).items():
    pd = (data.get("stages") or {}).get("phase_d") or {}
    crate_dirs[arm] = sorted({Path(v["crate_dir"]).parent.name for v in pd.values()})
    print(f"  nhanh {arm:<7} thu muc crate: {crate_dirs[arm]}")
if len(crate_dirs) == 2:
    a, b = crate_dirs.values()
    if set(a) & set(b):
        errs.append(f"hai nhanh DUNG CHUNG thu muc crate: {set(a) & set(b)}")
    else:
        print("  -> OK: khong dung chung thu muc crate")
else:
    errs.append(f"mong doi 2 nhanh, thay {list(crate_dirs)}")

iso = (gamma.get("arms", {}).get("none", {}).get("stages") or {}).get("isolation")
print(f"  bao cao go cai (nhanh 'none'): {json.dumps(iso, ensure_ascii=False)[:200]}")
if iso is None:
    errs.append("thieu bao cao go cai extension cho nhanh thu 2")
elif iso.get("uninstalled", {}).get("still_importable"):
    errs.append(f"van import duoc sau khi go: {iso['uninstalled']['still_importable']}")

# --------------------------------------------------------------- 3. VACUOUS
print("\n" + "=" * 78)
print("KIEM CHUNG 3 -- VACUOUS tren repo_zeta")
print("=" * 78)
zeta = json.loads((results / f"repo_summary_{ts}_repo_zeta.json").read_text(encoding="utf-8"))
by = {h["function"]: h for h in zeta["hotspots"]}
for name in ("tally", "always_used"):
    h = by.get(name)
    if h is None:
        print(f"  {name:<14} THIEU")
        errs.append(f"repo_zeta thieu hotspot {name}")
        continue
    print(f"  {name:<14} reason={h['reason']:<18} rust_call_count={h.get('rust_call_count')} "
          f"vacuous={h.get('vacuous')} reg_free_contrib={h.get('regression_free_contrib')}")
exp = {"tally": True, "always_used": False}
for name, want in exp.items():
    h = by.get(name) or {}
    if bool(h.get("vacuous")) != want:
        errs.append(f"repo_zeta/{name}: vacuous={h.get('vacuous')}, mong doi {want}")
if by.get("tally", {}).get("regression_free_contrib"):
    errs.append("tally VACUOUS ma van tinh vao regression-free")
print(f"  hybrid_tests={(zeta.get('metrics') or {}).get('hybrid_tests')} "
      f"n_vacuous={(zeta.get('metrics') or {}).get('n_vacuous')} "
      f"reg_free_rate={(zeta.get('metrics') or {}).get('regression_free_rate')}")

# ------------------------------------------------------- 4. bao cao ablation
print("\n" + "=" * 78)
print("KIEM CHUNG 4 -- ablation_report")
print("=" * 78)
ab_js = results / f"ablation_report_{ts}.json"
ab_md = results / f"ablation_report_{ts}.md"
if not (ab_js.exists() and ab_md.exists()):
    errs.append("thieu ablation_report_*.json/.md")
else:
    paired = json.loads(ab_js.read_text(encoding="utf-8"))
    print(f"  n_common={paired['n_common']} n_confounded={paired['n_confounded']} "
          f"n_generated_both={paired['n_generated_both']}")
    for m, c in paired["comparisons"].items():
        print(f"    {m:<16} cap={c['n_pairs']} ca2dung={c['both_ok']} "
              f"graph_only={c['graph_only']} none_only={c['none_only']} "
              f"ca2sai={c['both_fail']} batdong={c['n_discordant']}")
    txt = ab_md.read_text(encoding="utf-8")
    if "CỠ MẪU NHỎ" not in txt:
        errs.append("bao cao khong ghi chu co mau nho du so cap bat dong nho")
    else:
        print("  -> OK: co ghi chu CO MAU NHO, khong ket luan thong ke")
    for arm, s in (paired.get("per_arm_summary") or {}).items():
        print(f"    nhanh {arm:<7} token TB={s['avg_prompt_tokens']} "
              f"vong sua TB={s['avg_fix_rounds']} giay LLM TB={s['avg_llm_seconds']} "
              f"bi cat={s['n_truncated']}")
    gs = (paired.get("per_arm_summary") or {}).get("graph", {}).get("avg_prompt_tokens")
    ns = (paired.get("per_arm_summary") or {}).get("none", {}).get("avg_prompt_tokens")
    if gs is not None and ns is not None and not gs > ns:
        errs.append(f"nhanh graph phai co prompt DAI hon: {gs} vs {ns}")

# ---------------------------------------------------- 5. seed tat dinh
print("\n" + "=" * 78)
print("KIEM CHUNG 5 -- seed tat dinh + temperature co dinh")
print("=" * 78)
import ablation as ab  # noqa: E402

s1 = ab.seed_for(1234, "repo_gamma", "sum_squares", "graph", 0)
s2 = ab.seed_for(1234, "repo_gamma", "sum_squares", "graph", 0)
s3 = ab.seed_for(1234, "repo_gamma", "sum_squares", "none", 0)
print(f"  seed(graph)={s1} lap lai={s2} seed(none)={s3}")
if s1 != s2:
    errs.append("seed KHONG tat dinh")
if s1 == s3:
    errs.append("hai nhanh nhan cung seed (phai khac)")
gen_calls = [c for c in CALLS if c["role"] == "generator"]
temps = {c["temperature"] for c in gen_calls}
print(f"  temperature dung o {len(gen_calls)} loi goi generator: {temps}")
if temps != {0.2}:
    errs.append(f"temperature khong phai 0.2 dong nhat: {temps}")
if any(c["seed"] is None for c in gen_calls):
    errs.append("co loi goi generator khong truyen seed")

print()
if errs:
    print(f"### ABLATION: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### ABLATION: PASS")
