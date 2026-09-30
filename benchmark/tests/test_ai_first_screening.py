"""Kiem tra _screen_stream() (sang 2 giai doan AI-first/general, lan chay 4)
bang du lieu GIA (khong can dataset that, khong dung ten repo that -- tranh
phu thuoc vao so luong repo ai_preprocessing hien co cua dataset, con so do
co the doi neu dataset doi).

Muc tieu: (a) quota AI_DOMAIN_QUOTA=5 duoc tuan thu dung (dung ngay khi du,
khong sang thua); (b) khi KHONG du quota du sang het dong thi dung dung ly
do "da sang het dong"; (c) budget_left (chan chung screening_limit) duoc ton
trong.
"""
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

import run_experiment1 as exp1  # noqa: E402

errs: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'SAI'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        errs.append(label + (f" -- {detail}" if detail else ""))


def run_stream(stream, phase, budget_left, stop_when, accept_names):
    def fake_screen_repo(cfg, repo, results_dir, run_id):
        ok = repo.name in accept_names
        return {
            "label": repo.name,
            "repo_status": "PARTIAL" if ok else "BASELINE_FAILED",
            "funnel": {"counts": {"replayable": 5 if ok else 0}},
            "_screen_seconds": 0.0,
        }

    orig = exp1.screen_repo
    exp1.screen_repo = fake_screen_repo
    try:
        return exp1._screen_stream(
            cfg={}, results_dir=BENCH / "results", run_id="t_fake",
            stream=stream, phase=phase, min_replayable=2,
            budget_left=budget_left, stop_when=stop_when,
        )
    finally:
        exp1.screen_repo = orig


print("=" * 78)
print("(a) QUOTA AI dat truoc khi het dong -> dung SOM, khong sang thua")
print("=" * 78)
fake_ai = [Path(f"/fake/ai_{i}") for i in range(20)]
# 5 repo dau tien deu hop le -> phai dung sau dung 5 lan sang, KHONG dung ca 20.
accept_names = {f"ai_{i}" for i in range(5)}
accepted, rejected, screened, stop = run_stream(
    fake_ai, exp1.PHASE_AI_FIRST, budget_left=100,
    stop_when=lambda n_ok: n_ok >= exp1.AI_DOMAIN_QUOTA, accept_names=accept_names,
)
check(len(accepted) == 5, "chon dung 5 repo AI (quota)", str(len(accepted)))
check(len(screened) == 5, "CHI sang 5 lan (dung ngay khi du, khong sang thua 15 con lai)",
      str(len(screened)))
check(stop == "đủ quota giai đoạn", "ly do dung = du quota giai doan", stop)
check(not rejected, "khong repo nao bi loai (5 dau da du)")

print()
print("=" * 78)
print("(b) KHONG du quota du sang HET dong -> dung dung ly do 'het dong'")
print("=" * 78)
# Chi 3/20 hop le -> phai sang HET 20 (khong dung som o dau) roi moi tra ve.
accept_names_b = {f"ai_{i}" for i in (2, 9, 17)}
accepted_b, rejected_b, screened_b, stop_b = run_stream(
    fake_ai, exp1.PHASE_AI_FIRST, budget_left=100,
    stop_when=lambda n_ok: n_ok >= exp1.AI_DOMAIN_QUOTA, accept_names=accept_names_b,
)
check(len(accepted_b) == 3, "chi tim duoc 3/5 quota (dung so tim duoc, khong ep du 5)",
      str(len(accepted_b)))
check(len(screened_b) == 20, "sang HET ca 20 repo AI (khong con quota de dung som)",
      str(len(screened_b)))
check(stop_b == "đã sàng hết dòng", "ly do dung = da sang het dong", stop_b)
check(len(rejected_b) == 17, "17 repo con lai bi loai dung so", str(len(rejected_b)))

print()
print("=" * 78)
print("(c) budget_left chan dung SOM du con ung vien va chua du quota")
print("=" * 78)
accepted_c, rejected_c, screened_c, stop_c = run_stream(
    fake_ai, exp1.PHASE_AI_FIRST, budget_left=4,
    stop_when=lambda n_ok: n_ok >= exp1.AI_DOMAIN_QUOTA, accept_names=set(),  # khong ai hop le
)
check(len(screened_c) == 4, "dung dung o budget_left=4 (khong sang qua)", str(len(screened_c)))
check("screening_limit" in stop_c, "ly do dung nhac toi ngan sach/screening_limit", stop_c)

print()
if errs:
    print(f"### AI-FIRST SCREENING: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### AI-FIRST SCREENING: PASS")
