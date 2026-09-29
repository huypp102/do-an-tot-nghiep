"""Kiem chung PHA 0 -> 5 cua ban chuan bi pilot 2.

  0.1 scipy co trong requirements.txt
  0.2 setup_linux.sh goi gdown KHONG con `--id`
  0.3 subprocess chay code repo la KHONG treo khi git hoi dang nhap
  1.  cai them requirements-dev/test-requirements
  2.  ten trung -> uu tien cung file, roi qualified_name, con lai AMBIGUOUS_NAME
  3.  PSG day du (class/global/inheritance/ownership) + KHONG pha PSG rut gon
  4.  Decision Gate 3 tang doc lap + graph_confidence + phu thuoc cap HAM
  5.  noise floor: nhanh graph2, so bat dong nhieu vs hieu ung
"""
import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

errs: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'SAI'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        errs.append(f"{label}" + (f" -- {detail}" if detail else ""))


# ===========================================================================
print("=" * 78)
print("PHA 0.1 + 0.2 -- sua tay tu pilot 1 da vao code")
print("=" * 78)
req = (BENCH / "requirements.txt").read_text(encoding="utf-8")
check("scipy" in req, "requirements.txt co scipy",
      "networkx.pagerank can scipy -- pilot 1 phai cai tay")

setup = (BENCH / "scripts" / "setup_linux.sh").read_text(encoding="utf-8")
gdown_lines = [l for l in setup.splitlines() if "gdown" in l and "DATASET_DRIVE_ID" in l]
check(bool(gdown_lines), "setup_linux.sh co dong goi gdown")
check(all("--id" not in l for l in gdown_lines),
      "gdown KHONG con tuy chon --id", str(gdown_lines)[:100])

# ===========================================================================
print()
print("=" * 78)
print("PHA 0.3 -- subprocess chay code repo la KHONG treo cho nhap")
print("=" * 78)
from stage5_compiler_in_the_loop.repo_runner import build_child_env  # noqa: E402

env = build_child_env()
for var, want in (("GIT_TERMINAL_PROMPT", "0"), ("GIT_ASKPASS", "/bin/true")):
    check(env.get(var) == want, f"env co {var}={want}", f"thuc te={env.get(var)!r}")

# Lenh THAT: git ls-remote vao repo khong ton tai. Neu khong chan prompt,
# git se dung cho nhap username va lenh nay treo tới timeout.
if subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["git", "ls-remote", "https://github.com/khong-ton-tai/x"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, env=env, timeout=60,
        )
        elapsed = time.perf_counter() - t0
        check(True, f"git ls-remote tra ve sau {elapsed:.1f}s (exit={proc.returncode})",
              "fail nhanh, khong treo cho nhap")
        check(proc.returncode != 0, "git that bai (dung -- repo khong ton tai)")
        check(elapsed < 55, "khong treo den timeout", f"{elapsed:.1f}s")
    except subprocess.TimeoutExpired:
        check(False, "git ls-remote TREO den timeout",
              "prompt dang nhap chua bi chan -- dung loi cua pilot 1")
else:
    print("  (bo qua phep thu git that: may nay khong co git)")

# Moi subprocess trong code san pham phai chan stdin.
prod_files = [
    p for p in BENCH.rglob("*.py")
    if "subprocess.run(" in p.read_text(encoding="utf-8", errors="replace")
    and not any(x in p.parts for x in ("tests", "__pycache__", ".venv", "data"))
]
missing = []
for p in prod_files:
    src = p.read_text(encoding="utf-8", errors="replace")
    if src.count("stdin=subprocess.DEVNULL") < src.count("subprocess.run("):
        missing.append(p.relative_to(BENCH).as_posix())
check(not missing, f"moi subprocess ({len(prod_files)} file) da chan stdin", str(missing))

# ===========================================================================
print()
print("=" * 78)
print("PHA 1 -- cai them phu thuoc test")
print("=" * 78)
rr_src = (BENCH / "stage5_compiler_in_the_loop" / "repo_runner.py").read_text(encoding="utf-8")
for fname in ("requirements-dev.txt", "dev-requirements.txt", "test-requirements.txt"):
    check(fname in rr_src, f"install_repo tim {fname}")
check('".[test]"' in rr_src or ".[test]" in rr_src, "thu ca extras .[test]")
# Thu tu: file dev phai duoc cai SAU requirements.txt chinh.
i_main = rr_src.index('req = work_dir / "requirements.txt"')
i_dev = rr_src.index("requirements-dev.txt")
check(i_dev > i_main, "phu thuoc test cai SAU requirements.txt chinh")

# ===========================================================================
print()
print("=" * 78)
print("PHA 2 -- resolve ten trung")
print("=" * 78)
from stage1_profiling.module_resolve import _pick_node  # noqa: E402


class _N:
    def __init__(self, nid, name, qual, file, line=1):
        self.id, self.name, self.qualified_name = nid, name, qual
        self.file, self.lineno_start = file, line


# (a) cung file voi noi phat hien -> chon duoc, khong ambiguous
a = _N("a.py::A.__init__#L1", "__init__", "A.__init__", "a.py")
b = _N("b.py::B.__init__#L1", "__init__", "B.__init__", "b.py")
node, amb, _ = _pick_node("__init__", [a, b], hint_file="b.py")
check(node.id == b.id and not amb, "(a) uu tien node cung file voi noi phat hien")

# (b) khong co hint, nhung qualified_name phan biet duoc va 1 node sau nhat
deep = _N("c.py::C.Inner.__init__#L5", "__init__", "C.Inner.__init__", "c.py")
flat = _N("c.py::__init__#L1", "__init__", "__init__", "c.py")
node, amb, _ = _pick_node("__init__", [deep, flat], hint_file=None)
check(node.id == deep.id and not amb, "(b) phan biet bang qualified_name day du")

# (c) hoan toan khong phan biet duoc -> lay dau tien NHUNG gan co
x = _N("x.py::__init__#L1", "__init__", "__init__", "x.py")
y = _N("y.py::__init__#L1", "__init__", "__init__", "y.py")
node, amb, why = _pick_node("__init__", [x, y], hint_file=None)
check(amb is True, "(c) khong phan biet duoc -> gan AMBIGUOUS_NAME")
check(node.id == x.id, "(c) van lay node dau tien (giu hanh vi cu)")
check("khớp 2 node" in why or "2 node" in why, "(c) ly do co ghi so node trung", why[:80])

# 1 node -> khong bao gio ambiguous
node, amb, _ = _pick_node("f", [a], hint_file=None)
check(not amb, "1 node duy nhat -> khong ambiguous")

# ===========================================================================
print()
print("=" * 78)
print("PHA 3 -- PSG day du, KHONG pha PSG rut gon")
print("=" * 78)
from stage0_graph.builder import build_graph, discover_python_files  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="rtb_psg_"))
(tmp / "m.py").write_text(
    "import os\n"
    "MAX = 10\n"
    "thresh = 0.5\n"
    "\n"
    "class Base:\n"
    "    def run(self):\n"
    "        return 1\n"
    "\n"
    "class Child(Base):\n"
    "    def run(self):\n"
    "        return self.helper()\n"
    "    def helper(self):\n"
    "        return 2\n",
    encoding="utf-8",
)
(tmp / "other.py").write_text("import m\n\ndef go():\n    return m.MAX\n", encoding="utf-8")
g = build_graph(discover_python_files(tmp), tmp)

check(len(g.classes) == 2, "trich duoc 2 class", f"{sorted(c.name for c in g.classes.values())}")
check(len(g.global_vars) == 2, "trich duoc 2 bien toan cuc",
      f"{sorted(v.name for v in g.global_vars.values())}")
consts = [v.name for v in g.global_vars.values() if v.is_constant_case]
check(consts == ["MAX"], "nhan dien TEN_HANG", str(consts))
inh = [(e.kind, e.resolved) for e in g.inheritance_edges]
check(("superclassOf", True) in inh and ("subclassOf", True) in inh,
      "co ca 2 chieu canh ke thua (superclassOf/subclassOf)", str(inh))
check(len(g.ownership_edges) == 6, "canh so huu hasmember/ismember",
      f"{len(g.ownership_edges)} canh cho 3 method")
kinds = {e.kind for e in g.ownership_edges}
check(kinds == {"hasmember", "ismember"}, "dung 2 loai canh so huu", str(kinds))

# PSG RUT GON phai con nguyen.
check(len(g.files) == 2 and len(g.import_edges) >= 1,
      "PSG rut gon (files/import_edges) con nguyen",
      f"files={len(g.files)} import_edges={len(g.import_edges)}")

# Class cha ngoai scope -> canh unresolved, khong mat thong tin.
tmp2 = Path(tempfile.mkdtemp(prefix="rtb_psg2_"))
(tmp2 / "t.py").write_text(
    "import unittest\n\nclass C(unittest.TestCase):\n    def test_x(self):\n        pass\n",
    encoding="utf-8",
)
g2 = build_graph(discover_python_files(tmp2), tmp2)
unres = [e for e in g2.inheritance_edges if not e.resolved]
check(len(unres) == 1 and unres[0].dst == "TestCase",
      "lop cha NGOAI scope -> canh resolved=False, van ghi ten", str(unres[:1]))

# 3.3 -- context packaging phai them class detail ma KHONG hong voi ham thuong.
from stage3_context_packaging.packager import (  # noqa: E402
    format_context_for_prompt,
    package_context_for,
)

ctx_m = package_context_for("run", g, None)
cd = (ctx_m.get("occurrences") or [{}])[0].get("class_detail")
check(bool(cd), "hotspot la method -> co class_detail tu PSG day du")
if cd:
    check(cd["class_name"] in ("Base", "Child"), "class_detail co ten class", cd["class_name"])
txt = format_context_for_prompt(ctx_m)
check("là method của class" in txt, "prompt co dong noi ro class chua hotspot")

ctx_f = package_context_for("go", g, None)
check(ctx_f.get("found") is True, "ham CAP MODULE van dong goi context duoc")
check((ctx_f["occurrences"][0].get("class_detail")) is None,
      "ham cap module -> class_detail = None (khong bia ra)")
check(bool(format_context_for_prompt(ctx_f)), "prompt cho ham cap module khong rong")

# ===========================================================================
print()
print("=" * 78)
print("PHA 4 -- Decision Gate 3 tang doc lap")
print("=" * 78)
from stage0_graph.confidence import compute_graph_confidence  # noqa: E402
from stage2_decision_gate.dependency_roots import analyze as analyze_deps  # noqa: E402
from stage2_decision_gate import gate3  # noqa: E402

# 4.1 graph_confidence
gc = compute_graph_confidence(g)
check(gc["level"] in ("HIGH", "MEDIUM", "LOW", "OUT_OF_SCOPE"),
      "graph_confidence ra 1 trong 4 muc", gc["level"])
check("edge_quality" in gc, "co edge_quality = exact + 0.6*heuristic",
      str(gc.get("edge_quality")))
from stage0_graph.confidence import edge_quality  # noqa: E402

check(abs(edge_quality(0.5, 0.5) - 0.8) < 1e-9, "cong thuc edge_quality dung",
      f"edge_quality(0.5,0.5)={edge_quality(0.5,0.5)}")
empty = build_graph([], tmp)
check(compute_graph_confidence(empty)["level"] == "OUT_OF_SCOPE",
      "graph khong co ham -> OUT_OF_SCOPE")

# 4.3 phu thuoc cap HAM -- LOI CHAN OAN da het
IMPORTS = ["import cv2", "import numpy as np"]
r_add = analyze_deps("def add(a,b):\n    return a+b\n", IMPORTS)
r_blur = analyze_deps("def blur(i):\n    return cv2.blur(i)\n", IMPORTS)
r_norm = analyze_deps("def n(x):\n    return np.sum(x)\n", IMPORTS)
check(not r_add["is_blocked"], "ham KHONG dung cv2 -> khong bi chan oan",
      f"roots={r_add['dependency_roots']}")
check(r_blur["is_blocked"] and r_blur["blocked_roots"] == ["cv2"],
      "ham DUNG cv2 -> bi chan dung")
check(not r_norm["is_blocked"], "numpy khong nam trong danh sach chan")
check(r_add["n_file_imports"] == 2, "van ghi so import cua FILE de doi chieu")

# 4.2 + 4.4 ba tang doc lap, AND logic
check(gate3.decide("HIGH", "BLOCKED", "HIGH")[0] == "REJECT_BLOCKED",
      "nong + tin CAO nhung BLOCKED -> REJECT (khong bu tru)")
check(gate3.decide("HIGH", "TEST_ONLY", "HIGH")[0] == "REJECT_BLOCKED",
      "TEST_ONLY -> REJECT")
check(gate3.decide("LOW", "FEASIBLE", "HIGH")[0] == "KEEP_PYTHON",
      "kha thi + tin cao nhung KHONG nong -> KEEP_PYTHON")
check(gate3.decide("HIGH", "FEASIBLE", "HIGH")[0] == "SELECT", "ca 3 tang dat -> SELECT")
check(gate3.decide("HIGH", "FEASIBLE", "LOW")[0] == "REVIEW",
      "nong + kha thi nhung confidence LOW -> REVIEW")
check(gate3.decide("LOW_CONFIDENCE", "FEASIBLE", "HIGH")[0] == "REVIEW",
      "chua do duoc do nong -> REVIEW, khong phai KEEP_PYTHON")

# LOW_CONFIDENCE khac LOW
lvl, _ = gate3.classify_hotspot_level(None, None, has_dynamic=False)
check(lvl == "LOW_CONFIDENCE", "khong co so lieu -> LOW_CONFIDENCE (khong phai LOW)")
lvl, _ = gate3.classify_hotspot_level(0.1, None, has_dynamic=True)
check(lvl == "LOW", "do duoc va that su khong nong -> LOW")

# 4.5 simple_label tuong thich nguoc
from stage2_decision_gate.gate import LABEL_CANDIDATE, LABEL_SKIP, LABEL_VECTORIZE  # noqa: E402

check(gate3.simple_label("SELECT") == LABEL_CANDIDATE, "SELECT -> candidate")
check(gate3.simple_label("REVIEW") == LABEL_CANDIDATE, "REVIEW -> candidate")
check(gate3.simple_label("REJECT_BLOCKED") == LABEL_SKIP, "REJECT_BLOCKED -> skip")
check(gate3.simple_label("KEEP_PYTHON") == LABEL_VECTORIZE, "KEEP_PYTHON -> vectorize")

# translation_unit
unit, why = gate3.classify_translation_unit("SELECT", 50_000, 0.2)
check(unit == "BATCH_CALLER", "goi 50k lan, 4us/lan -> BATCH_CALLER", why[:70])
unit, _ = gate3.classify_translation_unit("SELECT", 50_000, 5000.0)
check(unit == "FUNCTION", "goi 50k lan nhung 100us/lan -> FUNCTION")
unit, _ = gate3.classify_translation_unit("SELECT", 5, 100.0)
check(unit == "FUNCTION", "goi it lan -> FUNCTION")
unit, _ = gate3.classify_translation_unit("REVIEW", 50_000, 0.2)
check(unit == "FUNCTION", "chi gan BATCH_CALLER cho hotspot SELECT")

# Chay that tren repo_eta: ham test bi loai, ham san pham duoc chon.
REPO_ETA = BENCH / "data" / "fake_dataset" / "repo_eta"
g_eta = build_graph(discover_python_files(REPO_ETA), REPO_ETA)
verdicts = gate3.evaluate_functions(g_eta)
sel = sorted(n for n, v in verdicts.items() if v.decision == "SELECT")
test_rejected = [n for n, v in verdicts.items()
                 if n.startswith("test_") and v.feasibility == "TEST_ONLY"]
check(set(sel) == {"add", "sub", "mul", "div", "sum_list"},
      "gate3 chon dung 5 ham san pham", str(sel))
check(len(test_rejected) >= 6, "ham test bi gan TEST_ONLY", f"{len(test_rejected)} ham")
s = gate3.summarize(verdicts, gc)
for key in ("by_decision", "by_hotspot_level", "by_feasibility", "by_confidence_level",
            "by_translation_unit"):
    check(key in s, f"summarize co {key}")
one = next(iter(s["verdicts"].values()))
for key in ("funcrank_static", "funcrank_dynamic", "rank_source", "simple_label"):
    check(key in one, f"verdict giu {key}")

# ===========================================================================
print()
print("=" * 78)
print("PHA 5 -- noise floor")
print("=" * 78)
import ablation as ab  # noqa: E402

cfg_on = {"ablation": {"enabled": True, "arms": ["graph", "none"], "noise_floor": True}}
cfg_off = {"ablation": {"enabled": True, "arms": ["graph", "none"], "noise_floor": False}}
check(ab.arms(cfg_on) == ["graph", "none", "graph2"], "bat noise_floor -> them nhanh graph2",
      str(ab.arms(cfg_on)))
check(ab.arms(cfg_off) == ["graph", "none"], "tat -> chi 2 nhanh")
check(ab.includes_graph_context("graph2") is True,
      "graph2 CO context (no la ban lap cua graph)")


def mk(**flags):
    return ab.ArmResult(arm="x", flags={"generated": True, **flags})


# Nhieu (2) >= hieu ung (1) -> khong duoc ket luan.
per = {
    "graph": {f"h{i}": mk(compiled=True) for i in range(4)},
    "none": {f"h{i}": mk(compiled=(i != 0)) for i in range(4)},
    "graph2": {f"h{i}": mk(compiled=(i > 1)) for i in range(4)},
}
paired = ab.build_pairs(per, min_discordant=1)
nf = paired["noise_floor"]
check(nf["available"] is True, "co khoi noise_floor khi da chay graph2")
row = nf["per_metric"]["compiled"]
check(row["n_discordant_effect"] == 1 and row["n_discordant_noise"] == 2,
      "dem dung bat dong hieu ung vs nhieu", str(row))
check(row["verdict"] == "KHÔNG PHÂN BIỆT ĐƯỢC VỚI NHIỄU",
      "nhieu >= hieu ung -> KHONG PHAN BIET DUOC", row["verdict"])
check(row["conclusive"] is False, "khong duoc danh dau conclusive")

# Hieu ung (3) > nhieu (0) -> vuot nhieu.
per2 = {
    "graph": {f"h{i}": mk(compiled=True) for i in range(4)},
    "none": {f"h{i}": mk(compiled=(i == 3)) for i in range(4)},
    "graph2": {f"h{i}": mk(compiled=True) for i in range(4)},
}
row2 = ab.build_pairs(per2, min_discordant=1)["noise_floor"]["per_metric"]["compiled"]
check(row2["verdict"] == "HIỆU ỨNG VƯỢT NHIỄU",
      "hieu ung > nhieu -> HIEU UNG VUOT NHIEU", str(row2))
check(row2["conclusive"] is True, "duoc danh dau conclusive khi vuot ca nguong cu mau")

# Chua chay graph2 -> phai noi ro la chua co co so ket luan.
nf3 = ab.build_pairs({k: v for k, v in per.items() if k != "graph2"}, 1)["noise_floor"]
check(nf3["available"] is False, "chua chay graph2 -> available=False")
check("KHÔNG biết" in nf3["note"], "co ghi chu ro rang la chua co co so", nf3["note"][:60])

txt = ab.format_report(paired)
check("NHIỄU NỀN" in txt, "bao cao in bang nhieu nen")
check("bất đồng NHIỄU" in txt, "bang co cot bat dong nhieu")

print()
if errs:
    print(f"### CHUAN BI PILOT 2: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### CHUAN BI PILOT 2: PASS")
