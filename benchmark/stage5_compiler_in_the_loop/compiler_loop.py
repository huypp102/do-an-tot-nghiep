"""Stage 5 -- Biên dịch code Rust và PHÂN LOẠI lỗi biên dịch.

Dùng `cargo check` thay vì `cargo build`: nhanh hơn đáng kể vì không sinh
binary/linking, nhưng vẫn chạy đủ phân tích cú pháp, kiểm tra kiểu và
borrow-checker -- tức là bắt được đúng 3 nhóm lỗi ta quan tâm.

Phân loại lỗi thành 4 nhóm, dựa trên MÃ LỖI của rustc (ổn định hơn nhiều so
với so khớp câu chữ tiếng Anh trong thông báo, vốn thay đổi theo phiên bản):

  SYNTAX_ERROR          lỗi cú pháp, parse không nổi
  TYPE_ERROR            sai kiểu, sai số lượng tham số, thiếu trait...
  BORROW_LIFETIME_ERROR vi phạm borrow checker / lifetime / ownership
  OTHER                 còn lại (thiếu crate, tên không tồn tại, ...)

Nhóm lỗi này được đưa vào prompt sửa lỗi để Generator Agent biết mình đang
đối mặt loại vấn đề nào.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.stage5_compiler_in_the_loop.compiler_loop")

SYNTAX_ERROR = "SYNTAX_ERROR"
TYPE_ERROR = "TYPE_ERROR"
BORROW_LIFETIME_ERROR = "BORROW_LIFETIME_ERROR"
OTHER = "OTHER"

DEFAULT_CARGO_TIMEOUT_SEC = 300

# Mã lỗi rustc -> nhóm. Không liệt kê hết (rustc có hàng trăm mã), chỉ các mã
# hay gặp nhất khi LLM sinh code Rust; phần còn lại rơi vào OTHER.
_ERROR_CODE_GROUPS: dict[str, str] = {
    # --- kiểu / trait / chữ ký hàm ---
    "E0308": TYPE_ERROR,   # mismatched types
    "E0277": TYPE_ERROR,   # trait bound không thoả
    "E0061": TYPE_ERROR,   # sai số lượng tham số
    "E0609": TYPE_ERROR,   # không có field đó
    "E0599": TYPE_ERROR,   # không có method đó
    "E0369": TYPE_ERROR,   # toán tử không áp dụng được cho kiểu này
    "E0605": TYPE_ERROR,   # cast không hợp lệ
    "E0107": TYPE_ERROR,   # sai số lượng generic argument
    # --- borrow checker / lifetime / ownership ---
    "E0502": BORROW_LIFETIME_ERROR,  # mượn vừa mutable vừa immutable
    "E0499": BORROW_LIFETIME_ERROR,  # mượn mutable nhiều lần
    "E0382": BORROW_LIFETIME_ERROR,  # dùng giá trị sau khi move
    "E0505": BORROW_LIFETIME_ERROR,  # move khi đang bị mượn
    "E0506": BORROW_LIFETIME_ERROR,  # gán vào biến đang bị mượn
    "E0597": BORROW_LIFETIME_ERROR,  # sống không đủ lâu
    "E0515": BORROW_LIFETIME_ERROR,  # trả tham chiếu tới biến local
    "E0596": BORROW_LIFETIME_ERROR,  # mượn mutable từ biến immutable
    "E0106": BORROW_LIFETIME_ERROR,  # thiếu lifetime specifier
}

# Lỗi cú pháp thường KHÔNG có mã E####; nhận diện qua dấu hiệu của parser.
_SYNTAX_HINTS = (
    "expected one of",
    "unexpected token",
    "expected `",
    "unclosed delimiter",
    "mismatched closing delimiter",
    "unexpected closing delimiter",
    "expected expression",
    "expected identifier",
    "this file contains an unclosed delimiter",
)


@dataclass
class CompileResult:
    """Kết quả 1 lần `cargo check`."""

    ok: bool
    error_class: str = OTHER
    error_codes: list[str] = field(default_factory=list)
    n_errors: int = 0
    output: str = ""          # stderr đã rút gọn, đưa vào prompt sửa lỗi
    skipped: bool = False     # True khi không có cargo -> không kết luận được
    skip_reason: str = ""


def cargo_available() -> bool:
    return shutil.which("cargo") is not None


def classify_error(stderr: str, error_codes: list[str] | None = None) -> str:
    """Xếp output lỗi vào 1 trong 4 nhóm. Ưu tiên mã lỗi rustc; nếu không có
    mã thì suy từ dấu hiệu parser; không khớp gì thì OTHER."""
    text = stderr or ""
    codes = error_codes or re.findall(r"\[(E\d{4})\]", text)

    # Borrow/lifetime được ưu tiên báo cáo hơn type, vì khi cả hai cùng xuất
    # hiện thì borrow checker thường là nguyên nhân gốc mà LLM hay sai nhất.
    groups = {_ERROR_CODE_GROUPS.get(c, OTHER) for c in codes}
    if BORROW_LIFETIME_ERROR in groups:
        return BORROW_LIFETIME_ERROR
    if TYPE_ERROR in groups:
        return TYPE_ERROR

    lowered = text.lower()
    if any(h in lowered for h in _SYNTAX_HINTS):
        return SYNTAX_ERROR

    return OTHER


def _extract_json_diagnostics(stdout: str) -> tuple[list[str], int, list[str]]:
    """Đọc output `--message-format=json` của cargo.
    Trả về (mã lỗi, số lỗi, các thông điệp đã render)."""
    codes: list[str] = []
    rendered: list[str] = []
    n_errors = 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        if msg.get("level") != "error":
            continue
        n_errors += 1
        code = (msg.get("code") or {}).get("code") if isinstance(msg.get("code"), dict) else None
        if code:
            codes.append(code)
        if msg.get("rendered"):
            rendered.append(msg["rendered"])
    return codes, n_errors, rendered


def compile_and_classify(
    crate_dir: Path, timeout_sec: int = DEFAULT_CARGO_TIMEOUT_SEC
) -> CompileResult:
    """Chạy `cargo check` trên crate tại `crate_dir`, phân loại lỗi nếu có.

    KHÔNG raise khi thiếu cargo hay cargo chạy lỗi hạ tầng -- trả về
    CompileResult(skipped=True) kèm lý do, để pipeline vẫn chạy tiếp (máy dev
    có thể chưa cài Rust toolchain; máy thuê GPU thì có).
    """
    crate_dir = Path(crate_dir)
    if not (crate_dir / "Cargo.toml").exists():
        return CompileResult(
            ok=False, skipped=True,
            skip_reason=f"Không thấy Cargo.toml trong {crate_dir} -- không phải crate Rust.",
        )

    if not cargo_available():
        return CompileResult(
            ok=False, skipped=True,
            skip_reason=(
                "Không tìm thấy `cargo` trong PATH -- bỏ qua bước biên dịch. "
                "Cài Rust toolchain (https://rustup.rs/) để bật Stage 5. "
                "Trong Docker, image đã cài sẵn (xem Dockerfile)."
            ),
        )

    cmd = ["cargo", "check", "--message-format=json", "--quiet"]
    logger.info("Stage 5: chạy `cargo check` tại %s ...", crate_dir)
    try:
        proc = subprocess.run(
            cmd, cwd=str(crate_dir), capture_output=True, text=True, timeout=timeout_sec
        )
    except subprocess.TimeoutExpired:
        return CompileResult(
            ok=False, error_class=OTHER,
            output=f"`cargo check` quá {timeout_sec}s -> huỷ.",
        )
    except OSError as exc:
        return CompileResult(
            ok=False, skipped=True, skip_reason=f"Không chạy được cargo: {exc}",
        )

    if proc.returncode == 0:
        logger.info("Stage 5: biên dịch THÀNH CÔNG.")
        return CompileResult(ok=True, n_errors=0)

    codes, n_errors, rendered = _extract_json_diagnostics(proc.stdout)
    # stderr chứa bản render người đọc được; nếu trống thì dùng bản từ JSON.
    human = (proc.stderr or "").strip() or "\n".join(rendered)
    error_class = classify_error(human, codes)

    logger.warning(
        "Stage 5: biên dịch THẤT BẠI -- %d lỗi, nhóm=%s, mã=%s",
        n_errors or len(codes), error_class, sorted(set(codes)) or "(không có mã)",
    )
    return CompileResult(
        ok=False,
        error_class=error_class,
        error_codes=sorted(set(codes)),
        n_errors=n_errors or len(codes),
        output=human[:6000],
    )
