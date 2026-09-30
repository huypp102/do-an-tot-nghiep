"""LẦN CHẠY CHẨN ĐOÁN, mục B1 -- dò GÕ CỨNG TĨNH trên mã Rust đã biên dịch
được. CHẾ ĐỘ BÓNG: chỉ ĐO, KHÔNG đổi quyết định accept/reject/compiled.

Dấu hiệu gõ cứng (mỗi cái khớp một lý do riêng trong `reasons`, không loại
trừ nhau -- 1 hàm có thể khớp nhiều dấu hiệu cùng lúc):
  no_param_used       -- thân hàm không hề nhắc tới tên bất kỳ tham số nào
                          (kể cả khi hàm CÓ tham số) -> nghi trả về hằng số
                          bất kể đầu vào.
  literal_return       -- biểu thức trả về (statement cuối cùng, hoặc sau
                          `return`) là literal (số/chuỗi/mảng hằng) chứ
                          không tính toán từ biến nào.
  suspicious_comment  -- chứa từ khoá gợi ý code giả/gán cứng
                          (hardcoded/placeholder/expected output/TODO/FIXME/
                          stub), không phân biệt hoa thường.

CHỈ PHÂN TÍCH TĨNH bằng regex đơn giản trên TEXT Rust -- không parse cú
pháp thật (không cần crate syn/thêm phụ thuộc Rust nào). Đủ cho việc dò
NGHI VẤN cần người đọc lại, không phải bằng chứng chắc chắn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SIGNATURE_RE = re.compile(
    r"fn\s+\w+\s*\(([^)]*)\)", re.DOTALL,
)
_SUSPICIOUS_KEYWORDS = (
    "hardcoded", "hard-coded", "hard coded", "placeholder",
    "expected output", "expected_output", "todo", "fixme", "stub",
)
_LITERAL_RETURN_RE = re.compile(
    r"""^\s*
        (return\s+)?
        (
            -?\d+(\.\d+)?([iuf](8|16|32|64|128|size))?   # số nguyên/thực Rust
          | "[^"]*"(\.to_string\(\)|\.to_owned\(\)|\.into\(\))?  # chuỗi hằng
          | \[[\d\s,.\-]*\]                               # mảng hằng số
          | true | false
        )
        \s*;?\s*$""",
    re.VERBOSE,
)


@dataclass
class HardcodingVerdict:
    flagged: bool = False
    reasons: list[str] = field(default_factory=list)
    param_names: list[str] = field(default_factory=list)
    tail_expression: str = ""

    def as_dict(self) -> dict:
        return {
            "flagged": self.flagged, "reasons": self.reasons,
            "param_names": self.param_names, "tail_expression": self.tail_expression,
        }


def _extract_param_names(rust_code: str) -> list[str]:
    m = _SIGNATURE_RE.search(rust_code)
    if not m:
        return []
    names = []
    for part in m.group(1).split(","):
        part = part.strip()
        if not part or part == "self" or part.startswith("&self"):
            continue
        # "name: Type" hoặc "mut name: Type" -- lấy token trước dấu ':'.
        name = part.split(":", 1)[0].strip()
        name = name.removeprefix("mut ").strip()
        if name and name != "py":  # `py: Python<'_>` của PyO3 không phải input thật
            names.append(name)
    return names


def _extract_body(rust_code: str) -> str:
    """Thân hàm ĐẦU TIÊN trong `rust_code` -- đếm ngoặc nhọn thô, đủ dùng cho
    1 file chỉ có 1 hàm PyO3 (đúng hình dạng code Generator Agent sinh ra)."""
    sig = _SIGNATURE_RE.search(rust_code)
    if not sig:
        return rust_code
    start = rust_code.find("{", sig.end())
    if start == -1:
        return ""
    depth = 0
    for i in range(start, len(rust_code)):
        if rust_code[i] == "{":
            depth += 1
        elif rust_code[i] == "}":
            depth -= 1
            if depth == 0:
                return rust_code[start + 1:i]
    return rust_code[start + 1:]


def _tail_expression(body: str) -> str:
    """Dòng KHÔNG-RỖNG, KHÔNG-COMMENT cuối cùng của thân hàm -- proxy rẻ tiền
    cho "biểu thức trả về", không cần parse cú pháp thật."""
    lines = [
        ln.strip() for ln in body.splitlines()
        if ln.strip() and not ln.strip().startswith("//")
    ]
    return lines[-1] if lines else ""


def detect_static_hardcoding(rust_code: str) -> HardcodingVerdict:
    v = HardcodingVerdict()
    if not rust_code or not rust_code.strip():
        return v

    v.param_names = _extract_param_names(rust_code)
    body = _extract_body(rust_code)
    lower_body = body.lower()

    if v.param_names:
        used = any(
            re.search(rf"\b{re.escape(p)}\b", body) for p in v.param_names
        )
        if not used:
            v.reasons.append("no_param_used")

    v.tail_expression = _tail_expression(body)
    if v.tail_expression and _LITERAL_RETURN_RE.match(v.tail_expression):
        v.reasons.append("literal_return")

    matched_kw = [kw for kw in _SUSPICIOUS_KEYWORDS if kw in lower_body]
    if matched_kw:
        v.reasons.append("suspicious_comment")
        v.tail_expression = v.tail_expression or ""  # không đổi, chỉ để rõ ý

    v.flagged = bool(v.reasons)
    return v
