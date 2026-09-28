"""PHA G (muc 3) -- chay PHA A-B tren DATASET THAT tren may dev.

llm.enabled=false -> pipeline di het Pha A (copy/venv/cai/chay test) va Pha B
(ghi doi so that, phat lai 2 lan, phan tang kieu) roi dung lai o Stage 3/4 voi
ly do LLM_FAILED. Dung pham vi "Pha A-B" ma yeu cau neu.

Bao cao: thoi gian tung repo, so test baseline, va ly do tung hotspot.
"""
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

ensure_utf8_stdio()

from repo_pipeline import run_repo_pipeline  # noqa: E402

# Dataset thật đọc từ biến môi trường -- KHÔNG hard-code ổ đĩa (PHẦN 3.1).
DATASET = Path(
    os.environ.get("REPOTRANSBENCH_ROOT")
    or "data_repotransbench/data_repotransbench/source_projects/Python"
)
REPOS = ["piskvorky_sqlitedict", "JoshData_pdf-redactor"]

cfg = load_config()
cfg["llm"]["enabled"] = False          # chi Pha A-B
cfg["graph"]["top_k_hotspots"] = 5
cfg["decision_gate"]["enabled"] = False
cfg["repo_oracle"]["enabled"] = True
cfg["repo_oracle"]["work_root"] = str(
    Path(os.environ.get("RTB_WORK_DIR") or tempfile.gettempdir()) / "rtb_work_real"
)
cfg["repo_oracle"]["keep_venv"] = False
cfg["repo_oracle"]["test_timeout_sec"] = 300
cfg["benchmark"].update({"iterations": 5, "warmup": 2})

results_dir = BENCH / "results"
ts = time.strftime("%Y%m%d_%H%M%S")

rows = []
for name in REPOS:
    repo = DATASET / name
    if not repo.exists():
        print(f"  BO QUA {name}: khong ton tai")
        continue
    t0 = time.perf_counter()
    row = run_repo_pipeline(
        cfg=cfg, repo_path=repo, results_dir=results_dir,
        timestamp=ts, label=name, benchmark_root=BENCH,
    )
    row["_wall_sec"] = time.perf_counter() - t0
    rows.append(row)

print("\n" + "=" * 78)
print("PHA A-B TREN DATASET THAT (may dev, khong LLM, khong cargo)")
print("=" * 78)
for r in rows:
    st = r.get("stages", {})
    bt = st.get("baseline_tests") or {}
    print(f"\n### {r['label']}")
    print(f"  thoi gian           : {r['_wall_sec']:.1f}s")
    print(f"  repo_status         : {r['repo_status']}")
    if r.get("status_note"):
        print(f"  ghi chu             : {str(r['status_note'])[:160]}")
    s0 = st.get("stage0") or {}
    print(f"  Stage 0             : {s0.get('n_files')} file, {s0.get('n_functions')} ham")
    print(f"  hotspot top-K       : {s0.get('functions')}")
    print(f"  bo test baseline    : ok={bt.get('ok')} pass={bt.get('n_passed')}/{bt.get('n_total')} "
          f"({bt.get('duration_sec')}s)")
    if bt.get("error"):
        print(f"     loi test         : {str(bt['error'])[:200]}")
    print("  ly do tung hotspot  :")
    for h in r.get("hotspots") or []:
        print(f"     {h['function']:<26} {h['reason']:<22} tier={h.get('tier') or '-':<14} "
              f"n_calls={h.get('n_captured_calls')}")
        if h.get("observed_arg_types"):
            print(f"        kieu doi so that: {h['observed_arg_types']}")
        elif h.get("detail"):
            print(f"        {str(h['detail'])[:130]}")

print(f"\nTong thoi gian 2 repo: {sum(r['_wall_sec'] for r in rows):.1f}s")
