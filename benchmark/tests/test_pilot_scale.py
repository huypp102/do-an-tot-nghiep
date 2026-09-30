"""Xac nhan QUY MO lan chay 4 duoc doc dung tu profiles/pilot_linux.yaml.

LAN CHAY 4 (2026-09-30) -- gioi han CUNG 10 repo, doi thu tu sang tu TEN sang
HOAN VI NGAU NHIEN CO SEED, them CAN BANG MIEN (ai_preprocessing/general):
    n_repos                    5 -> 10   (10 -> 5 -> 10)
    n_backup_repos              1 -> 2   (2 -> 1 -> 2)
    screening_limit            25 -> 50
    sample_seed                 (moi) 42
    noise_floor_max_hotspots   5         (giu nguyen, khoa PHANG)

Voi ti le dat 24-30% do duoc o pilot 1, can sang ~41-50 ung vien de ky vong du
10+2=12 -> screening_limit=50. estimate_pass_count(50)=12 (moc do gan nhat o
pilot 1 la 40 ung vien) -> can 12 == dat 12 -> SAT NGUONG, dung nhu 3 pilot
truoc (chi doi con so, giu nguyen TINH CHAT sat nguong).

Test nay khong kiem logic, chi kiem CON SO THAT SU DEN DUOC NOI DUNG NO. Cach
hong pho bien nhat la co cho nao hard-code hoac cache config cu roi de len --
luc do config doc dung ma pipeline van chay quy mo cu, va khong ai biet.

Nen moi gia tri duoc kiem BA lan:
  (a) YAML co dung so moi (khong phai so cu);
  (b) load_config(profile=...) tra ve so moi (khong phai mac dinh cua
      config.yaml, cung khong phai so cu);
  (c) noi TIEU THU no thuc su dung so do -- doc lai tu selection.json ma
      run_experiment1.py --dry-run vua ghi (them: seed, cot ai_preprocessing).
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

ensure_utf8_stdio()

PROFILE = "pilot_linux"
PROFILE_PATH = BENCH / "profiles" / f"{PROFILE}.yaml"

# (gia tri MOI mong doi, gia tri CU phai KHONG con)
EXPECTED = {
    "n_repos": (10, 5),
    "n_backup_repos": (2, 1),
    "screening_limit": (50, 25),
    "noise_floor_max_hotspots": (5, 10),
}

# Quy mo phai KHOP ti le dat do duoc, khong duoc dat cao hon roi mong.
PILOT1_PASS_AT_LIMIT = 12

errs: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'SAI'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        errs.append(label + (f" -- {detail}" if detail else ""))


# ===========================================================================
print("=" * 78)
print("(a) YAML co dung so MOI")
print("=" * 78)
raw = PROFILE_PATH.read_text(encoding="utf-8")
for key, (new, old) in EXPECTED.items():
    # Chi xet dong GAN gia tri, bo comment -- comment co the nhac so cu de
    # giai thich, va do la chuyen binh thuong.
    assigns = [
        l.split("#", 1)[0] for l in raw.splitlines()
        if re.match(rf"\s*{re.escape(key)}\s*:", l)
    ]
    check(len(assigns) == 1, f"{key}: dung 1 dong gan trong YAML", str(assigns))
    if not assigns:
        continue
    val = assigns[0].split(":", 1)[1].strip()
    check(val == str(new), f"{key} = {new} trong YAML", f"doc duoc {val!r}")
    check(val != str(old), f"{key} KHONG con gia tri cu {old}")

# `noise_floor` phai van la BOOL, khong bi doi thanh dict long nhau -- neu
# thanh dict thi `noise_floor_max_hotspots` khong con duoc doc.
nf_lines = [l for l in raw.splitlines() if re.match(r"\s*noise_floor\s*:", l)]
check(len(nf_lines) == 1, "noise_floor: dung 1 dong", str(nf_lines))
if nf_lines:
    v = nf_lines[0].split(":", 1)[1].split("#", 1)[0].strip()
    check(v == "true", "noise_floor van la bool true (khong phai dict)", f"doc duoc {v!r}")

# ===========================================================================
print()
print("=" * 78)
print("(b) load_config doc ra so MOI, khong phai mac dinh cung khong phai so cu")
print("=" * 78)
base = load_config()                      # config.yaml tran
prof = load_config(profile=PROFILE)       # da gop profile

exp_base = base.get("experiment") or {}
exp_prof = prof.get("experiment") or {}
ab_base = base.get("ablation") or {}
ab_prof = prof.get("ablation") or {}

got = {
    "n_repos": exp_prof.get("n_repos"),
    "n_backup_repos": exp_prof.get("n_backup_repos"),
    "screening_limit": exp_prof.get("screening_limit"),
    "noise_floor_max_hotspots": ab_prof.get("noise_floor_max_hotspots"),
}
for key, (new, old) in EXPECTED.items():
    check(got[key] == new, f"load_config -> {key} = {new}", f"thuc te {got[key]!r}")
    check(got[key] != old, f"{key} khong phai gia tri cu {old}")

# So sanh voi config.yaml tran: chung minh gia tri DEN TU PROFILE.
check(
    ab_base.get("noise_floor_max_hotspots") == 10
    and ab_prof.get("noise_floor_max_hotspots") == 5,
    "noise_floor_max_hotspots den TU PROFILE (config.yaml van 10)",
    f"base={ab_base.get('noise_floor_max_hotspots')} prof={ab_prof.get('noise_floor_max_hotspots')}",
)
check(not exp_base, "config.yaml tran KHONG co muc `experiment`",
      f"co: {sorted(exp_base)}" if exp_base else "dung -- chi profile khai bao")

# Nhung thu KHONG duoc doi lan nay.
for key, want in (("max_wall_hours", 6), ("test_timeout_sec", None)):
    if key == "max_wall_hours":
        check(exp_prof.get(key) == want, f"max_wall_hours GIU NGUYEN = {want}",
              f"thuc te {exp_prof.get(key)!r}")
ro = prof.get("repo_oracle") or {}
check(ro.get("test_timeout_sec") == 600, "test_timeout_sec GIU NGUYEN = 600",
      f"thuc te {ro.get('test_timeout_sec')!r}")
check(ro.get("repo_time_budget_sec") == 1800, "repo_time_budget_sec GIU NGUYEN = 1800",
      f"thuc te {ro.get('repo_time_budget_sec')!r}")

# Ham thuc su duoc pipeline goi phai tra ra 5, khong phai mac dinh 10.
import ablation as ab  # noqa: E402

check(ab.noise_floor_max_hotspots(prof) == 5,
      "ablation.noise_floor_max_hotspots(cfg) = 5",
      f"thuc te {ab.noise_floor_max_hotspots(prof)}")
check(ab.noise_floor_enabled(prof) is True, "noise_floor van bat")
check("graph2" in ab.arms(prof), "nhanh nhieu nen van co trong arms", str(ab.arms(prof)))

# ===========================================================================
print()
print("=" * 78)
print("(c) NOI TIEU THU thuc su dung so do -- doc lai selection.json cua --dry-run")
print("=" * 78)
dataset_root = os.environ.get("REPOTRANSBENCH_ROOT")
if not dataset_root or not Path(dataset_root).is_dir():
    print("  (bo qua: chua co REPOTRANSBENCH_ROOT tro vao dataset that)")
    print("  Phan (a) va (b) van du de bat loi 'config bi noi khac de len'.")
else:
    import subprocess

    run_id = "t_scale"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("RTB_WORK_DIR", str(Path(tempfile.gettempdir()) / "rtb_scale"))
    proc = subprocess.run(
        [sys.executable, "run_experiment1.py", "--profile", PROFILE,
         "--dry-run", "--skip-preflight", "--run-id", run_id],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, cwd=str(BENCH), env=env, timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    check(proc.returncode == 0, "--dry-run chay xong", f"exit={proc.returncode}")
    check("cần 10 repo + 2 dự phòng" in out,
          "man hinh in dung 'can 10 repo + 2 du phong'")
    check("CẢNH BÁO CỠ MẪU: SÁT NGƯỠNG" in out,
          "canh bao dung mức SAT NGUONG (khong phai 'co the khong du')")
    check("có thể KHÔNG đủ" not in out,
          "KHONG con thong diep cu 'co the khong du' (can 12 <= dat 12)")
    check("không dư ứng viên nào để loại thêm" in out,
          "canh bao noi ro la khong du bien an toan")
    check("seed hoán vị: 42" in out, "man hinh in dung seed hoan vi = 42")

    sel_path = BENCH / "results" / run_id / "selection.json"
    check(sel_path.exists(), "co selection.json")
    if sel_path.exists():
        sel = json.loads(sel_path.read_text(encoding="utf-8"))
        n_cand = len(sel.get("candidates") or [])
        check(n_cand <= 50, f"danh sach ung vien <= 50 (thuc te {n_cand})")
        check(n_cand == 50, "dung 50 ung vien (dataset 171 repo nen khong bi thieu)",
              str(n_cand))
        check(sel.get("screening_limit") == 50, "selection.json ghi screening_limit=50",
              str(sel.get("screening_limit")))
        check(sel.get("n_repos") == 10, "selection.json ghi n_repos=10",
              str(sel.get("n_repos")))
        check(sel.get("n_backup_repos") == 2, "selection.json ghi n_backup_repos=2",
              str(sel.get("n_backup_repos")))
        check(sel.get("seed") == 42, "selection.json ghi seed=42",
              str(sel.get("seed")))
        check("n_ai_preprocessing_in_pool" in sel,
              "selection.json ghi so repo ai_preprocessing trong pool ung vien")
        check(bool(sel.get("sample_size_warning")),
              "canh bao co mau duoc GHI vao selection.json (doc lai duoc sau)")
        # Hoan vi phai KHAC thu tu ten -- bang chung day la ngau nhien that,
        # khong phai sorted() nguy trang.
        by_name = sorted(sel.get("candidates") or [])
        check(sel.get("candidates") != by_name,
              "thu tu candidates KHAC thu tu ten (dung la hoan vi, khong phai sort)")


# ===========================================================================
print()
print("=" * 78)
print("(d) Quy mo KHOP ti le dat do duoc, va 3 muc canh bao dung")
print("=" * 78)
import run_experiment1 as exp1  # noqa: E402

n_rep, n_bk = EXPECTED["n_repos"][0], EXPECTED["n_backup_repos"][0]
limit = EXPECTED["screening_limit"][0]
est, how = exp1.estimate_pass_count(limit)
check(est == PILOT1_PASS_AT_LIMIT, f"uoc luong dat o {limit} ung vien = {PILOT1_PASS_AT_LIMIT}",
      f"{est} ({how})")
check(n_rep + n_bk <= est,
      f"can {n_rep}+{n_bk}={n_rep + n_bk} <= uoc luong dat {est} (khong dat cao roi mong)")
check(n_rep + n_bk == est, "dung SAT NGUONG: can == dat (khong du bien an toan)",
      f"can {n_rep + n_bk}, dat {est}")


def _capture(n_repos, n_backup):
    lines = exp1.warn_if_screening_too_small(limit, n_repos, n_backup)
    return "\n".join(lines)


import contextlib  # noqa: E402
import io as _io  # noqa: E402

buf = _io.StringIO()
with contextlib.redirect_stdout(buf):
    txt_edge = _capture(n_rep, n_bk)      # can 12 == dat 12 -> SAT NGUONG
    txt_short = _capture(15, 3)           # can 18 > dat 12 -> co the khong du
    txt_ok = _capture(3, 1)               # can 4 < dat 12 -> du, co du
check("SÁT NGƯỠNG" in txt_edge and "có thể KHÔNG đủ" not in txt_edge,
      "can == dat -> chi in SAT NGUONG")
check("không dư ứng viên nào để loại thêm" in txt_edge,
      "SAT NGUONG noi ro khong con du phong sai so")
check("có thể KHÔNG đủ" in txt_short and "SÁT NGƯỠNG" not in txt_short,
      "can > dat -> in 'co the KHONG du'")
check("dư 8 repo" in txt_ok and "CẢNH BÁO" not in txt_ok,
      "can < dat -> chi thong bao du, khong canh bao", txt_ok.strip()[:70])

# ===========================================================================
print()
print("=" * 78)
print("(e) DU PHONG KHONG DU BU HET repo hong -- khong crash, khong im lang")
print("=" * 78)
# n_backup_repos=1 nghia la chi bu duoc DUNG 1 repo INSTALL_FAILED. Neu co 2
# repo hong thi phai: chay tiep voi it hon n_repos, in ro ra, KHONG crash va
# KHONG tu ha quy tac xuong 0.
import json as _json  # noqa: E402
import outcomes  # noqa: E402

_orig_run_repo = None


def _fake_rows_scenario(n_selected, n_backup, n_install_failed):
    """Gia lap run_main_phase voi `n_install_failed` repo dau bi INSTALL_FAILED."""
    import types

    calls = {"n": 0}

    def fake_run_repo_pipeline(*, cfg, repo_path, results_dir, timestamp, label,
                               benchmark_root):
        calls["n"] += 1
        failed = calls["n"] <= n_install_failed
        return {
            "label": label,
            "ok": not failed,
            "repo_status": (outcomes.INSTALL_FAILED if failed else outcomes.PARTIAL),
            "status_note": "gia lap" if failed else "",
            "metrics": {"n_measured": 0 if failed else 2},
            "hotspots": [],
            "funnel": {},
        }

    import repo_pipeline as rp

    real = rp.run_repo_pipeline
    rp.run_repo_pipeline = fake_run_repo_pipeline
    # run_experiment1 import bên trong hàm nên patch ở module nguồn là đủ.
    try:
        root = Path(tempfile.mkdtemp(prefix="rtb_bk_"))
        sel = {
            "_selected_paths": [str(root / f"sel{i}") for i in range(n_selected)],
            "_backup_paths": [str(root / f"bk{i}") for i in range(n_backup)],
        }
        out_buf = _io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            rows, stopped = exp1.run_main_phase(
                cfg={"experiment": {}}, selection=sel,
                results_dir=root, run_id="t_bk",
                max_wall_hours=1.0, resume=False,
            )
        return rows, stopped, out_buf.getvalue()
    finally:
        rp.run_repo_pipeline = real


# Truong hop 1: 1 repo hong, CO 1 du phong -> thay duoc.
rows, stopped, out = _fake_rows_scenario(n_selected=5, n_backup=1, n_install_failed=1)
check(len(rows) == 6, "1 hong + 1 du phong -> chay 6 luot (5 + 1 thay the)", str(len(rows)))
check("thay bằng repo dự phòng" in out, "in ro da thay bang du phong")
check("HẾT DỰ PHÒNG" not in out, "chua het du phong thi khong bao het")

# Truong hop 2: 2 repo hong, CHI CO 1 du phong -> khong bu het.
rows, stopped, out = _fake_rows_scenario(n_selected=5, n_backup=1, n_install_failed=2)
n_ok = sum(1 for r in rows if r.get("ok"))
print(f"     da chay {len(rows)} luot, {n_ok} repo dung duoc, stopped_early={stopped}")
check(True, "KHONG crash khi het du phong")
check("HẾT DỰ PHÒNG" in out, "in ro 'HET DU PHONG' (khong bao loi im lang)")
check("Cỡ mẫu thật" in out, "noi ro co mau THAT la bao nhieu")
check(n_ok < 5, f"co mau that NHO HON n_repos=5 (thuc te {n_ok}) -- dung nhu du bao")
check(n_ok > 0, "KHONG tu ha xuong 0", f"n_ok={n_ok}")
check(len(rows) >= 5, "van chay het queue, khong bo do", str(len(rows)))
check(stopped is False, "khong bi danh dau dung som (chua cham max_wall_hours)")

# Truong hop 3: KHONG co du phong nao, 1 repo hong.
rows, stopped, out = _fake_rows_scenario(n_selected=3, n_backup=0, n_install_failed=1)
check("HẾT DỰ PHÒNG" in out, "n_backup=0 + 1 hong -> bao het du phong ngay")
check(len(rows) == 3, "van chay du 3 repo da chon", str(len(rows)))

print()
if errs:
    print(f"### QUY MO LAN CHAY 4: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### QUY MO LAN CHAY 4: PASS")
