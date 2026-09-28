"""PHA A + E -- Chạy BỘ TEST GỐC của repo làm oracle mức repo.

VÌ SAO CẦN: trước Pha A, oracle duy nhất là so output hàm-với-hàm trên vài
sample ảnh. Với bản hybrid (chỉ vài hàm là Rust, phần còn lại vẫn Python) thì
oracle đúng phải là BỘ TEST PYTHON GỐC của repo -- đó là thứ duy nhất trả lời
được câu "thay hotspot bằng Rust rồi repo có còn chạy đúng không".

BỐI CẢNH DATASET (đã kiểm chứng, đừng suy đoán lại):
  - `source_projects/Python/<repo>/run_tests.sh` = `coverage run --branch -m
    pytest tests`, và `test_summary.json` CHỈ có số coverage, KHÔNG có số test
    pass/fail. Nên module này KHÔNG đọc 2 file đó: nó tự chạy pytest với
    `--junitxml` để có số pass/fail thật, và không phụ thuộc `coverage`.
  - `target_projects/Python/Rust/<repo>/` (test `cargo test` cho bản dịch
    TOÀN repo) TUYỆT ĐỐI không được dùng làm oracle cho hybrid. Module này
    không bao giờ chạm tới đường dẫn đó.

BA RÀNG BUỘC MÔI TRƯỜNG đã định hình thiết kế:
  1. Dataset được mount CHỈ ĐỌC -> phải COPY repo sang `work_dir` trước khi
     chạy, vì pytest ghi cache/`.pyc`, và Pha E còn phải chèn extension Rust.
  2. Mỗi repo có phụ thuộc riêng, xung đột nhau -> mỗi repo một venv riêng,
     xoá sau khi xong (`keep_venv=false`) để không đầy đĩa máy thuê.
  3. Test của repo lạ chạy với quyền của tiến trình này -> môi trường
     subprocess phải LỌC SẠCH biến nhạy cảm (xem `build_child_env`).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.stage5_compiler_in_the_loop.repo_runner")

def default_work_root() -> Path:
    """Thư mục làm việc mặc định, TRUNG TÍNH giữa Linux và Windows.

    Không hard-code `/tmp`: trên Windows đường dẫn đó thành `C:\tmp` (hoặc
    tệ hơn là thư mục tương đối), và máy dev ở đây từng hết chỗ trên C:.
    `tempfile.gettempdir()` tôn trọng TMPDIR/TEMP nên máy thuê chỉ cần đặt
    biến môi trường, hoặc đặt thẳng `RTB_WORK_DIR`.
    """
    return Path(tempfile.gettempdir()) / "rtb_work"


DEFAULT_WORK_ROOT = str(default_work_root())
DEFAULT_TEST_TIMEOUT_SEC = 600
DEFAULT_REPO_BUDGET_SEC = 1800
DEFAULT_INSTALL_TIMEOUT_SEC = 900

# Thư mục rác không copy: chúng là sản phẩm của lần chạy trước, có thể rất
# nặng, và `.pytest_cache` mang theo trạng thái cũ làm sai lệch lượt chạy mới.
JUNK_DIRS = {
    ".pytest_cache", ".benchmarks", "htmlcov", ".tox", ".nox", ".mypy_cache",
    ".ruff_cache", "__pycache__", ".git", ".hypothesis", ".eggs", "build",
    "dist", ".coverage", "node_modules", ".venv", "venv", ".idea", ".vscode",
}

# Thư mục tên kiểu timestamp (vd "20240115_103000", "2024-01-15T10-30-00") --
# output của lần chạy benchmark trước, không phải source.
_TIMESTAMP_DIR_RE = re.compile(r"^\d{4}[-_]?\d{2}[-_]?\d{2}([-_T].*)?$")

# Biến môi trường KHÔNG được truyền vào subprocess chạy code của repo lạ.
_SENSITIVE_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD", "_CREDENTIALS")
_SENSITIVE_EXACT = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AWS_SESSION_TOKEN"}


def is_junk_dir(name: str) -> bool:
    """Thư mục này có phải rác của lần chạy trước không."""
    return name in JUNK_DIRS or bool(_TIMESTAMP_DIR_RE.match(name))


def build_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Môi trường cho subprocess, đã LỌC SẠCH biến nhạy cảm.

    Code test của repo trong dataset là code của người khác, chạy với quyền
    của tiến trình này. Không có lý do gì để nó thấy được API key của chúng
    ta. Lọc theo hậu tố (`*_KEY`, `*_TOKEN`, ...) thay vì danh sách cứng, để
    biến mới thêm sau này cũng tự động bị lọc.
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _SENSITIVE_EXACT:
            continue
        if any(upper.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
            continue
        env[key] = value
    # Đừng để cache bytecode của repo lẫn vào lượt sau.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env


@dataclass
class RepoTestResult:
    """Kết quả chạy bộ test của 1 repo ở MỘT cấu hình (baseline hoặc hybrid)."""

    label: str = "baseline"           # "baseline" | "hybrid"
    ok: bool = False                  # chạy được pytest và parse được kết quả
    n_passed: int = 0
    n_total: int = 0
    n_failed: int = 0
    n_errors: int = 0
    n_skipped: int = 0
    passed_ids: list[str] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    error: str = ""
    junit_path: str = ""
    returncode: int | None = None

    @property
    def pass_rate(self) -> float | None:
        """Tỉ lệ test pass. Đây là ĐÓNG GÓP của repo này vào APR của dataset
        (APR = trung bình `pass_rate` qua các repo, tính ở tầng dataset)."""
        if not self.ok or self.n_total == 0:
            return None
        return self.n_passed / self.n_total

    @property
    def success_rate_flag(self) -> bool:
        """SR theo định nghĩa RepoTransBench: repo pass TOÀN BỘ test."""
        return bool(self.ok and self.n_total > 0 and self.n_passed == self.n_total)

    def as_dict(self) -> dict:
        return {
            "label": self.label, "ok": self.ok,
            "n_passed": self.n_passed, "n_total": self.n_total,
            "n_failed": self.n_failed, "n_errors": self.n_errors,
            "n_skipped": self.n_skipped,
            "pass_rate": self.pass_rate, "sr": self.success_rate_flag,
            "duration_sec": round(self.duration_sec, 3),
            "error": self.error, "returncode": self.returncode,
            "n_passed_ids": len(self.passed_ids),
        }


# --------------------------------------------------------------------- COPY
def _ignore_junk(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if is_junk_dir(n)}


def prepare_work_copy(repo: Path, work_root: Path, force: bool = True) -> Path:
    """COPY repo từ dataset (mount chỉ đọc) sang `work_root/<tên repo>`.

    Bỏ các thư mục rác (`JUNK_DIRS` + thư mục tên timestamp). Raise OSError
    nếu copy thất bại -- caller quy thành INSTALL_FAILED.
    """
    repo = Path(repo)
    work_root = Path(work_root)
    dest = work_root / repo.name
    if dest.exists() and force:
        shutil.rmtree(dest, onerror=_force_remove)
    work_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo, dest, ignore=_ignore_junk, dirs_exist_ok=True)
    logger.info("Đã copy repo '%s' sang %s (bỏ thư mục rác).", repo.name, dest)
    return dest


def _force_remove(func, path, _exc):
    """`shutil.rmtree` onerror: file chỉ-đọc (hay gặp trong .git/objects/pack)
    phải chmod trước khi xoá, nếu không rmtree bỏ dở mà không báo."""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        logger.warning("Không xoá được %s -- bỏ qua.", path)


# --------------------------------------------------------------------- VENV
def _venv_python(venv_dir: Path) -> Path:
    """Đường dẫn interpreter trong venv. Windows dùng Scripts/, POSIX dùng bin/."""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def create_venv(work_dir: Path, timeout_sec: int = 300) -> tuple[Path | None, str]:
    """Tạo venv riêng cho repo. Ưu tiên `uv` (nhanh hơn nhiều), fallback
    `python -m venv`. Trả về (đường dẫn python trong venv, thông báo lỗi)."""
    venv_dir = Path(work_dir) / ".rtb_venv"
    env = build_child_env()

    if shutil.which("uv"):
        cmd = ["uv", "venv", str(venv_dir), "--python", sys.executable]
    else:
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_sec, env=env,
        )
    except subprocess.TimeoutExpired:
        return None, f"tạo venv quá {timeout_sec}s"
    except OSError as exc:
        return None, f"không chạy được lệnh tạo venv: {exc}"

    py = _venv_python(venv_dir)
    if proc.returncode != 0 or not py.exists():
        return None, (
            f"tạo venv thất bại (exit={proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '')[-400:]}"
        )
    logger.info("Đã tạo venv cho repo tại %s", venv_dir)
    return py, ""


def _pip_install(
    py: Path, args: list[str], cwd: Path, timeout_sec: int
) -> tuple[bool, str]:
    """Cài package vào venv. Dùng `uv pip` nếu có (nhanh hơn rất nhiều trên
    máy thuê tính tiền theo giờ), fallback `python -m pip`."""
    env = build_child_env()
    if shutil.which("uv"):
        cmd = ["uv", "pip", "install", "--python", str(py), *args]
    else:
        cmd = [str(py), "-m", "pip", "install", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_sec,
            cwd=str(cwd), env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"cài đặt quá {timeout_sec}s: {' '.join(args)}"
    except OSError as exc:
        return False, f"không chạy được pip: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "")[-800:]
    return True, ""


def install_repo(
    py: Path, work_dir: Path, timeout_sec: int = DEFAULT_INSTALL_TIMEOUT_SEC
) -> tuple[bool, str]:
    """Cài phụ thuộc của repo + pytest + cloudpickle vào venv.

    `cloudpickle` là bắt buộc: plugin ghi đối số ở Pha B chạy BÊN TRONG venv
    này nên phải tự có cloudpickle ở đó, không dùng được của venv cha.

    Thứ tự thử: requirements.txt -> `pip install -e .` (pyproject/setup.py).
    Repo không có gì để cài cũng KHÔNG coi là lỗi -- nhiều repo nhỏ chỉ cần
    stdlib; chỉ cần pytest là chạy test được.
    """
    work_dir = Path(work_dir)

    # pytest + cloudpickle trước: nếu bước này lỗi thì không có gì chạy được.
    ok, err = _pip_install(py, ["pytest", "cloudpickle"], work_dir, timeout_sec)
    if not ok:
        return False, f"không cài được pytest/cloudpickle: {err}"

    installed_something = False
    req = work_dir / "requirements.txt"
    if req.exists():
        ok, err = _pip_install(py, ["-r", str(req)], work_dir, timeout_sec)
        if not ok:
            # Không bỏ cuộc ngay: nhiều requirements.txt ghim phiên bản cũ
            # không build được trên Python mới, nhưng repo vẫn import được.
            logger.warning(
                "requirements.txt cài không xong (%s) -- thử `pip install -e .`.",
                err.splitlines()[-1] if err else "?",
            )
        else:
            installed_something = True

    if (work_dir / "pyproject.toml").exists() or (work_dir / "setup.py").exists():
        ok, err = _pip_install(py, ["-e", "."], work_dir, timeout_sec)
        if ok:
            installed_something = True
        elif not installed_something:
            return False, f"`pip install -e .` thất bại: {err}"
        else:
            logger.warning("`pip install -e .` lỗi nhưng requirements đã cài -- chạy tiếp.")

    if not installed_something:
        logger.info(
            "Repo không có requirements.txt/pyproject.toml cài được -- chạy "
            "test với stdlib + pytest (không coi là lỗi)."
        )
    return True, ""


def cleanup_venv(work_dir: Path, keep: bool = False) -> None:
    """Xoá venv sau khi xong repo. Trên dataset nhiều repo, giữ lại venv sẽ
    làm đầy đĩa máy thuê rất nhanh."""
    if keep:
        return
    venv_dir = Path(work_dir) / ".rtb_venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir, onerror=_force_remove)
        logger.info("Đã xoá venv của repo (keep_venv=false).")


# -------------------------------------------------------------------- PYTEST
def _test_target(work_dir: Path) -> str:
    """`tests` nếu có thư mục đó, không thì chạy pytest ở gốc repo."""
    return "tests" if (Path(work_dir) / "tests").is_dir() else "."


def parse_junit(junit_path: Path) -> tuple[dict, str]:
    """Parse junit XML ra số pass/fail + TẬP ID test pass.

    Tập id (chứ không chỉ con số) là thứ Pha E cần để tính REGRESSION_FREE:
    "hybrid pass được toàn bộ test mà baseline pass" mạnh hơn nhiều so với
    "hybrid pass đủ số lượng".
    """
    try:
        tree = ET.parse(junit_path)
    except (ET.ParseError, OSError) as exc:
        return {}, f"không đọc được junit XML: {exc}"

    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    passed_ids: list[str] = []
    failed_ids: list[str] = []
    skipped_ids: list[str] = []
    n_total = n_failed = n_errors = n_skipped = 0
    for suite in suites:
        for case in suite.iter("testcase"):
            n_total += 1
            classname = case.get("classname") or ""
            name = case.get("name") or "?"
            test_id = f"{classname}::{name}" if classname else name

            failed = case.find("failure") is not None
            errored = case.find("error") is not None
            skipped = case.find("skipped") is not None
            if failed:
                n_failed += 1
                failed_ids.append(test_id)
            elif errored:
                n_errors += 1
                failed_ids.append(test_id)
            elif skipped:
                n_skipped += 1
                skipped_ids.append(test_id)
            else:
                passed_ids.append(test_id)

    return {
        "passed_ids": passed_ids,
        "failed_ids": failed_ids,
        "skipped_ids": skipped_ids,
        "n_passed": len(passed_ids),
        "n_total": n_total,
        "n_failed": n_failed,
        "n_errors": n_errors,
        "n_skipped": n_skipped,
    }, ""


def run_pytest(
    py: Path,
    work_dir: Path,
    label: str = "baseline",
    plugin_args: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    timeout_sec: int = DEFAULT_TEST_TIMEOUT_SEC,
    results_dir: Path | None = None,
) -> RepoTestResult:
    """Chạy bộ test của repo, trả về `RepoTestResult`. KHÔNG raise.

    `-p no:cacheprovider`: không ghi `.pytest_cache`, để lượt chạy hybrid
    không thừa hưởng trạng thái của lượt baseline.
    Cố ý KHÔNG dùng `coverage`: nó không cần cho số pass/fail và là một
    phụ thuộc nữa có thể cài thất bại.
    """
    work_dir = Path(work_dir)
    out_dir = Path(results_dir) if results_dir else work_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    junit = out_dir / f"junit_{work_dir.name}_{label}.xml"

    cmd = [
        str(py), "-m", "pytest", _test_target(work_dir),
        "-p", "no:cacheprovider",
        f"--junitxml={junit}",
        "-q", "--no-header",
    ]
    cmd.extend(plugin_args or [])

    result = RepoTestResult(label=label, junit_path=str(junit))
    env = build_child_env(extra_env)
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_sec,
            cwd=str(work_dir), env=env,
        )
    except subprocess.TimeoutExpired:
        result.duration_sec = time.perf_counter() - start
        result.error = f"bộ test quá {timeout_sec}s -> huỷ (TIMEOUT)"
        logger.error("Repo '%s' [%s]: %s", work_dir.name, label, result.error)
        return result
    except OSError as exc:
        result.duration_sec = time.perf_counter() - start
        result.error = f"không chạy được pytest: {exc}"
        return result

    result.duration_sec = time.perf_counter() - start
    result.returncode = proc.returncode

    if not junit.exists():
        # pytest chết trước khi ghi được XML (lỗi import ở conftest, sai cú
        # pháp...). Giữ lại stderr để chẩn đoán, không nuốt lỗi.
        result.error = (
            f"pytest không sinh được junit XML (exit={proc.returncode}). "
            f"stdout/stderr: {((proc.stdout or '') + (proc.stderr or ''))[-600:]}"
        )
        logger.error("Repo '%s' [%s]: %s", work_dir.name, label, result.error)
        return result

    parsed, err = parse_junit(junit)
    if err:
        result.error = err
        return result

    result.ok = True
    result.passed_ids = parsed["passed_ids"]
    result.failed_ids = parsed.get("failed_ids") or []
    result.skipped_ids = parsed.get("skipped_ids") or []
    result.n_passed = parsed["n_passed"]
    result.n_total = parsed["n_total"]
    result.n_failed = parsed["n_failed"]
    result.n_errors = parsed["n_errors"]
    result.n_skipped = parsed["n_skipped"]
    logger.info(
        "Repo '%s' [%s]: %d/%d test pass (%d fail, %d error, %d skip) trong %.1fs",
        work_dir.name, label, result.n_passed, result.n_total,
        result.n_failed, result.n_errors, result.n_skipped, result.duration_sec,
    )
    return result


def baseline_failed(result: RepoTestResult) -> tuple[bool, str]:
    """Baseline có tệ tới mức phải LOẠI repo khỏi so sánh không.

    Theo yêu cầu: baseline fail HẾT hoặc không chạy được -> BASELINE_FAILED,
    và lỗi đó KHÔNG được tính cho hybrid. Baseline fail MỘT PHẦN thì vẫn dùng
    được: so sánh sẽ dựa trên TẬP test mà baseline pass (REGRESSION_FREE ở
    Pha E), nên vài test đỏ sẵn không làm hỏng phép so.
    """
    if not result.ok:
        return True, f"không chạy được bộ test baseline: {result.error}"
    if result.n_total == 0:
        return True, "repo không có test nào để làm oracle"
    if result.n_passed == 0:
        return True, f"baseline fail toàn bộ {result.n_total} test"
    return False, ""


def regression_free(baseline: RepoTestResult, hybrid: RepoTestResult) -> bool | None:
    """REGRESSION_FREE = tập test pass của hybrid CHỨA TOÀN BỘ tập pass của
    baseline. Trả None nếu thiếu dữ liệu một bên.

    Dùng tập id chứ không dùng số lượng: hybrid có thể pass đúng bằng số test
    nhưng là những test khác -- đó vẫn là hồi quy.
    """
    if not (baseline.ok and hybrid.ok):
        return None
    return set(baseline.passed_ids).issubset(set(hybrid.passed_ids))


def regression_breakdown(baseline: RepoTestResult, hybrid: RepoTestResult) -> dict:
    """Chi tiết những test MẤT đi, TÁCH RIÊNG "fail" với "bị skip".

    VÌ SAO PHẢI TÁCH: `regression_free` so tập id nên một test pass ở baseline
    mà BỊ SKIP ở lượt hybrid cũng bị tính là mất. Hai chuyện đó khác nhau về
    bản chất -- fail là bản Rust sai, còn skip thường là điều kiện môi trường
    (thiếu thư viện, khác nền tảng) hoặc `skipif` của chính repo.

    Vẫn giữ `regression_free` NGHIÊM NGẶT (mất là mất), nhưng ghi kèm phân
    loại này để người đọc không kết luận sai nguyên nhân. Âm thầm bỏ qua test
    bị skip sẽ che được đúng trường hợp bản Rust gây ra skip.
    """
    if not (baseline.ok and hybrid.ok):
        return {"available": False}
    lost = set(baseline.passed_ids) - set(hybrid.passed_ids)
    h_failed, h_skipped = set(hybrid.failed_ids), set(hybrid.skipped_ids)
    return {
        "available": True,
        "n_lost": len(lost),
        "lost_now_failing": sorted(lost & h_failed)[:20],
        "lost_now_skipped": sorted(lost & h_skipped)[:20],
        "lost_missing": sorted(lost - h_failed - h_skipped)[:20],
        "n_lost_now_failing": len(lost & h_failed),
        "n_lost_now_skipped": len(lost & h_skipped),
        "n_lost_missing": len(lost - h_failed - h_skipped),
        # Hồi quy THẬT SỰ do bản dịch: chỉ những test chuyển sang FAIL.
        "regression_free_strict": not lost,
        "regression_free_ignoring_skips": not (lost - h_skipped),
    }


def dataset_apr_sr(results: list[RepoTestResult]) -> dict:
    """APR/SR mức DATASET theo định nghĩa RepoTransBench (arXiv:2412.17744).

    APR = TRUNG BÌNH tỉ lệ test pass qua các repo (mỗi repo một phiếu, repo
          nhiều test không lấn át repo ít test).
    SR  = tỉ lệ repo pass TOÀN BỘ test.
    Repo không chạy được test bị loại khỏi cả hai (không tính là 0, vì đó là
    lỗi môi trường chứ không phải chất lượng bản dịch).
    """
    rates = [r.pass_rate for r in results if r.pass_rate is not None]
    n = len(rates)
    return {
        "n_repos_counted": n,
        "apr": (sum(rates) / n) if n else None,
        "sr": (sum(1 for r in results if r.success_rate_flag) / n) if n else None,
    }


# ===========================================================================
# PHẦN 2.4 -- CÁCH LY HAI NHÁNH ABLATION
# ===========================================================================
def uninstall_extensions(
    py: Path, module_names: list[str], work_dir: Path, timeout_sec: int = 300
) -> dict:
    """Gỡ CÀI extension của nhánh trước khỏi venv của repo.

    VÌ SAO BẮT BUỘC: `maturin develop` cài extension vào venv THEO TÊN MODULE,
    và hai nhánh ablation cố ý dùng CÙNG tên module (tên đó nằm trong prompt --
    đổi nó đi thì hai nhánh khác nhau ở hai biến, không còn là ablation một
    biến nữa). Nếu không gỡ, nhánh chạy sau sẽ `import` trúng file `.so` của
    nhánh trước và toàn bộ so sánh thành vô nghĩa -- đúng kiểu lỗi không báo
    lỗi, chỉ làm số liệu sai.

    Xoá 3 nơi, vì bản cài có thể nằm ở bất kỳ nơi nào trong số đó:
      1. gói đã cài trong site-packages (qua `pip uninstall`),
      2. file `.so`/`.pyd` còn sót trong site-packages,
      3. file shim `.py` của Tầng 2 mà Pha D ghi vào gốc `work_dir`.

    Trả về dict mô tả đã gỡ những gì -- ghi vào kết quả để hậu kiểm được.
    """
    removed: dict = {"pip_uninstalled": [], "files_removed": [], "still_importable": []}
    names = [n for n in dict.fromkeys(module_names) if n]
    if not names:
        return removed

    env = build_child_env()
    # 1. pip uninstall (im lặng nếu chưa cài -- không phải lỗi).
    try:
        proc = subprocess.run(
            [str(py), "-m", "pip", "uninstall", "-y", *names],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_sec, env=env,
        )
        for n in names:
            if f"Successfully uninstalled {n}" in (proc.stdout or ""):
                removed["pip_uninstalled"].append(n)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("pip uninstall lỗi (bỏ qua, còn 2 cách dọn nữa): %s", exc)

    # 2. Quét site-packages xoá .so/.pyd/.py còn sót.
    try:
        out = subprocess.run(
            [str(py), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, env=env,
        )
        site_dir = Path((out.stdout or "").strip())
    except (subprocess.TimeoutExpired, OSError):
        site_dir = None

    search_dirs = [d for d in (site_dir, Path(work_dir)) if d and d.is_dir()]
    for d in search_dirs:
        for n in names:
            for pattern in (f"{n}.*.so", f"{n}.so", f"{n}.*.pyd", f"{n}.pyd", f"{n}.py"):
                for hit in d.glob(pattern):
                    try:
                        hit.unlink()
                        removed["files_removed"].append(str(hit))
                    except OSError as exc:
                        logger.warning("Không xoá được %s: %s", hit, exc)

    # 3. XÁC MINH -- không tin vào việc đã gỡ, phải kiểm lại.
    # Đây là chốt an toàn thật: nếu còn import được thì nhánh sau sẽ dùng nhầm
    # bản build của nhánh trước, nên phải biết ngay thay vì phát hiện qua số liệu.
    check = (
        "import importlib.util,json,sys;"
        f"names={names!r};"
        "print(json.dumps([n for n in names if importlib.util.find_spec(n) is not None]))"
    )
    try:
        out = subprocess.run(
            [str(py), "-c", check], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=120,
            cwd=str(work_dir), env=env,
        )
        import json as _json

        for line in reversed((out.stdout or "").strip().splitlines()):
            if line.strip().startswith("["):
                removed["still_importable"] = _json.loads(line)
                break
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        logger.warning("Không xác minh được việc gỡ extension: %s", exc)

    if removed["still_importable"]:
        logger.error(
            "CÁCH LY ABLATION THẤT BẠI: vẫn import được %s sau khi gỡ. Nhánh "
            "chạy sau có thể dùng nhầm bản build của nhánh trước -> số liệu so "
            "sánh KHÔNG dùng được. Kiểm tra quyền ghi trong site-packages.",
            removed["still_importable"],
        )
    else:
        logger.info(
            "Đã cách ly nhánh ablation: gỡ %d gói, xoá %d file, xác minh không "
            "còn import được.",
            len(removed["pip_uninstalled"]), len(removed["files_removed"]),
        )
    return removed
