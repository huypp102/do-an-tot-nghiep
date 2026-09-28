"""2 kiem chung con lai:
  1. CHE DO LEGACY (target.mode=function, 4 ham viraj7) khong doi hanh vi:
     van di duong cu, van do in-process, KHONG di duong dong.
  2. EXIT CODE != 0 khi khong do duoc gi (chi chay 2 repo khong co test).
"""
import json
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

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

errs = []

print("=" * 72)
print("TEST 1 -- _dynamic_mode_selected phan tuyen dung")
print("=" * 72)
import run_pipeline  # noqa: E402

CASES = [
    ({"target": {"mode": "function"}, "dataset": {"enabled": False},
      "repo_oracle": {"enabled": True}}, False, "legacy function -> duong CU"),
    ({"target": {"mode": "repo"}, "dataset": {"enabled": False},
      "repo_oracle": {"enabled": True}}, True, "mode=repo -> duong DONG"),
    ({"target": {"mode": "file"}, "dataset": {"enabled": False},
      "repo_oracle": {"enabled": True}}, True, "mode=file -> duong DONG"),
    ({"target": {"mode": "function"}, "dataset": {"enabled": True},
      "repo_oracle": {"enabled": True}}, True, "dataset -> duong DONG"),
    ({"target": {"mode": "repo"}, "dataset": {"enabled": True},
      "repo_oracle": {"enabled": False}}, False, "repo_oracle tat -> duong CU"),
]
for cfg, expected, desc in CASES:
    got = run_pipeline._dynamic_mode_selected(cfg)
    ok = got == expected
    print(f"  {'OK ' if ok else 'SAI'} {desc:<40} -> dynamic={got}")
    if not ok:
        errs.append(f"{desc}: dynamic={got}, mong doi {expected}")

print()
print("=" * 72)
print("TEST 2 -- CHE DO LEGACY chay nguyen nhu truoc (4 ham viraj7)")
print("=" * 72)
original_load = run_pipeline.load_config


def patched_legacy(*a, **k):
    cfg = original_load(*a, **k)
    cfg["target"]["mode"] = "function"     # LEGACY
    cfg["dataset"]["enabled"] = False
    cfg["llm"]["enabled"] = False
    cfg["benchmark"].update({"iterations": 3, "warmup": 1})
    return cfg


run_pipeline.load_config = patched_legacy
code = run_pipeline.main()
run_pipeline.load_config = original_load
print(f"\n  exit code = {code}")
if code != 0:
    errs.append(f"legacy: exit code {code}, mong doi 0")

results = BENCH / "results"
newest = max(results.glob("pipeline_summary_*.json"), key=lambda p: p.stat().st_mtime)
d = json.loads(newest.read_text(encoding="utf-8"))
print(f"  file: {newest.name}")
print(f"  target_mode = {d.get('target_mode')}  (phai la 'function')")
print(f"  hotspot do duoc: {[h['function'] for h in d['stages']['stage6']['hotspots']]}")
if d.get("target_mode") != "function":
    errs.append("legacy khong ghi target_mode=function")
names = [h["function"] for h in d["stages"]["stage6"]["hotspots"]]
expect_names = ["edge_det", "harris", "hess_corner_det", "im_threshold"]
if names != expect_names:
    errs.append(f"legacy do sai bo ham: {names}")
else:
    print("  OK: van do dung 4 ham viraj7, khong doi")
# Duong legacy KHONG duoc sinh repo_summary_*.json
if "repo_status" in d:
    errs.append("legacy bi lan sang duong dong (co khoa repo_status)")

print()
print("=" * 72)
print("TEST 3 -- EXIT CODE != 0 khi khong do duoc gi")
print("=" * 72)


def patched_nothing(*a, **k):
    cfg = original_load(*a, **k)
    cfg["llm"]["enabled"] = False
    cfg["dataset"]["enabled"] = True
    cfg["dataset"]["source_root"] = str(BENCH / "data" / "fake_dataset")
    cfg["dataset"]["max_repos"] = 2      # repo_alpha, repo_beta -- khong co test
    cfg["repo_oracle"]["enabled"] = True
    cfg["repo_oracle"]["work_root"] = _work_root("rtb_work_x")
    cfg["graph"]["top_k_hotspots"] = 3
    return cfg


run_pipeline.load_config = patched_nothing
code2 = run_pipeline.main()
run_pipeline.load_config = original_load
print(f"\n  exit code = {code2} (mong doi != 0)")
if code2 == 0:
    errs.append(f"khong do duoc gi ma exit code van {code2}")
else:
    print("  OK: that bai khong con im lang")

print()
if errs:
    print(f"### THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### LEGACY + EXIT CODE: PASS")
