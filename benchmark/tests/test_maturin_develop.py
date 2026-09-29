"""Loi maturin develop phat hien o PILOT 2 -- kiem chung ban sua.

BANG CHUNG THAT (doc tu results/run_20260929_114055 cua pilot 2):
    cargo check      : 42/54 (77%) bien dich duoc
    maturin develop  : 0/42  (0%)  -- 42/42 crate co code Rust deu that bai
    loi nguyen van   :
        error: unexpected argument '--interpreter' found
          tip: to pass '--interpreter' as a value, use '-- --interpreter'
        Usage: maturin develop --release [ARGS]...

`maturin develop` KHONG co co `--interpreter` (chi `maturin build` moi co), nen
clap thoat exit 2 TRUOC khi build mot dong nao. Day la tang KHAC voi cargo
check -- cargo check xanh khong noi gi ve viec nay.

May dev KHONG co cargo, nen test nay o muc LOGIC (mock subprocess):
  (1) lenh sinh ra KHONG con `--interpreter`;
  (2) lenh dung interpreter cua venv REPO khi venv do co maturin;
  (3) stdout/stderr DAY DU duoc ghi vao ket qua khi exit != 0;
  (4) lay nguyen van loi pilot 2 -> phai lo ra trong `maturin_error`.

!! CHUA XAC NHAN BANG CARGO THAT -- can may thue. Phep kiem that la
   `preflight.check_maturin_develop_works()`.
"""
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

import stage5_compiler_in_the_loop.crate_builder as cb  # noqa: E402

errs: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'SAI'} {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        errs.append(label + (f" -- {detail}" if detail else ""))


# Loi NGUYEN VAN tu pilot 2.
PILOT2_STDERR = (
    "error: unexpected argument '--interpreter' found\n\n"
    "  tip: to pass '--interpreter' as a value, use '-- --interpreter'\n\n"
    "Usage: maturin develop --release [ARGS]...\n\n"
    "For more information, try '--help'."
)


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _fake_result(tmp: Path) -> cb.CrateResult:
    crate = tmp / "crate"
    (crate / "src").mkdir(parents=True, exist_ok=True)
    return cb.CrateResult(
        function_name="sum_squares", tier="TIER1_NATIVE",
        ext_module="sum_squares_rsext", ext_func="sum_squares",
        crate_dir=str(crate), ext_root=str(tmp),
    )


# ===========================================================================
print("=" * 78)
print("(1) Lenh maturin KHONG con --interpreter")
print("=" * 78)
FAKE_VENV_PY = Path("/fake/.rtb_venv/bin/python")

# venv CO maturin -> dung `python -m maturin` cua chinh venv do.
cb.venv_has_maturin = lambda py: True
cmd, how = cb.maturin_command(FAKE_VENV_PY)
print(f"     cmd = {cmd}")
check("--interpreter" not in cmd, "khong con co --interpreter trong lenh")
check(cmd[:3] == [str(FAKE_VENV_PY), "-m", "maturin"],
      "goi `python -m maturin` bang interpreter cua venv REPO", str(cmd[:3]))
check(cmd[3:] == ["develop", "--release"], "con lai dung `develop --release`", str(cmd[3:]))
check("interpreter của venv repo" in how, "mo ta noi ro dung venv nao", how[:60])

# venv KHONG co maturin -> lui ve PATH, van khong co --interpreter.
cb.venv_has_maturin = lambda py: False
cmd2, how2 = cb.maturin_command(FAKE_VENV_PY)
print(f"     cmd (khong co maturin trong venv) = {cmd2}")
check("--interpreter" not in cmd2, "ban lui ve PATH cung khong co --interpreter")
check(cmd2[0] == "maturin", "lui ve `maturin` tren PATH", str(cmd2[0]))
check("kém chắc chắn" in how2, "mo ta noi ro day la duong kem chac chan hon")

# ===========================================================================
print()
print("=" * 78)
print("(2)+(3) exit != 0 -> ghi DAY DU stdout/stderr, khong nuot")
print("=" * 78)
import tempfile  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="rtb_mat_"))
cb.venv_has_maturin = lambda py: True
cb.toolchain_available = lambda venv_python=None: (True, "")
_orig_run = cb.subprocess.run
cb.subprocess.run = lambda *a, **k: _Proc(2, "", PILOT2_STDERR)
try:
    res = cb.build_crate(_fake_result(tmp), FAKE_VENV_PY)
finally:
    cb.subprocess.run = _orig_run

check(res.status == cb.BUILD_FAILED, "status = BUILD_FAILED", res.status)
check(res.maturin_returncode == 2, "ghi lai exit code", str(res.maturin_returncode))
check(res.maturin_stderr == PILOT2_STDERR, "stderr giu NGUYEN VAN, khong cat")
check("unexpected argument" in res.maturin_error,
      "maturin_error mang loi that (khong chi 'exit=2')")
check("lệnh   :" in res.maturin_error and "cwd    :" in res.maturin_error,
      "maturin_error ghi ca lenh va cwd de tai lap duoc")
check("cách chọn venv" in res.maturin_error, "maturin_error ghi cach chon venv dich")
check(res.maturin_cmd and "--interpreter" not in res.maturin_cmd,
      "maturin_cmd duoc ghi lai va khong co --interpreter", str(res.maturin_cmd))

d = res.as_dict()
for key in ("maturin_error", "maturin_stdout", "maturin_stderr",
            "maturin_returncode", "maturin_cmd"):
    check(key in d, f"as_dict xuat {key} (ra duoc JSON ket qua)")
check(len(d["maturin_error"]) == len(res.maturin_error),
      "maturin_error trong JSON KHONG bi cat ngan",
      f"{len(d['maturin_error'])} ky tu")

# ===========================================================================
print()
print("=" * 78)
print("(4) Loi DAI hon 2000 ky tu van khong bi cat trong maturin_error")
print("=" * 78)
LONG = "x" * 5000
cb.subprocess.run = lambda *a, **k: _Proc(101, LONG, LONG)
try:
    res2 = cb.build_crate(_fake_result(tmp), FAKE_VENV_PY)
finally:
    cb.subprocess.run = _orig_run
d2 = res2.as_dict()
check(len(d2["maturin_stdout"]) == 5000, "stdout 5000 ky tu giu nguyen",
      str(len(d2["maturin_stdout"])))
check(len(d2["output"]) <= 2000, "`output` van cat 2000 (de gui LLM)",
      str(len(d2["output"])))
check(len(d2["maturin_error"]) > 4000,
      "maturin_error KHONG bi cat 4000 nhu FIX_PROMPT_TEMPLATE",
      str(len(d2["maturin_error"])))

# ===========================================================================
print()
print("=" * 78)
print("(5) Build THANH CONG -> khong sinh maturin_error nhieu")
print("=" * 78)
cb.subprocess.run = lambda *a, **k: _Proc(0, "Installed sum_squares_rsext", "")
try:
    res3 = cb.build_crate(_fake_result(tmp), FAKE_VENV_PY)
finally:
    cb.subprocess.run = _orig_run
check(res3.status == cb.BUILT_OK, "status = BUILT_OK", res3.status)
check(res3.maturin_error == "", "khong co maturin_error khi thanh cong")
check(res3.maturin_returncode == 0, "ghi exit 0")

# ===========================================================================
print()
print("=" * 78)
print("(6) maturin duoc cai vao MOI .rtb_venv (cau tra loi (c))")
print("=" * 78)
rr_src = (BENCH / "stage5_compiler_in_the_loop" / "repo_runner.py").read_text(
    encoding="utf-8")
check('_pip_install(py, ["maturin"]' in rr_src,
      "install_repo cai maturin vao venv cua repo")
i_pytest = rr_src.index('_pip_install(py, ["pytest", "cloudpickle"]')
i_mat = rr_src.index('_pip_install(py, ["maturin"]')
check(i_mat > i_pytest, "cai maturin sau pytest/cloudpickle (thu tu hop ly)")

# ===========================================================================
print()
print("=" * 78)
print("(7) preflight co phep kiem maturin RIENG, khong gop voi cargo check")
print("=" * 78)
pf_src = (BENCH / "preflight.py").read_text(encoding="utf-8")
check("def check_maturin_develop_works" in pf_src, "co check_maturin_develop_works")
check("checks.append(check_maturin_develop_works())" in pf_src,
      "duoc goi trong run_all cua preflight")
check("check_pyo3_example_compiles" in pf_src and
      pf_src.index("check_pyo3_example_compiles()") < pf_src.index(
          "check_maturin_develop_works()"),
      "chay sau cargo check (thu tu dung: cu phap truoc, dong goi sau)")
check("import " in pf_src[pf_src.index("def check_maturin_develop_works"):]
      and "khong import duoc" in pf_src.lower().replace("ô", "o").replace("Ô", "O")
      or "KHÔNG import được" in pf_src,
      "phep kiem chot lai bang viec IMPORT module vua cai")

print()
print("  !! CHUA XAC NHAN BANG CARGO THAT -- may dev khong co cargo/maturin.")
print("     Phep kiem that: preflight.check_maturin_develop_works() tren may thue.")
print()
if errs:
    print(f"### MATURIN DEVELOP: THAT BAI ({len(errs)})")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### MATURIN DEVELOP: PASS (muc logic)")
