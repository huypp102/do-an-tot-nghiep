"""Stage 4 -- Generator Agent: dịch 1 hotspot Python sang Rust bằng LLM.

Prompt template phỏng theo POLO (Bai et al., IJCAI-25) Fig.5 "Generator Agent
Prompt Template", NHƯNG đơn giản hoá cho mục tiêu khác:
  - POLO: tối ưu code C++ TẠI CHỖ (đổi data structure, preallocate, inline...).
  - Ở đây: DỊCH hàm Python sang Rust (expose qua PyO3) sao cho giữ nguyên
    hành vi số học, chạy được in-process cùng phần Python còn lại.
Các mảnh context (hotspot calls X / X calls hotspot / class bao ngoài / import)
lấy nguyên tinh thần Fig.5, do `stage3_context_packaging/packager.py` chuẩn bị.

ĐẦU RA: ghi file `versions/rust_pure/pyo3_ext/generated/<function_name>.rs`.
    *** KHÔNG BAO GIỜ ghi đè `versions/rust_pure/pyo3_ext/src/lib.rs` ***
    Đó là file người dùng tự viết tay; code LLM sinh ra chỉ là BẢN NHÁP để
    người đọc, đối chiếu rồi tự merge vào lib.rs khi thấy đúng. Thư mục
    generated/ đã được .gitignore để bản nháp không lẫn vào lịch sử git.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("benchmark.stage4_llm_transpile.generator_agent")

BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
GENERATED_DIR = BENCHMARK_ROOT / "versions" / "rust_pure" / "pyo3_ext" / "generated"

# VAI TRÒ của agent này. Gửi kèm MỌI lượt gọi, và chỉ agent này dùng --
# Decision Agent có system prompt hoàn toàn khác (xem decision_agent.py).
# ---------------------------------------------------------------------------
# API PYO3 -- KHUÔN MẪU BẮT BUỘC ĐƯA VÀO PROMPT
#
# VÌ SAO KHỐI NÀY TỒN TẠI (bằng chứng từ lượt chạy thật đầu tiên, 9 repo):
# `compiled = 0` ở CẢ 9 repo dù `generated = 1..5`. Nguyên nhân: crate ghim
# pyo3 0.22 nhưng prompt không hề nói phiên bản, nên model viết theo API 0.1x
# và `cargo check` trả về:
#
#     error[E0599]: no method named `clear` found for reference `&PyList`
#     error[E0277]: the trait bound `&PyList: PyFunctionArgument<'_, '_>`
#                   is not satisfied ... `&'a pyo3::Bound<'py, T>`
#     error[E0599]: no method named `add_function` found for reference
#                   `&pyo3::types::PyModule`
#     error[E0277]: the trait bound `&pyo3::types::PyModule:
#                   WrapPyFunctionArg<'_, _>` is not satisfied
#
# PyO3 0.21 đổi sang API `Bound<'py, T>`; mọi tham chiếu trần (`&PyList`,
# `&PyModule`) không còn dùng được làm tham số `#[pyfunction]`/`#[pymodule]`.
# Model không thể tự đoán ra điều đó, nên prompt phải DẠY nó -- và phải dạy
# bằng một ví dụ đầy đủ, không phải bằng vài dòng mô tả.
#
# `PYO3_API_VERSION` phải LUÔN khớp `crate_builder.PYO3_VERSION`. Lệch nhau là
# tái diễn đúng lỗi trên, nên có test tự động chặn:
# tests/test_pyo3_prompt_matches_version.py
# ---------------------------------------------------------------------------
PYO3_API_VERSION = "0.22"

PYO3_API_RULES = """**API PyO3 {pyo3_version} -- BẮT BUỘC ĐÚNG, ĐÂY LÀ LỖI HAY GẶP NHẤT**:
PyO3 >= 0.21 dùng API `Bound<'py, T>`. Tham chiếu trần kiểu CŨ (`&PyList`, \
`&PyDict`, `&PyAny`, `&PyModule`) KHÔNG còn biên dịch được -- nó cho \
`error[E0277]: the trait bound ... PyFunctionArgument is not satisfied`.

- Nhận list/dict/object bất kỳ từ Python: `&Bound<'_, PyList>`, \
`&Bound<'_, PyDict>`, `&Bound<'_, PyAny>`.
- Hàm module: `#[pymodule] fn {ext_module}(m: &Bound<'_, PyModule>) -> PyResult<()>` \
-- KHÔNG có tham số `_py: Python` riêng như bản cũ.
- `wrap_pyfunction!(ten_ham, m)?` với `m` là `&Bound<'_, PyModule>`.
- Trên `Bound<'_, PyList>` dùng các method có sẵn: `.len()`, `.get_item(i)?`, \
`.set_item(i, v)?`, `.append(v)?`, `.insert(i, v)?`, `.del_item(i)?`, `.iter()`.
- Method KHÔNG có trong danh sách trên (ví dụ `clear`) thì gọi qua \
`.call_method0("clear")?` / `.call_method1("ten", (arg,))?`. Đừng gọi thẳng \
`.clear()` -- nó không tồn tại và cho `error[E0599]: no method named `clear``.
- Lấy giá trị Rust từ phần tử: `let x: f64 = list.get_item(i)?.extract()?;`
- `use pyo3::prelude::*;` đã mang theo các trait `PyListMethods`, \
`PyDictMethods`, `PyAnyMethods`, `PyModuleMethods` -- thiếu nó thì mọi method \
trên đều "không tìm thấy".
"""

PYO3_EXAMPLE = """**KHUÔN MẪU ĐÚNG cho PyO3 {pyo3_version}** (bám sát khuôn này, chỉ thay \
tên hàm và phần thân):
```rust
use pyo3::prelude::*;
use pyo3::types::{{PyList, PyModule}};

/// Kiểu gốc vào, kiểu gốc ra: nhận `Vec<f64>` là đủ và đơn giản nhất.
#[pyfunction]
fn sum_squares(values: Vec<f64>) -> PyResult<f64> {{
    Ok(values.iter().map(|v| v * v).sum())
}}

/// SỬA ĐỐI SỐ TẠI CHỖ: phải nhận `&Bound<'_, PyList>`, không phải `Vec<f64>`
/// (Vec là bản copy nên bên Python không thấy thay đổi).
#[pyfunction]
fn accumulate_inplace(buffer: &Bound<'_, PyList>, addend: f64) -> PyResult<()> {{
    for i in 0..buffer.len() {{
        let current: f64 = buffer.get_item(i)?.extract()?;
        buffer.set_item(i, current + addend)?;
    }}
    Ok(())
}}

/// Method không có sẵn trên Bound<PyList> thì gọi qua call_method0.
#[pyfunction]
fn clear_all(items: &Bound<'_, PyList>) -> PyResult<()> {{
    items.call_method0("clear")?;
    Ok(())
}}

#[pymodule]
fn {ext_module}(m: &Bound<'_, PyModule>) -> PyResult<()> {{
    m.add_function(wrap_pyfunction!(sum_squares, m)?)?;
    m.add_function(wrap_pyfunction!(accumulate_inplace, m)?)?;
    m.add_function(wrap_pyfunction!(clear_all, m)?)?;
    Ok(())
}}
```
"""


def pyo3_api_block(ext_module: str) -> str:
    """Khối quy tắc API + ví dụ mẫu, chèn vào prompt của cả hai tầng."""
    return (
        PYO3_API_RULES.format(pyo3_version=PYO3_API_VERSION, ext_module=ext_module)
        + "\n"
        + PYO3_EXAMPLE.format(pyo3_version=PYO3_API_VERSION, ext_module=ext_module)
    )


GENERATOR_SYSTEM_PROMPT = f"""Bạn là lập trình viên Rust/PyO3 chuyên dịch code Python \
hiệu năng cao sang Rust.

VAI TRÒ DUY NHẤT của bạn: SINH và SỬA code Rust.
- Bạn KHÔNG đánh giá xem kết quả benchmark có tốt hay không.
- Bạn KHÔNG quyết định chấp nhận hay loại bỏ một bản dịch.
- Việc đó thuộc về một agent khác, độc lập với bạn.

Nguyên tắc khi viết code:
1. Giữ NGUYÊN hành vi số học của bản Python gốc.
2. Dùng ĐÚNG API PyO3 {PYO3_API_VERSION}, tức API `Bound<'py, T>`. Tham chiếu \
trần của các bản PyO3 cũ (`&PyList`, `&PyDict`, `&PyModule`) KHÔNG còn biên \
dịch được. Hàm module là `#[pymodule] fn ten(m: &Bound<'_, PyModule>) -> \
PyResult<()>`, KHÔNG có tham số `_py: Python` riêng.
3. Chữ ký hàm bám theo bảng KIỂU QUAN SÁT ĐƯỢC mà prompt cung cấp; không tự \
đoán và không ép về một quy ước I/O cố định nào.
4. Chỉ dùng `std` của Rust và crate `pyo3`, không thêm crate ngoài.
5. Bỏ mọi lời gọi GUI (cv2.imshow, cv2.waitKey) nếu code gốc có.
6. Khi được báo lỗi biên dịch, đọc KỸ mã lỗi (`error[E0277]`, `error[E0599]`...) \
cùng đoạn code kèm theo, sửa ĐÚNG lỗi đó rồi trả lại TOÀN BỘ file, không trả \
patch từng phần."""

PROMPT_TEMPLATE = """**Nhiệm vụ**: Bạn là lập trình viên Rust/PyO3. Hãy dịch hàm Python hotspot \
`{function_name}` sang Rust để tăng tốc, giữ NGUYÊN hành vi số học của bản Python.

Hãy suy nghĩ theo Chain-of-Thought: đọc kỹ code và context xung quanh, xác định \
phần tốn thời gian nhất, rồi mới viết code Rust.

**Context của hotspot (lấy từ Program Call Graph + Program Structure Graph)**:
{context}

**Code hotspot cần dịch**:
```python
{hotspot_source}
```

**Ràng buộc bắt buộc**:
1. Viết hàm Rust expose qua PyO3 (`#[pyfunction]`), nhận ảnh xám dạng phẳng \
`Vec<u8>` (row-major) kèm `width`/`height`, trả về `PyResult<Vec<u8>>` -- \
khớp quy ước I/O đang dùng trong `versions/rust_pure/pyo3_ext/src/lib.rs`.
2. Chỉ dùng thư viện chuẩn của Rust (`std`), không thêm crate ngoài ngoài `pyo3`.
3. Bỏ hết lời gọi GUI (`cv2.imshow`, `cv2.waitKey`) nếu code gốc có -- benchmark \
chạy headless.
4. Giữ nguyên ý nghĩa thuật toán; nếu buộc phải đổi (vd vì kiểu dữ liệu), ghi rõ ở \
phần chiến lược.

**Định dạng trả lời BẮT BUỘC (đúng 2 mục, đúng thứ tự)**:
## Rust code
```rust
<toàn bộ code Rust ở đây>
```

## Optimization strategy
<giải thích ngắn gọn: đã tối ưu gì so với bản Python, vì sao>
"""


def build_prompt(function_name: str, hotspot_source: str, context_text: str) -> str:
    """Ghép prompt hoàn chỉnh (tách riêng để test/in ra xem mà không cần gọi LLM)."""
    return PROMPT_TEMPLATE.format(
        function_name=function_name,
        context=context_text or "(không có context láng giềng)",
        hotspot_source=hotspot_source or "(không lấy được source)",
    )


# ---------------------------------------------------------------------------
# PHA D -- prompt cho CHỮ KÝ BẤT KỲ (chế độ repo động).
#
# `PROMPT_TEMPLATE` ở trên ràng buộc cứng chữ ký vào use-case ảnh
# (`Vec<u8>` + width/height). Đúng cho 4 hàm viraj7 ở chế độ legacy, nhưng SAI
# cho repo RepoTransBench: ở đó hàm nhận list số, chuỗi, dict, hay đối tượng
# tự định nghĩa. Nên chế độ động dùng template riêng, và chữ ký được suy từ
# KIỂU QUAN SÁT ĐƯỢC trên đối số thật (Pha B) chứ không từ type-hint.
#
# Việc PHÂN TẦNG do CODE quyết định (deep_compare.classify_tier), không hỏi
# LLM: nếu để LLM tự chọn cách nhận đối số thì mỗi lần sinh ra một quy ước
# khác nhau, và shim Python không khớp được.
# ---------------------------------------------------------------------------
TIER1_PROMPT_TEMPLATE = """**Nhiệm vụ**: dịch hàm Python `{function_name}` sang Rust (PyO3) \
để tăng tốc, giữ NGUYÊN hành vi của bản Python.

**Chữ ký thật, đo được từ đối số mà bộ test của repo truyền vào** (đây là \
sự thật, KHÔNG phải type-hint -- hãy tin số liệu này hơn mọi annotation trong code):
{observed_types}
{io_examples}{graph_section}
**Code hotspot cần dịch**:
```python
{hotspot_source}
```

{pyo3_api}
**Ràng buộc bắt buộc**:
1. Viết MỘT hàm `#[pyfunction]` tên ĐÚNG là `{function_name}`, và MỘT \
`#[pymodule]` tên ĐÚNG là `{ext_module}` có đăng ký hàm đó.
2. Chữ ký Rust phải nhận ĐÚNG số đối số và ĐÚNG kiểu như bảng trên, theo ánh \
xạ: int -> i64, float -> f64, bool -> bool, str -> String, bytes -> Vec<u8>, \
list[float] -> Vec<f64>, list[int] -> Vec<i64>, dict[str -> int] -> \
std::collections::HashMap<String, i64>. Nếu cần chính đối tượng Python (để sửa \
tại chỗ, hoặc kiểu không nằm trong bảng) thì dùng `&Bound<'_, PyList>` / \
`&Bound<'_, PyDict>` / `&Bound<'_, PyAny>` -- TUYỆT ĐỐI không dùng `&PyList`, \
`&PyDict`, `&PyAny`.
3. Trả về `PyResult<T>` với T là kiểu tương ứng giá trị trả về của bản Python. \
Hàm Python trả `None` thì Rust trả `PyResult<()>`.
4. Nếu bản Python SỬA ĐỐI SỐ TẠI CHỖ (mutate list truyền vào), nhận \
`&Bound<'_, PyList>` và sửa trực tiếp trên đó -- đừng nhận `Vec<T>` vì `Vec` \
là bản copy, sửa nó thì bên Python không thấy gì.
5. Chỉ dùng `std` của Rust và crate `pyo3`, không thêm crate ngoài.

**Định dạng trả lời BẮT BUỘC (đúng 2 mục, đúng thứ tự)**:
## Rust code
```rust
<toàn bộ code Rust, gồm cả #[pymodule]>
```

## Optimization strategy
<giải thích ngắn: đã tối ưu gì so với bản Python, vì sao>
"""

TIER2_PROMPT_TEMPLATE = """**Nhiệm vụ**: tăng tốc hàm Python `{function_name}` bằng cách \
TÁCH KERNEL sang Rust (PyO3).

Hàm này nhận ĐỐI TƯỢNG tuỳ ý, nên KHÔNG dịch nguyên chữ ký sang Rust được. \
Cách làm: Rust chỉ nhận các TRƯỜNG kiểu gốc của đối tượng, còn một shim Python \
mỏng lo việc tháo đối tượng ra và đóng gói kết quả lại.

**Chữ ký thật, đo được từ đối số mà bộ test của repo truyền vào**:
{observed_types}
{io_examples}{graph_section}
**Code hotspot cần dịch**:
```python
{hotspot_source}
```

{pyo3_api}
**Ràng buộc bắt buộc**:
1. Viết kernel Rust `#[pyfunction]` tên `{function_name}_kernel`, chỉ nhận/trả \
KIỂU GỐC (số, chuỗi, Vec của số). Kèm `#[pymodule]` tên ĐÚNG là `{ext_module}`.
2. Viết shim Python tên ĐÚNG là `{function_name}`: tháo các thuộc tính cần \
thiết ra khỏi đối tượng, gọi kernel Rust, rồi dựng lại giá trị trả về ĐÚNG như \
bản Python gốc (cùng lớp, cùng thuộc tính).
3. Shim phải nhận ĐÚNG chữ ký của hàm Python gốc, để thay thế được trong suốt.
4. Chỉ dùng `std` + `pyo3` ở phía Rust; phía shim chỉ dùng stdlib Python.

**Định dạng trả lời BẮT BUỘC (đúng 3 mục, đúng thứ tự)**:
## Rust code
```rust
<kernel Rust, gồm cả #[pymodule]>
```

## Python shim
```python
<hàm shim Python>
```

## Optimization strategy
<giải thích ngắn: phần nào chuyển sang Rust, phần nào giữ ở Python, vì sao>
"""


def format_observed_types(
    arg_types: list[str], kwarg_types: dict[str, str], return_type: str = ""
) -> str:
    """Bảng kiểu quan sát được, dạng LLM đọc được.

    Đây là mảnh context QUAN TRỌNG NHẤT của Pha D: không có nó thì LLM phải
    đoán chữ ký, và chữ ký sai thì code Rust không bao giờ gọi được từ Python.
    """
    lines: list[str] = []
    for i, t in enumerate(arg_types or []):
        lines.append(f"  - đối số vị trí #{i}: {t}")
    for k, t in (kwarg_types or {}).items():
        lines.append(f"  - đối số từ khoá `{k}`: {t}")
    if return_type:
        lines.append(f"  - GIÁ TRỊ TRẢ VỀ: {return_type}")
    if not lines:
        return "  (không ghi được kiểu nào -- hàm có thể không nhận đối số)"
    return "\n".join(lines)


def format_io_examples(examples: list[dict], max_examples: int = 3) -> str:
    """Khối ví dụ VÀO/RA lấy từ lời gọi thật mà bộ test của repo tạo ra.

    CÓ Ở CẢ HAI NHÁNH ABLATION. Đây KHÔNG phải context graph: nó là phần đặc
    tả hành vi tối thiểu để viết được chữ ký PyO3 và biết hàm trả về cái gì.
    Bỏ nó đi thì nhánh `none` không còn là "cùng bài toán, thiếu context lân
    cận" mà thành "bài toán khác, thiếu cả đặc tả" -- phép so sánh sẽ vô nghĩa.
    """
    if not examples:
        return ""
    lines = ["", "**Ví dụ vào/ra THẬT (ghi lại từ bộ test của repo)**:"]
    for i, ex in enumerate(examples[:max_examples]):
        args = ex.get("args_repr") or []
        kwargs = ex.get("kwargs_repr") or {}
        call = ", ".join(list(args) + [f"{k}={v}" for k, v in kwargs.items()])
        lines.append(f"  {i + 1}. `{ex.get('function', '?')}({call})`")
        if ex.get("exception"):
            lines.append(f"     -> NÉM LỖI: {ex['exception']}")
        else:
            lines.append(f"     -> trả về: {ex.get('result_repr', '?')}")
        if ex.get("args_after_repr") and ex["args_after_repr"] != args:
            lines.append(
                f"     -> đối số SAU lời gọi: {ex['args_after_repr']} "
                "(hàm SỬA ĐỐI SỐ TẠI CHỖ -- bản Rust phải sửa được y như vậy)"
            )
    lines.append("")
    return "\n".join(lines)


def format_graph_section(context_text: str) -> str:
    """Khối context lân cận PCG/PSG -- ĐÂY là thứ duy nhất khác nhau giữa 2
    nhánh ablation (`graph` có, `none` không)."""
    return (
        "\n**Context lân cận của hotspot (từ Program Call Graph + Program "
        f"Structure Graph)**:\n{context_text}\n"
    )


def build_signature_prompt(
    function_name: str,
    hotspot_source: str,
    context_text: str,
    tier: str,
    observed_types: str,
    ext_module: str,
    io_examples: list[dict] | None = None,
    include_graph_context: bool = True,
) -> str:
    """Prompt cho chế độ động. MỘT hàm build duy nhất cho CẢ HAI nhánh ablation.

    `include_graph_context` là **cờ duy nhất** phân biệt hai nhánh:
        True  (nhánh "graph") -- có khối context lân cận PCG/PSG.
        False (nhánh "none")  -- BỎ HẲN khối đó, giữ nguyên mọi thứ còn lại.

    Cố ý dùng chung một hàm thay vì hai hàm/hai template: nếu tách ra thì chỉ
    cần một lần sửa lệch là hai nhánh khác nhau ở nhiều hơn một biến, và toàn
    bộ kết luận ablation mất giá trị.
    """
    from stage1_profiling.deep_compare import TIER_KERNEL

    template = TIER2_PROMPT_TEMPLATE if tier == TIER_KERNEL else TIER1_PROMPT_TEMPLATE
    return template.format(
        function_name=function_name,
        graph_section=(
            format_graph_section(context_text or "(không có context láng giềng)")
            if include_graph_context else ""
        ),
        io_examples=format_io_examples(io_examples or []),
        hotspot_source=hotspot_source or "(không lấy được source)",
        observed_types=observed_types,
        ext_module=ext_module,
        # Khối API PyO3 + khuôn mẫu đã đối chiếu. CÓ Ở CẢ HAI NHÁNH ablation --
        # nó không phải context graph, mà là điều kiện để code biên dịch được.
        # Thiếu nó thì `compiled = 0` ở mọi nhánh và ablation không so được gì.
        pyo3_api=pyo3_api_block(ext_module),
    )


def parse_python_shim(text: str) -> str:
    """Lấy khối `## Python shim` (chỉ Tầng 2 có). Rỗng nếu không có."""
    match = re.search(
        r"##\s*Python shim\s*\n+```(?:python)?\s*\n(.*?)```",
        text, flags=re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


def parse_response(text: str) -> dict[str, str]:
    """Tách code Rust + chiến lược tối ưu ra khỏi câu trả lời của model.

    Chịu được cả khi model không bám đúng template 100%: ưu tiên lấy khối
    ```rust ... ```; nếu không có thì lấy khối ``` ... ``` bất kỳ.
    """
    rust_code = ""
    match = re.search(r"```rust\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        rust_code = match.group(1).strip()
    else:
        generic = re.search(r"```\s*\n(.*?)```", text, flags=re.DOTALL)
        if generic:
            rust_code = generic.group(1).strip()
            logger.warning(
                "Response không có khối ```rust -- đã lấy tạm khối code chung "
                "đầu tiên, cần kiểm tra tay trước khi dùng."
            )

    strategy = ""
    strat = re.search(
        r"##\s*Optimization strategy\s*\n(.*?)(?:\n##\s|\Z)", text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if strat:
        strategy = strat.group(1).strip()

    return {"rust_code": rust_code, "strategy": strategy, "raw_response": text}


def write_generated_rust(
    function_name: str, rust_code: str, strategy: str = "", output_dir: Path | None = None
) -> Path:
    """Ghi code Rust sinh ra vào generated/<function_name>.rs.

    KHÔNG đụng tới src/lib.rs (code viết tay). File cũ cùng tên bị ghi đè --
    đây là bản nháp sinh lại được, không phải nguồn sự thật."""
    out_dir = Path(output_dir) if output_dir is not None else GENERATED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # Tên hàm đã đến từ AST nên an toàn, vẫn lọc lại cho chắc.
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", function_name) or "unnamed"
    out_path = out_dir / f"{safe_name}.rs"

    header = (
        f"// === BẢN NHÁP do Stage 4 (LLM) sinh tự động cho hàm `{function_name}` ===\n"
        "// KHÔNG phải code đã kiểm chứng. Đọc kỹ, đối chiếu hành vi với bản\n"
        "// Python, rồi mới merge TAY vào versions/rust_pure/pyo3_ext/src/lib.rs.\n"
        "// File này sinh lại được bất cứ lúc nào và đã bị .gitignore bỏ qua.\n"
    )
    if strategy:
        header += "//\n// Chiến lược tối ưu model đưa ra:\n"
        header += "".join(f"//   {line}\n" for line in strategy.splitlines())
    header += "\n"

    out_path.write_text(header + (rust_code or "// (model không trả về code Rust)\n"),
                        encoding="utf-8")
    logger.info("Đã ghi bản nháp Rust: %s (%d ký tự code)", out_path, len(rust_code or ""))
    return out_path


FIX_PROMPT_TEMPLATE = """Code Rust bạn vừa viết cho `{function_name}` KHÔNG BIÊN DỊCH ĐƯỢC.

**Loại lỗi**: {error_class}

**Output của `cargo check`**:
```
{compiler_output}
```

Sửa ĐÚNG các lỗi trên. Trả lại TOÀN BỘ file Rust hoàn chỉnh (không trả patch \
từng phần), theo đúng định dạng 2 mục như lần trước:

## Rust code
```rust
<toàn bộ code Rust đã sửa>
```

## Optimization strategy
<đã sửa gì so với bản trước>
"""


OPTIMIZE_PROMPT_TEMPLATE = """Code Rust của `{function_name}` đã chạy ĐÚNG, giờ cần NHANH HƠN.

Đây là vòng tối ưu thứ {round_index}.

**Chiến lược do khâu đánh giá đề xuất**:
{strategy}

Áp dụng chiến lược trên (hoặc hướng tốt hơn nếu bạn thấy rõ), giữ NGUYÊN \
hành vi số học. Trả lại TOÀN BỘ file Rust theo đúng định dạng 2 mục như trước:

## Rust code
```rust
<toàn bộ code Rust đã tối ưu>
```

## Optimization strategy
<đã đổi gì ở vòng này và vì sao nó nhanh hơn>
"""


class GeneratorAgent:
    """Agent SINH/SỬA code Rust, giữ lịch sử hội thoại RIÊNG.

    Vì dùng `AgentSession` riêng, khi Stage 5 báo lỗi biên dịch và yêu cầu
    sửa, agent vẫn NHỚ code nó vừa viết -- không phải gửi lại từ đầu. Đồng
    thời Decision Agent KHÔNG hề thấy dòng hội thoại này (xem docstring
    agent_session.py).
    """

    def __init__(
        self,
        backend,
        output_dir: Path | None = None,
        model: str | None = None,
        num_ctx: int | None = None,
        prompt_dir: Path | None = None,
        arm: str = "",
    ) -> None:
        from stage4_llm_transpile.agent_session import AgentSession

        self.backend = backend
        self.output_dir = output_dir
        # `model` = llm.<backend>.generator_model -- model CHUYÊN SINH CODE,
        # khác model của Decision Agent. `num_ctx` rộng hơn vì prompt chứa cả
        # code lẫn context từ Stage 3.
        self.model = model
        self.num_ctx = num_ctx
        # PHẦN 2.2: nơi lưu prompt đầy đủ để diff hai nhánh. `arm` chỉ dùng cho
        # tên file -- nó KHÔNG được đi vào nội dung prompt, nếu không hai nhánh
        # sẽ khác nhau ở nhiều hơn một biến.
        self.prompt_dir = Path(prompt_dir) if prompt_dir else None
        self.arm = arm or ""
        self.session = AgentSession(
            role_name="generator",
            system_prompt=GENERATOR_SYSTEM_PROMPT,
            backend=backend,
            model=model,
            num_ctx=num_ctx,
        )

    def _dump_prompt(self, function_name: str, prompt: str, label: str) -> Path | None:
        """Ghi prompt ĐẦY ĐỦ ra file. Trả None nếu chưa cấu hình `prompt_dir`.

        Tên file mang cả `arm` để `diff` hai nhánh là một lệnh:
            diff prompts/graph/<fn>.generate.txt prompts/none/<fn>.generate.txt
        """
        if self.prompt_dir is None:
            return None
        try:
            self.prompt_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", function_name) or "unnamed"
            suffix = f".{self.arm}" if self.arm else ""
            path = self.prompt_dir / f"{safe}{suffix}.{label}.txt"
            path.write_text(prompt, encoding="utf-8")
            return path
        except OSError as exc:
            logger.warning("Không ghi được prompt ra %s: %s", self.prompt_dir, exc)
            return None

    def _send_and_parse(
        self,
        function_name: str,
        prompt: str,
        temperature: float | None = None,
        seed: int | None = None,
        prompt_label: str = "generate",
    ) -> dict[str, Any]:
        """Gửi 1 lượt, parse code Rust, ghi file. Gom lỗi thành dict thay vì
        raise để caller (run_pipeline/loop_runner) chạy tiếp hotspot khác.

        Luôn LƯU PROMPT ĐẦY ĐỦ ra file trước khi gọi (PHẦN 2.2): hai nhánh
        ablation phải diff được với nhau, và nếu chỉ log độ dài thì không ai
        kiểm tra lại được rằng chúng chỉ khác đúng một khối.
        """
        import time as _time

        from stage4_llm_transpile.model_backend import ModelBackendError

        prompt_path = self._dump_prompt(function_name, prompt, prompt_label)

        t0 = _time.perf_counter()
        try:
            response = self.session.send(prompt, temperature=temperature, seed=seed)
        except ModelBackendError as exc:
            logger.error("Generator Agent: gọi LLM thất bại cho '%s': %s", function_name, exc)
            return {
                "ok": False, "function_name": function_name, "error": str(exc),
                "prompt_path": str(prompt_path) if prompt_path else None,
                "llm_seconds": _time.perf_counter() - t0,
            }
        llm_seconds = _time.perf_counter() - t0
        # Ước lượng tại chỗ khi backend không báo số token (backend tuỳ biến
        # của người dùng, hoặc mock trong test). Báo cáo độ dài prompt là yêu
        # cầu của ablation (PHẦN 2.5/2.6) nên không được phụ thuộc vào việc
        # backend có tự tính hay không.
        est_tokens = (
            self.session.last_prompt_tokens
            if self.session.last_prompt_tokens is not None
            else int(len(prompt) / 3.5)
        )
        # Phán định BỊ CẮT ở đây, không chỉ dựa vào backend: Ollama cắt prompt
        # âm thầm, và một backend tuỳ biến có thể không báo gì cả. So ước lượng
        # token với `num_ctx` của chính vai trò này là phép kiểm độc lập
        # backend, và nó là căn cứ để gắn CONFOUNDED (PHẦN 2.5).
        truncated = bool(self.session.last_truncated)
        if self.num_ctx and est_tokens > int(self.num_ctx):
            truncated = True
            logger.warning(
                "Prompt cho '%s' ước tính %d token > num_ctx=%s -> coi là BỊ CẮT. "
                "Hotspot này sẽ bị gắn CONFOUNDED và tách khỏi so sánh ablation.",
                function_name, est_tokens, self.num_ctx,
            )

        parsed = parse_response(response)
        if not parsed["rust_code"]:
            logger.error(
                "Generator Agent: response cho '%s' không chứa code Rust nào.", function_name,
            )
            return {"ok": False, "function_name": function_name,
                    "error": "response không chứa code Rust", "raw_response": response,
                    "prompt_path": str(prompt_path) if prompt_path else None,
                    "prompt_tokens": est_tokens,
                    "truncated": truncated,
                    "llm_seconds": llm_seconds}

        out_path = write_generated_rust(
            function_name, parsed["rust_code"], parsed["strategy"], self.output_dir
        )
        result = {
            "ok": True,
            "function_name": function_name,
            "output_path": str(out_path),
            "rust_code": parsed["rust_code"],
            "strategy": parsed["strategy"],
            "rust_code_chars": len(parsed["rust_code"]),
            # --- số liệu cho báo cáo ablation (PHẦN 2.5/2.6) ---
            "prompt_path": str(prompt_path) if prompt_path else None,
            "prompt_chars": len(prompt),
            "prompt_tokens": est_tokens,
            "truncated": truncated,
            "llm_seconds": llm_seconds,
            "seed": seed,
            "temperature": temperature,
        }
        # Tầng 2 (Pha D) còn cần shim Python đi kèm kernel Rust. Lượt sửa lỗi
        # biên dịch cũng phải giữ lại shim, nên parse ở đây chứ không chỉ ở
        # lượt sinh đầu tiên.
        shim = parse_python_shim(parsed.get("raw_response") or "")
        if shim:
            result["python_shim"] = shim
        return result

    def generate_rust(
        self,
        function_name: str,
        context: dict[str, Any],
        signature: dict[str, Any] | None = None,
        include_graph_context: bool = True,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Lượt ĐẦU: dịch hotspot sang Rust dựa trên context từ Stage 3.

        `signature` (Pha D, chỉ có ở chế độ repo động) gồm:
            tier               -- TIER1_NATIVE | TIER2_KERNEL (do code quyết)
            observed_arg_types, observed_kwarg_types, observed_return_type
            ext_module         -- tên `#[pymodule]` mà code Rust phải dùng
        Không truyền `signature` -> dùng prompt LEGACY (chữ ký ảnh Vec<u8> +
        width/height), giữ nguyên hành vi cũ cho 4 hàm viraj7.
        """
        from stage3_context_packaging.packager import format_context_for_prompt

        if not context.get("found"):
            return {"ok": False, "function_name": function_name,
                    "error": "không có context (hàm không nằm trong graph)"}

        occurrences = context.get("occurrences") or []
        hotspot_source = occurrences[0]["hotspot"]["source"] if occurrences else ""
        context_text = format_context_for_prompt(context)

        if signature:
            prompt = build_signature_prompt(
                function_name=function_name,
                hotspot_source=hotspot_source,
                context_text=context_text,
                tier=signature.get("tier", ""),
                observed_types=format_observed_types(
                    signature.get("observed_arg_types") or [],
                    signature.get("observed_kwarg_types") or {},
                    signature.get("observed_return_type", ""),
                ),
                ext_module=signature.get("ext_module") or f"{function_name}_ext",
                io_examples=signature.get("io_examples") or [],
                include_graph_context=include_graph_context,
            )
            logger.info(
                "Generator Agent: dịch '%s' theo chữ ký THẬT (tier=%s, %d đối số, "
                "arm=%s, context_graph=%s, seed=%s) qua backend %s (prompt %d ký tự)...",
                function_name, signature.get("tier", "?"),
                len(signature.get("observed_arg_types") or []),
                self.arm or "(không ablation)", include_graph_context, seed,
                getattr(self.backend, "name", "?"), len(prompt),
            )
        else:
            prompt = build_prompt(function_name, hotspot_source, context_text)
            logger.info(
                "Generator Agent: dịch hotspot '%s' qua backend %s (prompt %d ký tự)...",
                function_name, getattr(self.backend, "name", "?"), len(prompt),
            )
        return self._send_and_parse(
            function_name, prompt, temperature=temperature, seed=seed,
            prompt_label="generate",
        )

    def optimize_further(
        self, function_name: str, strategy: str | None, round_index: int,
        temperature: float | None = None, seed: int | None = None,
    ) -> dict[str, Any]:
        """Lượt TỐI ƯU THÊM cho vòng lặp Stage 6 (optimization_loop).

        Khác `fix_compile_error`: ở đây code đã chạy đúng rồi, chỉ cần NHANH
        HƠN, theo chiến lược mà Decision Agent vừa đề xuất. Vẫn là việc của
        Generator Agent (viết code), không phải Decision Agent (đánh giá).
        """
        prompt = OPTIMIZE_PROMPT_TEMPLATE.format(
            function_name=function_name,
            round_index=round_index,
            strategy=strategy or "(Decision Agent không nêu chiến lược cụ thể -- "
                                "tự chọn hướng tối ưu bạn thấy hợp lý nhất)",
        )
        logger.info(
            "Generator Agent: tối ưu thêm '%s' (vòng %d), chiến lược: %s",
            function_name, round_index, (strategy or "(tự chọn)")[:80],
        )
        return self._send_and_parse(
            function_name, prompt, temperature=temperature, seed=seed,
            prompt_label=f"optimize_r{round_index}",
        )

    def fix_compile_error(
        self, function_name: str, compiler_output: str, error_class: str = "OTHER",
        temperature: float | None = None, seed: int | None = None,
    ) -> dict[str, Any]:
        """Lượt SỬA LỖI cho Stage 5 (compiler-in-the-loop).

        Dùng CÙNG session với lượt sinh code, nên agent nhớ code nó vừa viết.
        ĐÂY LÀ VIỆC CỦA GENERATOR AGENT, không phải Decision Agent --
        Decision Agent chỉ vào cuộc sau khi đã compile xong VÀ benchmark xong.
        """
        prompt = FIX_PROMPT_TEMPLATE.format(
            function_name=function_name,
            error_class=error_class,
            compiler_output=(compiler_output or "")[:4000],
        )
        logger.info(
            "Generator Agent: yêu cầu sửa lỗi biên dịch '%s' cho '%s'...",
            error_class, function_name,
        )
        self._n_fix_calls = getattr(self, "_n_fix_calls", 0) + 1
        return self._send_and_parse(
            function_name, prompt, temperature=temperature, seed=seed,
            prompt_label=f"fix{self._n_fix_calls}",
        )


def generate_rust_for(
    function_name: str,
    context: dict[str, Any],
    backend,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Tiện ích 1-lượt (giữ cho code cũ gọi được): tạo 1 GeneratorAgent dùng
    một lần rồi dịch. Nếu cần vòng lặp sửa lỗi biên dịch của Stage 5, hãy tự
    tạo `GeneratorAgent` và giữ nó để session không bị mất."""
    agent = GeneratorAgent(backend, output_dir)
    return agent.generate_rust(function_name, context)
