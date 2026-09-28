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
GENERATOR_SYSTEM_PROMPT = """Bạn là lập trình viên Rust/PyO3 chuyên dịch code Python \
hiệu năng cao sang Rust.

VAI TRÒ DUY NHẤT của bạn: SINH và SỬA code Rust.
- Bạn KHÔNG đánh giá xem kết quả benchmark có tốt hay không.
- Bạn KHÔNG quyết định chấp nhận hay loại bỏ một bản dịch.
- Việc đó thuộc về một agent khác, độc lập với bạn.

Nguyên tắc khi viết code:
1. Giữ NGUYÊN hành vi số học của bản Python gốc.
2. Hàm expose qua PyO3 bằng `#[pyfunction]`, nhận ảnh xám phẳng `Vec<u8>` \
(row-major) kèm `width`/`height`, trả về `PyResult<Vec<u8>>`.
3. Chỉ dùng `std` của Rust và crate `pyo3`, không thêm crate ngoài.
4. Bỏ mọi lời gọi GUI (cv2.imshow, cv2.waitKey) nếu code gốc có.
5. Khi được báo lỗi biên dịch, sửa ĐÚNG lỗi đó và trả lại TOÀN BỘ file, \
không trả patch từng phần."""

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
    ) -> None:
        from stage4_llm_transpile.agent_session import AgentSession

        self.backend = backend
        self.output_dir = output_dir
        # `model` = llm.<backend>.generator_model -- model CHUYÊN SINH CODE,
        # khác model của Decision Agent. `num_ctx` rộng hơn vì prompt chứa cả
        # code lẫn context từ Stage 3.
        self.model = model
        self.num_ctx = num_ctx
        self.session = AgentSession(
            role_name="generator",
            system_prompt=GENERATOR_SYSTEM_PROMPT,
            backend=backend,
            model=model,
            num_ctx=num_ctx,
        )

    def _send_and_parse(self, function_name: str, prompt: str) -> dict[str, Any]:
        """Gửi 1 lượt, parse code Rust, ghi file. Gom lỗi thành dict thay vì
        raise để caller (run_pipeline/loop_runner) chạy tiếp hotspot khác."""
        from stage4_llm_transpile.model_backend import ModelBackendError

        try:
            response = self.session.send(prompt)
        except ModelBackendError as exc:
            logger.error("Generator Agent: gọi LLM thất bại cho '%s': %s", function_name, exc)
            return {"ok": False, "function_name": function_name, "error": str(exc)}

        parsed = parse_response(response)
        if not parsed["rust_code"]:
            logger.error(
                "Generator Agent: response cho '%s' không chứa code Rust nào.", function_name,
            )
            return {"ok": False, "function_name": function_name,
                    "error": "response không chứa code Rust", "raw_response": response}

        out_path = write_generated_rust(
            function_name, parsed["rust_code"], parsed["strategy"], self.output_dir
        )
        return {
            "ok": True,
            "function_name": function_name,
            "output_path": str(out_path),
            "rust_code": parsed["rust_code"],
            "strategy": parsed["strategy"],
            "rust_code_chars": len(parsed["rust_code"]),
        }

    def generate_rust(self, function_name: str, context: dict[str, Any]) -> dict[str, Any]:
        """Lượt ĐẦU: dịch hotspot sang Rust dựa trên context từ Stage 3."""
        from stage3_context_packaging.packager import format_context_for_prompt

        if not context.get("found"):
            return {"ok": False, "function_name": function_name,
                    "error": "không có context (hàm không nằm trong graph)"}

        occurrences = context.get("occurrences") or []
        hotspot_source = occurrences[0]["hotspot"]["source"] if occurrences else ""
        prompt = build_prompt(function_name, hotspot_source, format_context_for_prompt(context))

        logger.info(
            "Generator Agent: dịch hotspot '%s' qua backend %s (prompt %d ký tự)...",
            function_name, getattr(self.backend, "name", "?"), len(prompt),
        )
        return self._send_and_parse(function_name, prompt)

    def optimize_further(
        self, function_name: str, strategy: str | None, round_index: int
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
        return self._send_and_parse(function_name, prompt)

    def fix_compile_error(
        self, function_name: str, compiler_output: str, error_class: str = "OTHER"
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
        return self._send_and_parse(function_name, prompt)


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
