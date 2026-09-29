"""PHA D -- MỘT crate Rust RIÊNG cho MỖI hotspot, build vào venv của repo.

VÌ SAO MỖI HOTSPOT MỘT CRATE (không dồn vào `pyo3_ext/src/lib.rs` như trước):
  1. Chữ ký khác nhau. Trước Pha D mọi hàm bị ép về `Vec<u8>` + width/height
     (use-case ảnh). Repo RepoTransBench có hàm nhận list số, chuỗi, dict,
     đối tượng -- không dồn chung một quy ước I/O được.
  2. Cô lập thất bại. Một hotspot không biên dịch được thì chỉ nó bị loại;
     dồn chung một lib.rs thì một lỗi làm chết cả mẻ.
  3. Không đụng code viết tay. `versions/rust_pure/pyo3_ext/src/lib.rs` là
     file người dùng tự viết -- crate ở đây nằm trong `work_dir` của repo,
     hoàn toàn tách biệt.

HAI TẦNG (tầng do `deep_compare.classify_tier` quyết định, không do LLM):
  Tầng 1 `TIER1_NATIVE`  -- Rust nhận/trả kiểu gốc. `rust_pure` = chính hàm
                            Rust đó; `hybrid_pyo3` cũng trỏ vào nó (phần còn
                            lại của repo vẫn là Python -> đó chính là hybrid).
  Tầng 2 `TIER2_KERNEL`  -- Rust chỉ là kernel trên kiểu gốc; một shim Python
                            mỏng tháo đối tượng ra và đóng gói kết quả lại.
                            `hybrid_pyo3` trỏ vào SHIM, không trỏ vào kernel.

THỨ TỰ THI HÀNH (yêu cầu Pha D): làm xong và kiểm thử Tầng 1 TRƯỚC, rồi mới
tới Tầng 2. Tầng 2 hỏng không được chặn Tầng 1 -- xem `build_all`.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.stage5_compiler_in_the_loop.crate_builder")

PYO3_VERSION = "0.22"      # khớp versions/rust_pure/pyo3_ext/Cargo.toml
RUST_EDITION = "2021"
DEFAULT_BUILD_TIMEOUT_SEC = 600

BUILT_OK = "BUILT_OK"
BUILD_FAILED = "BUILD_FAILED"
SKIPPED_NO_TOOLCHAIN = "SKIPPED_NO_TOOLCHAIN"

SIGNATURE_EXTENDED = "extended"
SIGNATURE_NATIVE_ONLY = "native_only"


def ext_module_name(function_name: str) -> str:
    """Tên `#[pymodule]` cho hotspot. Phải là identifier Rust/Python hợp lệ.

    Thêm hậu tố `_rsext` để không đụng tên module nào của repo: một repo có
    hàm `checksum` rất có thể cũng có module `checksum`, và nạp trùng tên sẽ
    che module thật của repo.
    """
    safe = re.sub(r"[^0-9A-Za-z_]", "_", function_name).strip("_") or "hotspot"
    if safe[0].isdigit():
        safe = f"f_{safe}"
    return f"{safe}_rsext"


def shim_module_name(function_name: str) -> str:
    """Tên module Python chứa shim của Tầng 2."""
    return f"{ext_module_name(function_name)}_shim"


@dataclass
class CrateResult:
    """Kết quả dựng + build crate cho 1 hotspot."""

    function_name: str
    status: str = BUILD_FAILED
    tier: str = ""
    ext_module: str = ""
    ext_func: str = ""
    ext_root: str = ""
    crate_dir: str = ""
    shim_module: str = ""
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.status == BUILT_OK

    def version_targets(self) -> dict[str, dict]:
        """Đặc tả cho `versions/registry.py`: phiên bản nào trỏ vào đâu.

        Tầng 2: `hybrid_pyo3` PHẢI trỏ vào shim Python (nó mới có chữ ký giống
        hàm gốc), còn `rust_pure` trỏ vào kernel -- kernel nhận kiểu gốc nên
        chỉ so/đo được khi hotspot vốn đã là kiểu gốc. Với Tầng 2 ta cố ý
        KHÔNG khai báo `rust_pure`: "Rust thuần" cho một hàm nhận đối tượng
        Python là điều không tồn tại, khai báo nó sẽ là số liệu giả.
        """
        from stage1_profiling.deep_compare import TIER_KERNEL

        if not self.ok:
            return {}
        if self.tier == TIER_KERNEL:
            if not self.shim_module:
                return {}
            return {
                "hybrid_pyo3": {
                    "ext_module": self.shim_module,
                    "ext_func": self.function_name,
                    "ext_root": self.ext_root,
                },
            }
        return {
            "rust_pure": {
                "ext_module": self.ext_module,
                "ext_func": self.ext_func,
                "ext_root": self.ext_root,
            },
            "hybrid_pyo3": {
                "ext_module": self.ext_module,
                "ext_func": self.ext_func,
                "ext_root": self.ext_root,
            },
        }

    def as_dict(self) -> dict:
        return {
            "function_name": self.function_name, "status": self.status,
            "tier": self.tier, "ext_module": self.ext_module,
            "ext_func": self.ext_func, "shim_module": self.shim_module,
            "crate_dir": self.crate_dir, "output": self.output[:2000],
        }


def toolchain_available() -> tuple[bool, str]:
    """cargo + maturin có trên máy không."""
    if shutil.which("cargo") is None:
        return False, "không thấy `cargo` trong PATH (chưa cài Rust toolchain)"
    if shutil.which("maturin") is None:
        return False, "không thấy `maturin` trong PATH (`pip install maturin`)"
    return True, ""


CARGO_TOML_TEMPLATE = """# Crate do PHA D sinh TỰ ĐỘNG cho hotspot `{function_name}`.
# Nằm trong work_dir của repo, KHÔNG liên quan tới
# versions/rust_pure/pyo3_ext/ (code viết tay của người dùng).
[package]
name = "{ext_module}"
version = "0.1.0"
edition = "{edition}"

[lib]
name = "{ext_module}"
crate-type = ["cdylib"]

[dependencies]
pyo3 = {{ version = "{pyo3}", features = ["extension-module"] }}

[profile.release]
opt-level = 3
lto = true
"""

PYPROJECT_TEMPLATE = """[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "{ext_module}"
version = "0.1.0"
requires-python = ">=3.9"

[tool.maturin]
module-name = "{ext_module}"
"""


def make_crate(
    function_name: str,
    rust_code: str,
    work_dir: Path,
    tier: str = "",
    python_shim: str = "",
    arm: str = "",
) -> CrateResult:
    """Dựng thư mục crate (chưa build). KHÔNG raise.

    `arm` (PHẦN 2.4) chỉ đổi ĐƯỜNG DẪN trên đĩa, KHÔNG đổi tên module. Tên
    module đi vào prompt, nên nếu nó khác nhau giữa hai nhánh thì hai prompt
    khác nhau ở hai biến chứ không phải một, và ablation mất giá trị. Cách ly
    bản CÀI ĐẶT được lo riêng bằng `repo_runner.uninstall_extensions()`.
    """
    ext_module = ext_module_name(function_name)
    crate_dir = Path(work_dir) / (".rtb_crates" + (f"_{arm}" if arm else "")) / ext_module
    result = CrateResult(
        function_name=function_name, tier=tier,
        ext_module=ext_module, ext_func=function_name,
        crate_dir=str(crate_dir), ext_root=str(Path(work_dir).resolve()),
    )

    if not (rust_code or "").strip():
        result.output = "Stage 4 không sinh được code Rust nào để build"
        return result

    try:
        (crate_dir / "src").mkdir(parents=True, exist_ok=True)
        (crate_dir / "Cargo.toml").write_text(
            CARGO_TOML_TEMPLATE.format(
                function_name=function_name, ext_module=ext_module,
                edition=RUST_EDITION, pyo3=PYO3_VERSION,
            ),
            encoding="utf-8",
        )
        (crate_dir / "pyproject.toml").write_text(
            PYPROJECT_TEMPLATE.format(ext_module=ext_module), encoding="utf-8"
        )
        (crate_dir / "src" / "lib.rs").write_text(rust_code, encoding="utf-8")
    except OSError as exc:
        result.output = f"không ghi được crate: {exc}"
        return result

    # Tầng 2: shim Python phải nằm ở gốc work_dir để `import <shim_module>`
    # chạy được trong venv của repo (work_dir đã có trong sys.path).
    from stage1_profiling.deep_compare import TIER_KERNEL

    if tier == TIER_KERNEL:
        if not (python_shim or "").strip():
            result.output = (
                "hotspot ở Tầng 2 (kernel extraction) nhưng LLM không trả về "
                "khối `## Python shim` -- không có gì để gọi kernel Rust"
            )
            return result
        shim_mod = shim_module_name(function_name)
        header = (
            f"# Shim Python do PHA D sinh cho hotspot `{function_name}` (Tầng 2).\n"
            f"# Tháo đối tượng -> gọi kernel Rust `{ext_module}` -> đóng gói kết quả.\n"
        )
        try:
            (Path(work_dir) / f"{shim_mod}.py").write_text(
                header + python_shim + "\n", encoding="utf-8"
            )
        except OSError as exc:
            result.output = f"không ghi được shim Python: {exc}"
            return result
        result.shim_module = shim_mod

    result.status = BUILD_FAILED  # chờ build
    result.output = "crate đã dựng, chưa build"
    return result


def build_crate(
    result: CrateResult,
    venv_python: Path,
    timeout_sec: int = DEFAULT_BUILD_TIMEOUT_SEC,
) -> CrateResult:
    """`maturin develop --release` để cài extension vào VENV CỦA REPO.

    Phải là venv của repo, không phải venv benchmark: extension chỉ có ích khi
    nằm cùng chỗ với các module của repo, vì cả phép so khớp (Pha B) lẫn phép
    đo (Pha F) đều chạy trong venv đó.
    """
    from stage5_compiler_in_the_loop.repo_runner import build_child_env

    available, why = toolchain_available()
    if not available:
        result.status = SKIPPED_NO_TOOLCHAIN
        result.output = why
        logger.warning("Pha D [%s]: %s -- bỏ qua build.", result.function_name, why)
        return result

    crate_dir = Path(result.crate_dir)
    venv_dir = Path(venv_python).parent.parent
    env = build_child_env({
        # maturin develop cài vào venv đang "active" -- chỉ ra bằng VIRTUAL_ENV.
        "VIRTUAL_ENV": str(venv_dir),
        "PATH": os.pathsep.join([str(Path(venv_python).parent), os.environ.get("PATH", "")]),
    })
    cmd = ["maturin", "develop", "--release", "--interpreter", str(venv_python)]

    try:
        proc = subprocess.run(
            cmd, cwd=str(crate_dir), capture_output=True, text=True,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
            timeout=timeout_sec, env=env,
        )
    except subprocess.TimeoutExpired:
        result.status = BUILD_FAILED
        result.output = f"maturin develop quá {timeout_sec}s -> huỷ"
        return result
    except OSError as exc:
        result.status = BUILD_FAILED
        result.output = f"không chạy được maturin: {exc}"
        return result

    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        result.status = BUILD_FAILED
        result.output = combined[-3000:]
        logger.error(
            "Pha D [%s]: maturin develop THẤT BẠI (exit=%d).",
            result.function_name, proc.returncode,
        )
        return result

    result.status = BUILT_OK
    result.output = combined[-1000:]
    logger.info(
        "Pha D [%s]: đã build extension '%s' vào venv của repo (tier=%s).",
        result.function_name, result.ext_module, result.tier or "?",
    )
    return result


@dataclass
class BuildPlan:
    """Kế hoạch build chia theo TẦNG, để thi hành đúng thứ tự yêu cầu."""

    tier1: list[str] = field(default_factory=list)
    tier2: list[str] = field(default_factory=list)
    unsupported: dict[str, str] = field(default_factory=dict)


def plan_by_tier(
    tiers: dict[str, str], signature_support: str = SIGNATURE_EXTENDED
) -> BuildPlan:
    """Chia hotspot theo tầng và áp `signature_support`.

    `native_only` là van an toàn cho lượt chạy muốn số liệu "sạch": chỉ nhận
    hotspot mà Rust dịch được nguyên chữ ký, mọi thứ cần shim đều bị loại
    thành `UNSUPPORTED_KIND` thay vì âm thầm đo một thứ khác.
    """
    from stage1_profiling.deep_compare import TIER_KERNEL, TIER_NATIVE

    plan = BuildPlan()
    for name, tier in tiers.items():
        if tier == TIER_NATIVE:
            plan.tier1.append(name)
        elif tier == TIER_KERNEL:
            if signature_support == SIGNATURE_NATIVE_ONLY:
                plan.unsupported[name] = (
                    "cần Tầng 2 (kernel + shim) nhưng config đặt "
                    "signature_support=native_only"
                )
            else:
                plan.tier2.append(name)
        else:
            plan.unsupported[name] = (
                f"ngoài Tầng 1 và Tầng 2 (tier={tier or 'không xác định'})"
            )
    return plan


def build_all(
    plan: BuildPlan,
    rust_by_function: dict[str, dict],
    tiers: dict[str, str],
    work_dir: Path,
    venv_python: Path,
    timeout_sec: int = DEFAULT_BUILD_TIMEOUT_SEC,
    arm: str = "",
) -> dict[str, CrateResult]:
    """Build THEO THỨ TỰ: hết Tầng 1 rồi mới tới Tầng 2.

    Mỗi hotspot được cô lập: lỗi của một hotspot (kể cả toàn bộ Tầng 2) không
    chặn những hotspot đã xong -- đúng yêu cầu "Tầng 2 hỏng không được chặn
    Tầng 1".
    """
    results: dict[str, CrateResult] = {}

    for wave_name, names in (("Tầng 1", plan.tier1), ("Tầng 2", plan.tier2)):
        if not names:
            continue
        logger.info("Pha D: build %s -- %d hotspot: %s", wave_name, len(names), names)
        for name in names:
            info = rust_by_function.get(name) or {}
            crate = make_crate(
                function_name=name,
                rust_code=info.get("rust_code") or "",
                work_dir=work_dir,
                tier=tiers.get(name, ""),
                python_shim=info.get("python_shim") or "",
                arm=arm,
            )
            if crate.output == "crate đã dựng, chưa build":
                crate = build_crate(crate, venv_python, timeout_sec)
            results[name] = crate
        n_ok = sum(1 for n in names if results.get(n) and results[n].ok)
        logger.info("Pha D: %s xong -- %d/%d build được.", wave_name, n_ok, len(names))

    return results
