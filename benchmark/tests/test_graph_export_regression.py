"""AUDIT_RUN4_v2 muc 5 -- hoi quy: them audit.graph_snapshot KHONG duoc doi
quyet dinh Gate hay danh sach ham duoc chon. Chay repo_pipeline.run_repo_pipeline
that tren data/fake_dataset/repo_eta (llm.enabled=False -> KHONG can Ollama),
1 lan voi export TAT (mo phong "truoc"), 1 lan voi export BAT (that), so
sanh summary["stages"]["stage2"] (labels + candidates) phai GIONG HET.

Kiem them: pcg.json/psg.json duoc ghi dung cho, schema null (khong phai
false) khi build_mode=static, va overlay finish() phu dung len node co san.
"""
import json
import logging
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

ensure_utf8_stdio()

import repo_pipeline  # noqa: E402
from audit import graph_snapshot  # noqa: E402

errs: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'SAI'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        errs.append(label + (f" -- {detail}" if detail else ""))


REPO = BENCH / "data" / "fake_dataset" / "repo_eta"
cfg = load_config()
cfg["llm"]["enabled"] = False
cfg["ablation"]["enabled"] = False
cfg["compiler_loop"]["enabled"] = False
cfg["repo_oracle"]["keep_venv"] = False

results_dir_before = Path(tempfile.mkdtemp(prefix="rtb_gexport_before_"))
results_dir_after = Path(tempfile.mkdtemp(prefix="rtb_gexport_after_"))


def _run(results_dir: Path, run_id: str, export_enabled: bool) -> dict:
    if export_enabled:
        row = repo_pipeline.run_repo_pipeline(
            cfg=cfg, repo_path=REPO, results_dir=results_dir,
            timestamp=run_id, label="repo_eta", benchmark_root=BENCH,
        )
    else:
        # Mo phong "TRUOC KHI CO export": vo hieu hoa 2 diem goi bang no-op,
        # KHONG can git-revert -- cung 1 code path, chi tat phan quan sat.
        orig_write = graph_snapshot.write_snapshot
        orig_overlay = graph_snapshot.overlay_outcomes
        graph_snapshot.write_snapshot = lambda *a, **k: None
        graph_snapshot.overlay_outcomes = lambda *a, **k: None
        try:
            row = repo_pipeline.run_repo_pipeline(
                cfg=cfg, repo_path=REPO, results_dir=results_dir,
                timestamp=run_id, label="repo_eta", benchmark_root=BENCH,
            )
        finally:
            graph_snapshot.write_snapshot = orig_write
            graph_snapshot.overlay_outcomes = orig_overlay
    return row


print("=" * 78)
print("HOI QUY -- co/khong graph export phai ra CUNG mot quyet dinh Gate")
print("=" * 78)
row_before = _run(results_dir_before, "t_before", export_enabled=False)
row_after = _run(results_dir_after, "t_after", export_enabled=True)

s2_before = (row_before.get("stages") or {}).get("stage2") or {}
s2_after = (row_after.get("stages") or {}).get("stage2") or {}

check(s2_before.get("labels") == s2_after.get("labels"),
      "labels (Gate) GIONG HET co/khong export",
      f"before={s2_before.get('labels')} after={s2_after.get('labels')}")
check(s2_before.get("candidates") == s2_after.get("candidates"),
      "candidates (danh sach ham duoc chon) GIONG HET",
      f"before={s2_before.get('candidates')} after={s2_after.get('candidates')}")
check(row_before.get("repo_status") == row_after.get("repo_status"),
      "repo_status GIONG HET", f"{row_before.get('repo_status')} vs {row_after.get('repo_status')}")
# O che do khong LLM, outcome chi la LLM_FAILED (xem docstring mục 5) nen
# KHONG dung outcome lam bang chung -- nhung van kiem no GIONG NHAU, chi
# khong coi no la DU de ket luan "khong doi".
hotspots_before = {h["function"]: h["reason"] for h in row_before.get("hotspots", [])}
hotspots_after = {h["function"]: h["reason"] for h in row_after.get("hotspots", [])}
check(hotspots_before == hotspots_after,
      "reason tung hotspot GIONG HET (du o che do khong LLM reason chi la LLM_FAILED)",
      f"before={hotspots_before} after={hotspots_after}")

print()
print("=" * 78)
print("PCG/PSG duoc ghi dung -- schema null (khong phai false) khi static")
print("=" * 78)
pcg_path = results_dir_after / "graphs" / "repo_eta" / "pcg.json"
psg_path = results_dir_after / "graphs" / "repo_eta" / "psg.json"
check(pcg_path.exists(), "pcg.json duoc ghi", str(pcg_path))
check(psg_path.exists(), "psg.json duoc ghi", str(psg_path))

if pcg_path.exists():
    pcg = json.loads(pcg_path.read_text(encoding="utf-8"))
    check(pcg.get("graph_available") is True, "graph_available=true")
    check(pcg.get("metadata", {}).get("build_mode") == "static",
          "build_mode=static dung thuc te (khong bi hard-code sai)")
    nodes = pcg.get("nodes") or []
    check(len(nodes) > 0, f"co node ({len(nodes)})")
    n_dyn_not_null = sum(1 for n in nodes if n.get("funcrank_dynamic") is not None)
    n_exec_not_null = sum(1 for n in nodes if n.get("executed_at_runtime") is not None)
    check(n_dyn_not_null == 0,
          "funcrank_dynamic = null (KHONG phai false) cho MOI node khi static",
          f"{n_dyn_not_null} node khac null")
    check(n_exec_not_null == 0,
          "executed_at_runtime = null (KHONG phai false) cho MOI node khi static",
          f"{n_exec_not_null} node khac null")
    # Khong node nao duoc gan false cho 2 truong nay (phan biet voi null).
    n_dyn_false = sum(1 for n in nodes if n.get("funcrank_dynamic") is False)
    n_exec_false = sum(1 for n in nodes if n.get("executed_at_runtime") is False)
    check(n_dyn_false == 0 and n_exec_false == 0,
          "KHONG co node nao bi gan false (chi null hoac so thuc)")
    test_nodes = [n for n in nodes if n.get("is_test")]
    check(len(test_nodes) >= 6, f"is_test=true cho ham test ({len(test_nodes)} node)")
    # Overlay tu finish() phai da phu outcome len node.
    check(pcg.get("overlay_applied") is True, "overlay_applied=true (Diem ghi 2 da chay)")
    with_outcome = [n for n in nodes if "outcome" in n]
    check(len(with_outcome) > 0, f"co node duoc phu outcome ({len(with_outcome)})")

print()
print("=" * 78)
print("graph_available=false khi pipeline chet SOM (chua co graph)")
print("=" * 78)
results_dir_early = Path(tempfile.mkdtemp(prefix="rtb_gexport_early_"))
bad_cfg = dict(cfg)
# repo_path khong ton tai -> PHA A copy that bai TRUOC khi co graph.
row_early = repo_pipeline.run_repo_pipeline(
    cfg=cfg, repo_path=BENCH / "data" / "fake_dataset" / "__khong_ton_tai__",
    results_dir=results_dir_early, timestamp="t_early", label="ghost_repo",
    benchmark_root=BENCH,
)
check(row_early.get("repo_status") == "INSTALL_FAILED",
      "repo gia lap chet SOM o INSTALL_FAILED (dung truoc gia dinh)",
      str(row_early.get("repo_status")))
early_pcg = results_dir_early / "graphs" / "ghost_repo" / "pcg.json"
check(early_pcg.exists(), "van ghi 1 file du khi chet som (khong im lang)")
if early_pcg.exists():
    early = json.loads(early_pcg.read_text(encoding="utf-8"))
    check(early.get("graph_available") is False,
          "graph_available=false (KHONG dung 1 file trong day du gia)")

print()
if errs:
    print(f"### GRAPH EXPORT REGRESSION: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### GRAPH EXPORT REGRESSION: PASS")
