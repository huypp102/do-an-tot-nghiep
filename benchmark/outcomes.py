"""PHA 0 -- Chấm dứt thất bại im lặng.

Trước khi có file này, một repo mà pipeline KHÔNG đo được gì vẫn kết thúc với
`summary["ok"] = True` và exit code 0, còn từng hotspot thì chỉ để lại một
dòng log warning rồi biến mất khỏi bảng kết quả. Chạy cả dataset ra bảng toàn
`n/a` mà vẫn tự nhận là thành công.

Hai enum dưới đây là hợp đồng chung cho toàn pipeline:

    RepoStatus     -- kết cục của MỘT repo (một lần gọi `run_once`).
    HotspotReason  -- lý do CUỐI CÙNG của MỘT hotspot. Bắt buộc, đúng 1 giá
                      trị. Không có hotspot nào được rời pipeline mà thiếu nó.

QUY TẮC (được `run_pipeline.py` thi hành, xem `decide_repo_status`):
  * `summary["ok"] = True` CHỈ khi có ≥ 1 hotspot đạt `MEASURED`, nghĩa là
    vừa có kết quả correctness vừa có speedup.
  * Không hotspot nào đo được -> ghi lý do của TỪNG hotspot vào output và
    `run_pipeline` thoát với exit code khác 0.

Vì sao là chuỗi hằng thay vì `enum.Enum`: các giá trị này được ghi thẳng vào
JSON kết quả và đọc lại bởi script phân tích/luận văn, nên giữ dạng chuỗi
phẳng để không phải serialize/deserialize thêm một lớp.
"""
from __future__ import annotations

# ---------------------------------------------------------------- RepoStatus
OK = "OK"
"""Mọi hotspot đo được, và bộ test repo (nếu chạy) không tệ hơn baseline."""

PARTIAL = "PARTIAL"
"""Có ít nhất 1 hotspot MEASURED, nhưng vài hotspot khác bị loại vì lý do
riêng. Vẫn dùng được cho so sánh, chỉ cần ghi rõ phần bị loại."""

NO_MEASURABLE_HOTSPOT = "NO_MEASURABLE_HOTSPOT"
"""Không hotspot nào đo được VÀ chưa hotspot nào tới được bước sinh code.
Nghĩa là repo rụng ở khâu trước LLM: không phát lại được đối số, không thuộc
tầng hỗ trợ, hoặc Decision Gate gạt hết."""

ALL_HOTSPOTS_FAILED_COMPILE = "ALL_HOTSPOTS_FAILED_COMPILE"
"""Đã sinh được code Rust cho >= 1 hotspot nhưng KHÔNG cái nào biên dịch được.

TÁCH RIÊNG khỏi `NO_MEASURABLE_HOTSPOT` vì hai nguyên nhân khác hẳn nhau và
cách sửa cũng khác hẳn: cái trên là vấn đề của khâu ghi/phát lại đối số hoặc
phân tầng kiểu, còn cái này là vấn đề của PROMPT hoặc MODEL sinh code.

Lượt chạy thật đầu tiên (9 repo) cho `generated` = 1..5 nhưng `compiled` = 0 ở
CẢ 9 repo -- gộp chung một nhãn thì bảng kết quả trông như "không tìm được
hotspot nào", trong khi nguyên nhân thật là prompt dạy model viết theo API
PyO3 cũ (&PyList/&PyModule) còn crate ghim pyo3 0.22 (cần Bound<'_, T>)."""

BASELINE_FAILED = "BASELINE_FAILED"
"""Bộ test Python NGUYÊN BẢN của repo đã fail (hoặc không chạy được). Repo bị
loại khỏi so sánh và lỗi này TUYỆT ĐỐI không được tính cho bản hybrid."""

INSTALL_FAILED = "INSTALL_FAILED"
"""Không dựng được môi trường cho repo (venv/requirements/`pip install -e .`)."""

TIMEOUT = "TIMEOUT"
"""Vượt `repo_time_budget_sec` hoặc `test_timeout_sec`."""

REPO_STATUSES = (
    OK, PARTIAL, NO_MEASURABLE_HOTSPOT, ALL_HOTSPOTS_FAILED_COMPILE,
    BASELINE_FAILED, INSTALL_FAILED, TIMEOUT,
)

# ------------------------------------------------------------- HotspotReason
MEASURED = "MEASURED"
"""Đích đến duy nhất được coi là thành công: có correctness VÀ có speedup."""

UNSUPPORTED_KIND = "UNSUPPORTED_KIND"
"""Loại hàm hoặc kiểu đối số nằm ngoài khả năng xử lý: generator, async, hoặc
kiểu không thuộc Tầng 1/Tầng 2 của Pha D."""

UNREPLAYABLE_ARGS = "UNREPLAYABLE_ARGS"
"""Đối số thật không pickle được (vd `sqlite3.Connection`, socket, file
handle) nên không phát lại được để so sánh."""

NOT_COVERED_BY_TESTS = "NOT_COVERED_BY_TESTS"
"""Bộ test của repo không gọi tới hàm này lần nào -> không có đối số thật."""

NONDETERMINISTIC = "NONDETERMINISTIC"
"""Phát lại 2 lần trên chính bản Python cho ra kết quả khác nhau (thời gian,
random, thứ tự dict/set...) -> không dùng làm chuẩn so sánh được."""

UNRESOLVABLE_IMPORT = "UNRESOLVABLE_IMPORT"
"""Không đổi được đường dẫn file thành `module:qualname` import được."""

COMPILE_FAILED = "COMPILE_FAILED"
"""Stage 5 hết số vòng sửa mà code Rust vẫn không biên dịch được."""

CORRECTNESS_FAILED = "CORRECTNESS_FAILED"
"""Biên dịch được, chạy được, nhưng output lệch so với bản Python."""

NO_IMPLEMENTATION = "NO_IMPLEMENTATION"
"""Không tra được hàm trong registry (kể cả registry động của Pha C). Thay
cho việc bỏ qua im lặng trước đây."""

GATE_SKIPPED = "GATE_SKIPPED"
"""Decision Gate (Stage 2) gán nhãn skip/vectorize -> cố ý không dịch."""

EXCLUDED_BY_TOP_K = "EXCLUDED_BY_TOP_K"
"""Hợp lệ, QUA được Decision Gate, nhưng xếp hạng FuncRank NGOÀI hạn mức
`top_k_translate` -- khác GATE_SKIPPED (đó là Gate CHỦ ĐỘNG loại, đây là hạn
mức số lượng cắt SAU Gate). Tách riêng để bảng ngưỡng (mục E báo cáo chẩn
đoán) đếm được CHÍNH XÁC bao nhiêu hàm bị cắt vì hạn mức, không lẫn với các
lý do Gate khác."""

LLM_FAILED = "LLM_FAILED"
"""Generator Agent không sinh được code Rust (backend lỗi, response rỗng...)."""

BUILD_FAILED = "BUILD_FAILED"
"""Biên dịch được bằng `cargo check` nhưng `maturin develop` thất bại, hoặc
không có toolchain Rust trên máy."""

MEASURE_FAILED = "MEASURE_FAILED"
"""Build xong nhưng phép đo tốc độ không cho ra số liệu."""

HOTSPOT_REASONS = (
    MEASURED, UNSUPPORTED_KIND, UNREPLAYABLE_ARGS, NOT_COVERED_BY_TESTS,
    NONDETERMINISTIC, UNRESOLVABLE_IMPORT, COMPILE_FAILED, CORRECTNESS_FAILED,
    NO_IMPLEMENTATION, GATE_SKIPPED, EXCLUDED_BY_TOP_K, LLM_FAILED,
    BUILD_FAILED, MEASURE_FAILED,
)

# Giải thích ngắn cho từng lý do -- in ra bảng kết quả và đưa vào báo cáo để
# người đọc không phải tra ngược vào source.
REASON_HELP: dict[str, str] = {
    MEASURED: "đo được đầy đủ (có correctness + có speedup)",
    UNSUPPORTED_KIND: "loại hàm/kiểu đối số ngoài khả năng xử lý (generator, async, kiểu lạ)",
    UNREPLAYABLE_ARGS: "đối số thật không pickle được nên không phát lại được",
    NOT_COVERED_BY_TESTS: "bộ test của repo không gọi hàm này lần nào",
    NONDETERMINISTIC: "phát lại 2 lần trên bản Python ra kết quả khác nhau",
    UNRESOLVABLE_IMPORT: "không đổi được file thành module:qualname import được",
    COMPILE_FAILED: "hết số vòng sửa mà Rust vẫn không biên dịch được",
    CORRECTNESS_FAILED: "output Rust lệch so với bản Python",
    NO_IMPLEMENTATION: "không tra được hàm trong registry",
    GATE_SKIPPED: "Decision Gate gán nhãn skip/vectorize -- cố ý không dịch",
    EXCLUDED_BY_TOP_K: "qua được Gate nhưng ngoài hạn mức top_k_translate",
    LLM_FAILED: "Generator Agent không sinh được code Rust",
    BUILD_FAILED: "cargo check OK nhưng maturin develop thất bại / thiếu toolchain",
    MEASURE_FAILED: "build xong nhưng không đo ra số liệu",
}


def validate_reason(reason: str) -> str:
    """Chặn lỗi chính tả enum ngay tại chỗ gán.

    Cố ý KHÔNG raise: một lý do sai chính tả không đáng làm sập cả lượt chạy
    dataset. Trả về `NO_IMPLEMENTATION` kèm log để lỗi vẫn lộ ra chứ không
    biến thành `None` âm thầm -- đúng tinh thần Pha 0.
    """
    if reason in HOTSPOT_REASONS:
        return reason
    import logging

    logging.getLogger("benchmark.outcomes").error(
        "Lý do hotspot không hợp lệ: %r (hợp lệ: %s) -- ghi nhận là %s.",
        reason, ", ".join(HOTSPOT_REASONS), NO_IMPLEMENTATION,
    )
    return NO_IMPLEMENTATION


def decide_repo_status(
    hotspot_reasons: list[str],
    baseline_failed: bool = False,
    install_failed: bool = False,
    timed_out: bool = False,
    n_generated: int | None = None,
    n_compiled: int | None = None,
) -> str:
    """Suy ra `RepoStatus` từ lý do của từng hotspot + các cờ mức repo.

    Thứ tự ưu tiên có chủ ý: lỗi mức MÔI TRƯỜNG (install/timeout/baseline)
    phải thắng, vì khi môi trường sai thì lý do của từng hotspot không đáng
    tin. Đặc biệt `BASELINE_FAILED` phải được giữ nguyên để tầng trên biết mà
    LOẠI repo khỏi so sánh, không tính lỗi đó cho hybrid.

    `n_generated`/`n_compiled` đến từ phễu (funnel.counts). Khi đã sinh được
    code mà không cái nào biên dịch được, trả `ALL_HOTSPOTS_FAILED_COMPILE`
    thay vì `NO_MEASURABLE_HOTSPOT` -- hai nguyên nhân khác nhau thì phải có
    hai nhãn khác nhau, nếu không bảng kết quả sẽ chỉ sai nguyên nhân chứ
    không sai con số, mà đó là kiểu sai khó phát hiện nhất.
    """
    if install_failed:
        return INSTALL_FAILED
    if timed_out:
        return TIMEOUT
    if baseline_failed:
        return BASELINE_FAILED

    n_measured = sum(1 for r in hotspot_reasons if r == MEASURED)
    if n_measured == 0:
        if (n_generated or 0) > 0 and (n_compiled or 0) == 0:
            return ALL_HOTSPOTS_FAILED_COMPILE
        return NO_MEASURABLE_HOTSPOT
    if n_measured == len(hotspot_reasons):
        return OK
    return PARTIAL


def is_success(repo_status: str) -> bool:
    """Repo này có đóng góp số liệu dùng được không.

    `run_pipeline.py` dùng đúng hàm này để đặt `summary["ok"]`, nên định
    nghĩa "thành công" chỉ nằm ở MỘT chỗ.
    """
    return repo_status in (OK, PARTIAL)
