"""Chặn LỆCH PHIÊN BẢN PyO3 giữa Cargo.toml và prompt.

VÌ SAO CÓ TEST NÀY: lượt chạy thực nghiệm đầu tiên trên máy thuê cho
`compiled = 0` ở **cả 9 repo** dù `generated = 1..5`. Nguyên nhân duy nhất là
crate ghim `pyo3 = "0.22"` còn prompt dạy model viết theo API PyO3 cũ. Lỗi đó
chỉ lộ ra sau khi đã tốn giờ GPU và chạy `cargo check` thật:

    error[E0599]: no method named `clear` found for reference `&PyList`
    error[E0277]: the trait bound `&PyList: PyFunctionArgument<'_, '_>`
                  is not satisfied ... `&'a pyo3::Bound<'py, T>`
    error[E0599]: no method named `add_function` found for reference
                  `&pyo3::types::PyModule`
    error[E0277]: the trait bound `&pyo3::types::PyModule:
                  WrapPyFunctionArg<'_, _>` is not satisfied

Nay đổi version ở MỘT nơi mà quên nơi kia sẽ bị bắt ngay khi chạy test, không
phải đợi tới lượt chạy thật.

Chạy: python tests/test_pyo3_prompt_matches_version.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))

from stage4_llm_transpile.generator_agent import (  # noqa: E402
    PYO3_API_VERSION,
    PYO3_EXAMPLE,
    build_signature_prompt,
)
from stage5_compiler_in_the_loop.crate_builder import (  # noqa: E402
    CARGO_TOML_TEMPLATE,
    PYO3_VERSION,
)

# Các mẫu API PyO3 CŨ. Chúng chỉ được phép xuất hiện trong phần văn bản CẢNH
# BÁO ("đừng dùng cái này"), tuyệt đối không trong khối code mẫu.
OLD_API_PATTERNS = [
    (r"&PyList\b", "&PyList -- phải là &Bound<'_, PyList>"),
    (r"&PyDict\b", "&PyDict -- phải là &Bound<'_, PyDict>"),
    (r"&PyAny\b", "&PyAny -- phải là &Bound<'_, PyAny>"),
    (r"&PyModule\b", "&PyModule -- phải là &Bound<'_, PyModule>"),
    (r"_py:\s*Python", "tham số _py: Python -- PyO3 >= 0.21 không còn dùng"),
    (r"\.clear\(\)", ".clear() -- Bound<PyList> không có, dùng call_method0(\"clear\")"),
]


def rust_code_blocks(text: str) -> list[str]:
    """Chỉ lấy nội dung các khối ```rust ... ``` -- đó là phần model sẽ bắt chước."""
    return re.findall(r"```rust\s*\n(.*?)```", text, flags=re.DOTALL)


def main() -> int:
    problems: list[str] = []
    print("=" * 78)
    print("PYO3: PHIÊN BẢN TRONG CARGO.TOML PHẢI KHỚP API DẠY TRONG PROMPT")
    print("=" * 78)

    # --- 1. Hai hằng số phải khớp nhau ---------------------------------------
    print(f"  crate_builder.PYO3_VERSION     = {PYO3_VERSION}")
    print(f"  generator_agent.PYO3_API_VERSION = {PYO3_API_VERSION}")
    if PYO3_VERSION != PYO3_API_VERSION:
        problems.append(
            f"LỆCH VERSION: Cargo.toml ghim pyo3 {PYO3_VERSION} nhưng prompt dạy "
            f"API {PYO3_API_VERSION}. Đây đúng là nguyên nhân đã làm compiled=0 "
            f"ở cả 9 repo -- sửa cho khớp ở CẢ HAI nơi."
        )

    # --- 2. Cargo.toml sinh ra phải ghim đúng version -----------------------
    cargo = CARGO_TOML_TEMPLATE.format(
        function_name="f", ext_module="f_rsext", edition="2021", pyo3=PYO3_VERSION
    )
    m = re.search(r'pyo3\s*=\s*\{\s*version\s*=\s*"([^"]+)"', cargo)
    pinned = m.group(1) if m else None
    print(f"  version ghim trong Cargo.toml sinh ra = {pinned}")
    if pinned != PYO3_API_VERSION:
        problems.append(f"Cargo.toml ghim '{pinned}', prompt dạy '{PYO3_API_VERSION}'")

    # --- 3. Khối code mẫu không được chứa API cũ ---------------------------
    example = PYO3_EXAMPLE.format(pyo3_version=PYO3_API_VERSION, ext_module="demo_rsext")
    blocks = rust_code_blocks(example)
    print(f"  số khối ```rust trong ví dụ mẫu = {len(blocks)}")
    if not blocks:
        problems.append("PYO3_EXAMPLE không có khối ```rust nào -- model không có khuôn mẫu")
    for i, block in enumerate(blocks):
        for pattern, why in OLD_API_PATTERNS:
            for hit in re.finditer(pattern, block):
                line = block[: hit.start()].count("\n") + 1
                problems.append(f"ví dụ mẫu, khối #{i} dòng {line}: dùng API CŨ -- {why}")

    # --- 4. Ví dụ mẫu phải có chữ ký #[pymodule] kiểu MỚI ------------------
    joined = "\n".join(blocks)
    need = [
        ("#[pymodule]", "thiếu #[pymodule]"),
        ("fn demo_rsext(m: &Bound<'_, PyModule>) -> PyResult<()>",
         "chữ ký #[pymodule] không đúng API Bound"),
        ("&Bound<'_, PyList>", "không có ví dụ nhận list bằng &Bound<'_, PyList>"),
        ("wrap_pyfunction!", "thiếu wrap_pyfunction!"),
        ('call_method0("clear")', "không có ví dụ gọi method qua call_method0"),
        ("use pyo3::prelude::*;", "thiếu `use pyo3::prelude::*;` (mang theo các trait *Methods)"),
    ]
    for token, why in need:
        ok = token in joined
        print(f"  {'OK ' if ok else 'SAI'} ví dụ mẫu chứa: {token}")
        if not ok:
            problems.append(f"ví dụ mẫu: {why}")

    # --- 5. Prompt THẬT của cả hai tầng phải mang khối API -----------------
    for tier, label in (("TIER1_NATIVE", "Tầng 1"), ("TIER2_KERNEL", "Tầng 2")):
        prompt = build_signature_prompt(
            function_name="f", hotspot_source="def f(x): pass", context_text="CTX",
            tier=tier, observed_types="  - #0: list[float](n=3)",
            ext_module="f_rsext",
        )
        has_version = PYO3_API_VERSION in prompt
        has_example = "#[pymodule]" in prompt and "&Bound<'_, PyModule>" in prompt
        print(f"  {'OK ' if (has_version and has_example) else 'SAI'} prompt {label}: "
              f"nêu version={has_version}, có khuôn mẫu={has_example}")
        if not has_version:
            problems.append(f"prompt {label} không nêu phiên bản PyO3")
        if not has_example:
            problems.append(f"prompt {label} không có khuôn mẫu #[pymodule] kiểu Bound")
        # Khối code trong prompt cũng không được chứa API cũ.
        for i, block in enumerate(rust_code_blocks(prompt)):
            for pattern, why in OLD_API_PATTERNS:
                if re.search(pattern, block):
                    problems.append(f"prompt {label}, khối code #{i}: API CŨ -- {why}")

    print()
    if problems:
        print(f"### PYO3 PROMPT/VERSION: THẤT BẠI ({len(problems)} vấn đề)")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("### PYO3 PROMPT/VERSION: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
