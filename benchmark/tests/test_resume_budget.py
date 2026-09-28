"""PHAN 3.4 + 4 -- kiem chung --resume va max_wall_hours.

Xac nhan:
  1. max_wall_hours=0 -> dung ngay, GHI ket qua do dang, exit code 3.
  2. --resume -> bo qua repo da xong (doc lai file), khong chay lai.
  3. --resume o muc (repo, nhanh) -> nap lai arm_cache, KHONG goi lai LLM.
  4. --dry-run -> in ke hoach, khong chay gi, co ghi quy tac chon mau.
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


LLM_CALLS = {"n": 0}
IMPL = {
    "tally": "def tally(values):\n    return float(sum(float(v) for v in values))\n",
    "always_used": (
        "def always_used(values):\n"
        "    return max([float(v) for v in values]) if values else 0.0\n"
    ),
}


class MockBackend:
    name = "mock"

    def chat(self, messages, system=None, **kwargs):
        LLM_CALLS["n"] += 1
        text = messages[-1]["content"] if messages else ""
        if system and "ĐÁNH GIÁ" in system:
            return "## Decision\nACCEPT\n\n## Continue\nSTOP\n\n## Next strategy\nnone"
        name = next((c for c in IMPL if f"`{c}`" in text), "?")
        return (f"## Rust code\n```rust\n// {name}\n```\n\n"
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
    body = IMPL.get(result.function_name)
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

import run_experiment1 as exp  # noqa: E402

# Chi dung repo_zeta (nho, co test) de lượt chay ngan.
FAKE_DS = BENCH / "data" / "fake_dataset"
import input.intake as intake  # noqa: E402

_orig = intake.resolve_dataset_repos
intake.resolve_dataset_repos = lambda root, mx=0: [
    p for p in _orig(str(FAKE_DS), 0) if p.name == "repo_zeta"
]

_orig_load = exp.load_config


def make_cfg(**over):
    def patched(*a, **k):
        cfg = _orig_load(*a, **k)
        cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
        cfg["dataset"].update({"enabled": True, "source_root": str(FAKE_DS)})
        cfg["graph"].update({"candidate_pool": 30, "top_k_translate": 3})
        cfg["benchmark"].update({"iterations": 3, "warmup": 1})
        cfg["decision_gate"]["enabled"] = False
        cfg["repo_oracle"].update({
            "enabled": True, "work_root": _work_root("rtb_work_rs"), "keep_venv": False,
        })
        cfg["ablation"].update({"enabled": True})
        cfg["experiment"] = {
            "max_wall_hours": 1, "screening_limit": 5,
            "min_replayable_hotspots": 1, "n_repos": 1, "n_backup_repos": 0,
        }
        cfg.update(over)
        return cfg
    return patched


errs = []
RESULTS = BENCH / "results"

# ------------------------------------------------------------------ 1. dry-run
print("=" * 78)
print("KIEM CHUNG 1 -- --dry-run: in ke hoach, khong chay gi")
print("=" * 78)
exp.load_config = make_cfg()
code = exp.main(["--run-id", "t_dry", "--dry-run", "--skip-preflight"])
print(f"  exit code = {code}")
sel = RESULTS / "t_dry" / "selection.json"
meta = RESULTS / "t_dry" / "metadata.json"
if code != 0:
    errs.append(f"--dry-run exit {code}, mong doi 0")
if not sel.exists():
    errs.append("--dry-run khong ghi selection.json")
else:
    d = json.loads(sel.read_text(encoding="utf-8"))
    print(f"  dry_run={d.get('dry_run')} ung_vien={d.get('candidates')}")
    if not d.get("dry_run"):
        errs.append("selection.json khong danh dau dry_run")
    if d.get("selected"):
        errs.append("--dry-run ma van chon repo (dang cham vao viec sang)")
if meta.exists():
    m = json.loads(meta.read_text(encoding="utf-8"))
    has_rule = bool((m.get("selection") or {}).get("rule"))
    print(f"  metadata co ghi QUY TAC chon mau: {has_rule}")
    if not has_rule:
        errs.append("metadata.json khong ghi quy tac chon mau")
else:
    errs.append("--dry-run khong ghi metadata.json")
if (RESULTS / "t_dry" / "repos").exists() and any((RESULTS / "t_dry" / "repos").iterdir()):
    errs.append("--dry-run ma van chay repo")

# ---------------------------------------------------- 2. max_wall_hours = 0
print()
print("=" * 78)
print("KIEM CHUNG 2 -- max_wall_hours=0: dung ngay, ghi ket qua do dang")
print("=" * 78)
exp.load_config = make_cfg()
code = exp.main(["--run-id", "t_budget", "--skip-preflight", "--max-wall-hours", "0"])
print(f"  exit code = {code} (mong doi 3 = dung som)")
if code != 3:
    errs.append(f"max_wall_hours=0 -> exit {code}, mong doi 3")
rep = RESULTS / "t_budget" / "report.md"
print(f"  co report.md du dung som: {rep.exists()}")
if not rep.exists():
    errs.append("dung som ma khong ghi report.md")

# ------------------------------------------- 3. chay day du roi resume
print()
print("=" * 78)
print("KIEM CHUNG 3 -- chay day du, roi --resume khong goi lai LLM")
print("=" * 78)
LLM_CALLS["n"] = 0
exp.load_config = make_cfg()
code = exp.main(["--run-id", "t_resume", "--skip-preflight"])
first_calls = LLM_CALLS["n"]
print(f"  luot 1: exit={code}, so loi goi LLM={first_calls}")
if first_calls == 0:
    errs.append("luot 1 khong goi LLM lan nao -> khong kiem duoc resume")
done = sorted(p.name for p in (RESULTS / "t_resume" / "repos").glob("*.json"))
cache = sorted(p.name for p in (RESULTS / "t_resume" / "arm_cache").glob("*.json"))
print(f"  repo da ghi: {done}")
print(f"  arm_cache  : {cache}")
if not done:
    errs.append("khong ghi ket qua tung repo")
if len(cache) < 2:
    errs.append(f"mong doi >=2 file arm_cache (2 nhanh), thay {cache}")

LLM_CALLS["n"] = 0
code2 = exp.main(["--run-id", "t_resume", "--skip-preflight", "--resume"])
second_calls = LLM_CALLS["n"]
print(f"  luot 2 (--resume): exit={code2}, so loi goi LLM={second_calls}")
if second_calls != 0:
    errs.append(f"--resume van goi LLM {second_calls} lan (phai la 0)")

# --------------------------- 4. resume o muc nhanh: xoa file repo, giu cache
print()
print("=" * 78)
print("KIEM CHUNG 4 -- resume o muc (repo, nhanh): xoa ket qua repo, giu arm_cache")
print("=" * 78)
for p in (RESULTS / "t_resume" / "repos").glob("*.json"):
    p.unlink()
LLM_CALLS["n"] = 0
code3 = exp.main(["--run-id", "t_resume", "--skip-preflight", "--resume"])
third_calls = LLM_CALLS["n"]
print(f"  exit={code3}, so loi goi LLM={third_calls} (phai la 0 vi arm_cache con)")
if third_calls != 0:
    errs.append(f"resume muc nhanh van goi LLM {third_calls} lan (arm_cache khong duoc dung)")

print()
if errs:
    print(f"### RESUME + NGAN SACH: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### RESUME + NGAN SACH: PASS")
