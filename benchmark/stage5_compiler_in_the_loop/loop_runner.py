"""Stage 5 -- Vòng lặp compiler-in-the-loop + số liệu Pass@1 / DSR@1.

Luồng cho MỖI hotspot đã được Stage 4 dịch sang Rust:

    cargo check  --(lỗi)-->  Generator Agent sửa  -->  cargo check  --> ...
         |                        (tối đa max_retries vòng)
      (thành công)
         v
    đánh dấu compile OK -> Stage 6 benchmark

PHÂN VAI (nhấn mạnh, vì rất dễ làm sai): vòng lặp sửa lỗi này CHỈ gọi
GENERATOR AGENT -- vai trò sinh/sửa code. DECISION AGENT tuyệt đối KHÔNG
tham gia ở đây; nó chỉ chạy ở Stage 6 sau khi đã compile được VÀ đã đo tốc
độ, để quyết định accept/reject. Xem thêm docstring của agent_session.py.

SỐ LIỆU:
  Pass@1 = tỉ lệ hotspot biên dịch được NGAY LẦN ĐẦU, không cần sửa lần nào.
  DSR@1  = Debug Success Rate: trong số hotspot LỖI ở lần đầu, bao nhiêu %
           cuối cùng sửa được trong giới hạn max_retries.
  (Hai chỉ số này bổ sung nhau: Pass@1 đo chất lượng sinh code lần đầu,
   DSR@1 đo khả năng tự sửa lỗi khi có phản hồi từ compiler.)
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from stage5_compiler_in_the_loop.compiler_loop import (
    DEFAULT_CARGO_TIMEOUT_SEC,
    CompileResult,
    compile_and_classify,
)

logger = logging.getLogger("benchmark.stage5_compiler_in_the_loop.loop_runner")

MAX_COMPILE_RETRIES = 3


@dataclass
class HotspotCompileOutcome:
    """Kết quả Stage 5 cho ĐÚNG 1 hotspot."""

    function_name: str
    compiled: bool = False
    passed_first_try: bool = False
    attempts: int = 0                  # số lần cargo check đã chạy
    fix_rounds: int = 0                # số lần gọi Generator Agent để sửa
    error_classes: list[str] = field(default_factory=list)  # 1 phần tử / vòng LỖI
    # LẦN CHẠY CHẨN ĐOÁN: vòng (0-based, 0 = lần thử ban đầu TRƯỚC khi sửa lần
    # nào) mà hotspot này biên dịch được, hoặc None nếu không bao giờ biên
    # dịch được trong giới hạn max_retries (hoặc bị skip). Tách riêng khỏi
    # `passed_first_try`/Pass@1 (luôn tính từ vòng 0, KHÔNG đổi khi max_retries
    # đổi) -- trường này để đọc lại được DSR@N cho từng N <= max_retries.
    compiled_at_round: int | None = None
    skipped: bool = False
    skip_reason: str = ""
    final_error: str = ""
    final_code: str = ""
    """Code Rust ĐÃ BIÊN DỊCH ĐƯỢC (rỗng nếu không bao giờ biên dịch được hoặc
    bị skip) -- dùng cho phân tích CHẾ ĐỘ BÓNG (mục B1: dò gõ cứng tĩnh)."""


def _write_candidate_to_crate(rust_code: str, crate_dir: Path) -> Path:
    """Ghi code Rust cần kiểm tra vào crate NHÁP để `cargo check`.

    Ghi vào crate tạm riêng, KHÔNG đụng
    versions/rust_pure/pyo3_ext/src/lib.rs (code người dùng tự viết tay).
    """
    src_dir = crate_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    lib_rs = src_dir / "lib.rs"
    lib_rs.write_text(rust_code, encoding="utf-8")
    return lib_rs


def prepare_scratch_crate(template_crate: Path, scratch_dir: Path) -> Path:
    """Tạo crate nháp (copy Cargo.toml từ crate thật) để biên dịch thử code
    LLM sinh ra mà không làm bẩn crate gốc."""
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    src_toml = Path(template_crate) / "Cargo.toml"
    if src_toml.exists():
        shutil.copy2(src_toml, scratch_dir / "Cargo.toml")
    (scratch_dir / "src").mkdir(exist_ok=True)
    return scratch_dir


def run_compile_loop_for(
    function_name: str,
    rust_code: str,
    generator_agent: Any,
    crate_dir: Path,
    max_retries: int = MAX_COMPILE_RETRIES,
    cargo_timeout_sec: int = DEFAULT_CARGO_TIMEOUT_SEC,
) -> HotspotCompileOutcome:
    """Biên dịch `rust_code`; nếu lỗi thì nhờ `generator_agent` sửa, lặp tối
    đa `max_retries` lần.

    generator_agent: instance `GeneratorAgent` (Stage 4) -- PHẢI là agent đã
        sinh ra code này, để nó còn nhớ ngữ cảnh trong session riêng của nó.

    KHÔNG raise: mọi lỗi (thiếu cargo, LLM hỏng) đều gói vào outcome.
    """
    outcome = HotspotCompileOutcome(function_name=function_name)
    current_code = rust_code

    for attempt in range(max_retries + 1):  # lần 0 = thử ban đầu
        _write_candidate_to_crate(current_code, crate_dir)
        result: CompileResult = compile_and_classify(crate_dir, cargo_timeout_sec)
        outcome.attempts += 1

        if result.skipped:
            outcome.skipped = True
            outcome.skip_reason = result.skip_reason
            logger.warning("Stage 5 [%s]: %s", function_name, result.skip_reason)
            return outcome

        if result.ok:
            outcome.compiled = True
            outcome.compiled_at_round = attempt
            outcome.final_code = current_code
            outcome.passed_first_try = attempt == 0
            logger.info(
                "Stage 5 [%s]: biên dịch OK sau %d lần thử (%d lần sửa).",
                function_name, outcome.attempts, outcome.fix_rounds,
            )
            return outcome

        outcome.error_classes.append(result.error_class)
        outcome.final_error = result.output

        if attempt >= max_retries:
            logger.error(
                "Stage 5 [%s]: hết %d lần sửa mà vẫn không biên dịch được "
                "(lỗi cuối: %s).", function_name, max_retries, result.error_class,
            )
            return outcome

        # --- Gọi GENERATOR AGENT (không phải Decision Agent) để sửa ---
        fix = generator_agent.fix_compile_error(
            function_name=function_name,
            compiler_output=result.output,
            error_class=result.error_class,
        )
        outcome.fix_rounds += 1
        if not fix.get("ok"):
            outcome.final_error = str(fix.get("error", "Generator Agent không sửa được"))
            logger.error(
                "Stage 5 [%s]: Generator Agent không trả về code sửa (%s) -- dừng.",
                function_name, outcome.final_error,
            )
            return outcome
        current_code = fix.get("rust_code") or current_code

    return outcome


def compute_metrics(outcomes: list[HotspotCompileOutcome]) -> dict[str, Any]:
    """Pass@1 và DSR@1 từ danh sách outcome. Bỏ qua hotspot bị skip (không
    có cargo) vì chúng không cho kết luận nào về chất lượng code."""
    evaluated = [o for o in outcomes if not o.skipped]
    skipped = [o for o in outcomes if o.skipped]

    n = len(evaluated)
    passed_first = [o for o in evaluated if o.passed_first_try]
    failed_first = [o for o in evaluated if not o.passed_first_try]
    debugged_ok = [o for o in failed_first if o.compiled]

    # Bảng vòng sửa (mục E4 báo cáo chẩn đoán): {vòng (str) hoặc "null": số hàm
    # biên dịch được ĐÚNG ở vòng đó}. "null" = không bao giờ biên dịch được
    # trong giới hạn max_retries (không tính hotspot bị skip -- không cho kết
    # luận gì về chất lượng code).
    round_histogram: dict[str, int] = {}
    for o in evaluated:
        key = "null" if o.compiled_at_round is None else str(o.compiled_at_round)
        round_histogram[key] = round_histogram.get(key, 0) + 1

    metrics: dict[str, Any] = {
        "n_total": len(outcomes),
        "n_evaluated": n,
        "n_skipped": len(skipped),
        "n_passed_first_try": len(passed_first),
        "n_compiled_eventually": len([o for o in evaluated if o.compiled]),
        "pass_at_1": (len(passed_first) / n) if n else None,
        "dsr_at_1": (len(debugged_ok) / len(failed_first)) if failed_first else None,
        "compiled_at_round_histogram": round_histogram,
    }
    if skipped:
        metrics["skip_reason"] = skipped[0].skip_reason
    return metrics


def run_stage5(
    transpile_results: list[dict[str, Any]],
    generator_agents: dict[str, Any],
    benchmark_root: Path,
    max_retries: int = MAX_COMPILE_RETRIES,
    cargo_timeout_sec: int = DEFAULT_CARGO_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Chạy Stage 5 cho TẤT CẢ hotspot mà Stage 4 dịch thành công.

    transpile_results: list dict trả về từ Stage 4 (generator_agent).
    generator_agents:  {tên hàm: GeneratorAgent đã dịch hàm đó} -- cần đúng
        agent cũ để giữ ngữ cảnh session khi sửa lỗi.
    """
    template_crate = benchmark_root / "versions" / "rust_pure" / "pyo3_ext"
    scratch = prepare_scratch_crate(
        template_crate, benchmark_root / "results" / ".stage5_scratch_crate"
    )

    outcomes: list[HotspotCompileOutcome] = []
    for item in transpile_results:
        if not item.get("ok"):
            continue
        name = item["function_name"]
        code = item.get("rust_code") or ""
        if not code:
            # Bản nháp đã ghi ra file nhưng không giữ code trong dict.
            path = item.get("output_path")
            if path and Path(path).exists():
                code = Path(path).read_text(encoding="utf-8")
        agent = generator_agents.get(name)
        if agent is None:
            logger.warning(
                "Stage 5 [%s]: không có GeneratorAgent tương ứng -- bỏ qua "
                "(không thể sửa lỗi mà giữ đúng ngữ cảnh).", name,
            )
            continue
        outcomes.append(
            run_compile_loop_for(
                function_name=name, rust_code=code, generator_agent=agent,
                crate_dir=scratch, max_retries=max_retries,
                cargo_timeout_sec=cargo_timeout_sec,
            )
        )

    metrics = compute_metrics(outcomes)
    logger.info("Stage 5 tổng kết: %s", metrics)
    return {
        "metrics": metrics,
        "outcomes": [asdict(o) for o in outcomes],
        "scratch_crate": str(scratch),
    }
