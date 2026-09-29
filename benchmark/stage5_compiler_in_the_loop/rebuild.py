"""Rebuild extension PyO3 giữa các vòng tối ưu (Stage 6).

VẤN ĐỀ NÀY SINH RA ĐỂ SỬA: trước đây, sau mỗi vòng `optimize_further()`,
code Rust mới chỉ được ghi thành file NHÁP trong
`versions/rust_pure/pyo3_ext/generated/`. Extension đang nạp trong tiến
trình vẫn là bản CŨ, nên phép đo vòng sau lặp lại đúng số liệu vòng trước ->
speedup giống hệt nhau qua mọi vòng, vô nghĩa.

CÁCH SỬA: mỗi vòng, trước khi đo lại:
    1. Copy draft mới nhất đè vào `pyo3_ext/src/lib.rs` (vị trí maturin build).
    2. Chạy `maturin develop --release`.
    3. Đo lại trong 1 SUBPROCESS MỚI (xem stage6_benchmark/measure_subprocess.py)
       -- extension native KHÔNG reload an toàn trong tiến trình đang chạy.

AN TOÀN VỚI CODE VIẾT TAY: `src/lib.rs` có thể là code người dùng tự viết.
Trước lần ghi đè ĐẦU TIÊN, file gốc được sao lưu thành `lib.rs.orig_backup`,
và `restore_original()` phải được gọi khi kết thúc vòng lặp (run_pipeline.py
gọi trong khối `finally`). Không bao giờ để người dùng mất code vì pipeline.

Trạng thái build trả về:
    REBUILT_OK        build lại thành công, số đo sau đó là của code MỚI
    BUILD_FAILED      maturin build lỗi -> KHÔNG được báo speedup cho vòng này
    SKIPPED_NO_CARGO  máy không có cargo/maturin -> không build được
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("benchmark.stage5_compiler_in_the_loop.rebuild")

REBUILT_OK = "REBUILT_OK"
BUILD_FAILED = "BUILD_FAILED"
SKIPPED_NO_CARGO = "SKIPPED_NO_CARGO"

DEFAULT_BUILD_TIMEOUT_SEC = 300  # build Rust release có thể chậm
BACKUP_SUFFIX = ".orig_backup"


@dataclass
class RebuildResult:
    status: str
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.status == REBUILT_OK


def toolchain_available() -> tuple[bool, str]:
    """Có đủ cargo + maturin để build extension không?"""
    if shutil.which("cargo") is None:
        return False, "không tìm thấy `cargo` trong PATH (cài Rust: https://rustup.rs/)"
    if shutil.which("maturin") is None:
        try:
            import maturin  # noqa: F401,PLC0415
        except ImportError:
            return False, "không tìm thấy `maturin` (pip install maturin)"
    return True, ""


def backup_original_lib(crate_dir: Path) -> Path | None:
    """Sao lưu `src/lib.rs` gốc (chỉ làm 1 lần, không đè bản sao lưu cũ).
    Trả về đường dẫn bản sao lưu, hoặc None nếu không có lib.rs."""
    lib_rs = Path(crate_dir) / "src" / "lib.rs"
    if not lib_rs.exists():
        return None
    backup = lib_rs.with_suffix(lib_rs.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(lib_rs, backup)
        logger.info("Đã sao lưu code Rust viết tay: %s", backup.name)
    return backup


def restore_original_lib(crate_dir: Path) -> bool:
    """Khôi phục `src/lib.rs` từ bản sao lưu và xoá bản sao lưu.
    PHẢI gọi khi kết thúc vòng lặp, kể cả khi có lỗi."""
    lib_rs = Path(crate_dir) / "src" / "lib.rs"
    backup = lib_rs.with_suffix(lib_rs.suffix + BACKUP_SUFFIX)
    if not backup.exists():
        return False
    shutil.copy2(backup, lib_rs)
    backup.unlink()
    logger.info("Đã khôi phục code Rust viết tay gốc về %s", lib_rs.name)
    return True


def install_draft_into_crate(draft_path: Path, crate_dir: Path) -> Path:
    """Copy draft của Generator Agent đè vào vị trí maturin build.
    Tự sao lưu bản gốc trước lần ghi đè đầu tiên."""
    crate_dir = Path(crate_dir)
    backup_original_lib(crate_dir)
    src_dir = crate_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    lib_rs = src_dir / "lib.rs"
    shutil.copy2(Path(draft_path), lib_rs)
    logger.info("Đã nạp draft %s -> %s", Path(draft_path).name, lib_rs)
    return lib_rs


def maturin_develop(
    crate_dir: Path, timeout_sec: int = DEFAULT_BUILD_TIMEOUT_SEC
) -> RebuildResult:
    """Chạy `maturin develop --release` trong `crate_dir`. KHÔNG raise."""
    available, why = toolchain_available()
    if not available:
        logger.warning("Không build lại được extension: %s", why)
        return RebuildResult(SKIPPED_NO_CARGO, why)

    cmd = [sys.executable, "-m", "maturin", "develop", "--release"]
    logger.info("Đang build lại extension: %s (timeout %ds)...", " ".join(cmd), timeout_sec)
    try:
        proc = subprocess.run(
            cmd, cwd=str(crate_dir), capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", timeout=timeout_sec
        )
    except subprocess.TimeoutExpired:
        return RebuildResult(BUILD_FAILED, f"`maturin develop` quá {timeout_sec}s -> huỷ.")
    except OSError as exc:
        return RebuildResult(SKIPPED_NO_CARGO, f"không chạy được maturin: {exc}")

    if proc.returncode != 0:
        detail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-3000:]
        logger.error("Build lại extension THẤT BẠI (exit=%s).", proc.returncode)
        return RebuildResult(BUILD_FAILED, detail)

    logger.info("Build lại extension THÀNH CÔNG.")
    return RebuildResult(REBUILT_OK, (proc.stdout or "")[-1000:])


def rebuild_from_draft(
    draft_path: Path, crate_dir: Path, timeout_sec: int = DEFAULT_BUILD_TIMEOUT_SEC
) -> RebuildResult:
    """Gộp 2 bước: nạp draft vào crate rồi build lại."""
    try:
        install_draft_into_crate(draft_path, crate_dir)
    except OSError as exc:
        return RebuildResult(BUILD_FAILED, f"không copy được draft: {exc}")
    return maturin_develop(crate_dir, timeout_sec)
