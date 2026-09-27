"""Profiling ĐỘNG cho PCG, theo phương pháp Runtime Local Hotspot Detection
của paper POLO (Bai et al., "POLO: An LLM-Powered Project-Level Code
Performance Optimization Framework", IJCAI-25, Section 3.1) -- thay
Callgrind (chỉ chạy tốt trên Linux) bằng Scalene (đã chốt dùng cho Stage 1
của hệ thống chính, đồng bộ công cụ, chạy được trên Windows).

KHÔNG dựng lại FSM (finite state machine) phức tạp như POLO làm để parse
output Callgrind. Vì đang wrap trực tiếp ở mức Python (không phải parse
binary instrumentation output), dùng cách đơn giản hơn nhiều: decorator wrap
từng hàm target, ghi lại caller (qua stack lời gọi tự quản lý, không cần
`inspect.stack()` -- xem _DynamicCallTracker) + callee + elapsed time mỗi
lần gọi.

NGUỒN DỮ LIỆU:
  1. NGUỒN CHÍNH (count + self/cumulative time mỗi hàm, mỗi cạnh
     caller->callee): đo TRỰC TIẾP bằng _DynamicCallTracker, dùng
     time.perf_counter() qua workload thật (ảnh mẫu thật, lặp N lần theo
     config `graph.dynamic.workload_iterations`). KHÔNG phụ thuộc
     Scalene/cProfile -- luôn hoạt động, kể cả hàm quá nhanh để 1 sampling
     profiler như Scalene bắt được mẫu (ĐÃ KIỂM CHỨNG THỰC TẾ: với các hàm
     dummy stub gần như tức thời của scaffold này, Scalene chạy thành công
     nhưng KHÔNG cho ra entry function nào cho chúng -- đúng lý do Scalene
     chỉ nên dùng bổ sung, không phải nguồn chính).
  2. BỔ SUNG (% Python-only vs native/C-extension mỗi hàm): chạy
     `scalene run --profile-all --cpu-only --json` trên 1 script driver gọi
     lại đúng các hàm đó, parse JSON, khớp theo tên hàm
     (`files[*].functions[*].line` = tên hàm, `n_cpu_percent_python` /
     `n_cpu_percent_c`). Nếu Scalene không có entry cho 1 hàm (thường gặp
     với hàm quá nhanh) -- bỏ qua phần bổ sung CHO HÀM ĐÓ, không ảnh hưởng
     nguồn chính.
  3. Nếu module `scalene` KHÔNG import được (chưa cài, hoặc lỗi tương thích
     Windows với sampling profiler mức thấp ở 1 số bản) -- fallback dùng
     `cProfile` (có sẵn trong Python, không cần cài) để suy ra tỉ lệ
     python%/native% XẤP XỈ, dựa trên self-time (`inlinetime`) của chính hàm
     (Python thuần) so với thời gian trong các lời gọi built-in/C TRỰC TIẾP
     bên trong nó (`calls` có `code` là string). Đây là bản THAY THẾ TẠM cho
     Scalene -- Scalene là công cụ đã chốt CHÍNH THỨC cho Stage 1, cần quay
     lại dùng đúng nó khi môi trường cho phép (không cần sửa code, chỉ cần
     `pip install scalene` -- module này tự phát hiện lại).

AN TOÀN THỰC THI: chỉ CHẠY THẬT các hàm đã có implementation callable trong
`versions/python_pure/pipeline.py::PIPELINE_REGISTRY` -- đây là nguồn thực
thi AN TOÀN DUY NHẤT trong scaffold này (headless, không GUI, không phụ
thuộc cv2/scipy/matplotlib). Các hàm GỐC trong `target.path` (vd
edge_detection.py của repo viraj7) dùng `cv2.imshow`/`waitKey` (treo tiến
trình chờ GUI) hoặc cú pháp Python 2 -- KHÔNG thể tự động chạy an toàn.
Hàm nào trong danh sách ứng viên KHÔNG có trong PIPELINE_REGISTRY sẽ đơn
giản KHÔNG xuất hiện trong DynamicProfileResult (không self_time) -- đây
chính là case Section 3.2 của POLO (runtime analysis không phủ hết), dùng
static PSG/PCG bổ sung qua stage0_graph/rank.py::func_rank (rank_source=
"static_fallback").
"""
from __future__ import annotations

import functools
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from stage0_graph.models import ProgramGraph

logger = logging.getLogger("benchmark.stage1_profiling.dynamic_profiler")


@dataclass
class DynamicProfileResult:
    """Kết quả 1 lần chạy workload dưới profiling động.

    self_time: {tên hàm: tổng thời gian EXCLUSIVE (giây, không tính hàm con)
        cộng dồn mọi lần gọi} -- nguồn cho FunctionNode.dynamic_time_pct.
    call_count: {tên hàm: tổng số lần được gọi, bất kể ai gọi}.
    edge_count/edge_time: {(caller, callee): count / tổng elapsed
        CUMULATIVE (gồm cả hàm con)} -- caller=None nghĩa là gọi trực tiếp
        từ driver profiling (không phải từ 1 hàm target khác), KHÔNG tạo
        thành cạnh PCG.
    total_time: tổng self_time mọi hàm -- mẫu số quy đổi % (khớp quy ước
        "total cost" của POLO/Callgrind: tổng exclusive cost = 100%).
    python_pct/native_pct: {tên hàm: %} lấy từ Scalene (hoặc cProfile
        fallback) -- BỔ SUNG, có thể thiếu hàm nếu profiler không bắt được mẫu.
    profiler_backend: "scalene" | "cprofile-fallback" | "none"
    """

    self_time: dict[str, float] = field(default_factory=dict)
    call_count: dict[str, int] = field(default_factory=dict)
    edge_count: dict[tuple[str | None, str], int] = field(default_factory=dict)
    edge_time: dict[tuple[str | None, str], float] = field(default_factory=dict)
    total_time: float = 0.0
    python_pct: dict[str, float] = field(default_factory=dict)
    native_pct: dict[str, float] = field(default_factory=dict)
    profiler_backend: str = "none"


class _DynamicCallTracker:
    """Wrap hàm target bằng decorator, tự quản lý 1 stack lời gọi (không cần
    `inspect.stack()` -- rẻ hơn và đủ chính xác vì ta kiểm soát toàn bộ lời
    gọi qua chính wrapper này) để tách self-time (exclusive) khỏi
    cumulative-time, giống cách cProfile tính tottime/cumtime."""

    def __init__(self) -> None:
        self.call_count: dict[str, int] = {}
        self.self_time: dict[str, float] = {}
        self.edge_count: dict[tuple[str | None, str], int] = {}
        self.edge_time: dict[tuple[str | None, str], float] = {}
        self._stack: list[dict] = []

    def wrap(self, name: str, fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            caller = self._stack[-1]["name"] if self._stack else None
            frame = {"name": name, "start": time.perf_counter(), "child_time": 0.0}
            self._stack.append(frame)
            try:
                return fn(*args, **kwargs)
            finally:
                self._stack.pop()
                elapsed = time.perf_counter() - frame["start"]
                self_elapsed = max(elapsed - frame["child_time"], 0.0)
                if self._stack:
                    self._stack[-1]["child_time"] += elapsed
                self.call_count[name] = self.call_count.get(name, 0) + 1
                self.self_time[name] = self.self_time.get(name, 0.0) + self_elapsed
                key = (caller, name)
                self.edge_count[key] = self.edge_count.get(key, 0) + 1
                self.edge_time[key] = self.edge_time.get(key, 0.0) + elapsed

        return wrapper


def _run_workload_with_tracker(
    candidates: dict[str, Callable], image: np.ndarray, iterations: int
) -> _DynamicCallTracker:
    tracker = _DynamicCallTracker()
    wrapped = {name: tracker.wrap(name, fn) for name, fn in candidates.items()}
    for _ in range(max(iterations, 1)):
        for name, fn in wrapped.items():
            try:
                fn(image)
            except Exception as exc:  # noqa: BLE001 -- 1 hàm lỗi không được phá cả profiling
                logger.warning(
                    "Hàm '%s' raise lỗi khi profiling động (vẫn tính thời "
                    "gian đã chạy, không dừng cả lượt profiling): %s: %s",
                    name, type(exc).__name__, exc,
                )
    return tracker


def run_dynamic_profiling(
    candidate_names: list[str],
    image: np.ndarray,
    workload_iterations: int,
    benchmark_root: Path,
) -> DynamicProfileResult:
    """Entry point chính. Xem docstring module để biết nguồn dữ liệu + giới
    hạn an toàn thực thi (chỉ chạy hàm có trong PIPELINE_REGISTRY của
    versions/python_pure/pipeline.py)."""
    sys.path.insert(0, str(benchmark_root)) if str(benchmark_root) not in sys.path else None
    from versions.python_pure import pipeline as python_pure_pipeline  # noqa: E402

    candidates = {
        name: python_pure_pipeline.PIPELINE_REGISTRY[name]
        for name in dict.fromkeys(candidate_names)  # khử trùng lặp, giữ thứ tự
        if name in python_pure_pipeline.PIPELINE_REGISTRY
    }
    missing = [n for n in dict.fromkeys(candidate_names) if n not in candidates]
    if missing:
        logger.info(
            "Profiling động: %d/%d hàm ứng viên KHÔNG có implementation "
            "callable trong versions/python_pure/pipeline.py -- bỏ qua khỏi "
            "runtime analysis (FuncRank sẽ dùng static fallback cho các hàm "
            "này, xem POLO Section 3.2): %s",
            len(missing), len(missing) + len(candidates), missing,
        )
    if not candidates:
        logger.warning("Profiling động: không có hàm nào chạy được -- trả về kết quả rỗng.")
        return DynamicProfileResult()

    logger.info(
        "Profiling động: chạy %d hàm x %d lần (workload_iterations) với "
        "ảnh mẫu thật shape=%s...", len(candidates), workload_iterations, image.shape,
    )
    tracker = _run_workload_with_tracker(candidates, image, workload_iterations)
    total_time = sum(tracker.self_time.values()) or 1e-12

    python_pct, native_pct, backend = _run_supplementary_profiler(
        candidates, image, workload_iterations, benchmark_root,
    )

    return DynamicProfileResult(
        self_time=tracker.self_time,
        call_count=tracker.call_count,
        edge_count=tracker.edge_count,
        edge_time=tracker.edge_time,
        total_time=total_time,
        python_pct=python_pct,
        native_pct=native_pct,
        profiler_backend=backend,
    )


def apply_dynamic_profile(graph: ProgramGraph, profile: DynamicProfileResult) -> None:
    """Ghi kết quả profiling ĐỘNG vào `graph` (mutate in-place):
    FunctionNode.dynamic_time_pct/dynamic_call_count, và CallEdge.count/
    time_contribution_pct cho các cạnh khớp được (theo TÊN hàm -- xem giới
    hạn khớp theo tên đã ghi trong stage0_graph/builder.py).

    Hàm/cạnh không khớp được dữ liệu động (không có trong `profile`) giữ
    nguyên giá trị mặc định (None/0) -- sẽ dùng static fallback ở
    stage0_graph/rank.py::func_rank.
    """
    total = profile.total_time or 1e-12

    for fn in graph.functions.values():
        if fn.name in profile.self_time:
            fn.dynamic_time_pct = 100.0 * profile.self_time[fn.name] / total
            fn.dynamic_call_count = profile.call_count.get(fn.name, 0)

    name_by_id = {fid: fn.name for fid, fn in graph.functions.items()}
    matched_edges = 0
    for edge in graph.call_edges:
        caller_name = name_by_id.get(edge.caller)
        callee_name = name_by_id.get(edge.callee)
        key = (caller_name, callee_name)
        if key in profile.edge_count:
            edge.count = profile.edge_count[key]
            edge.time_contribution_pct = 100.0 * profile.edge_time.get(key, 0.0) / total
            matched_edges += 1

    # Cạnh động đo được nhưng KHÔNG khớp cạnh tĩnh nào (vd static analysis bỏ
    # sót lời gọi phức tạp) -- không tự thêm cạnh mới vào graph (tránh đoán
    # bừa id cụ thể khi trùng tên), chỉ log để biết còn thiếu.
    dynamic_pairs = {k for k in profile.edge_count if k[0] is not None}
    static_pairs = {(name_by_id.get(e.caller), name_by_id.get(e.callee)) for e in graph.call_edges}
    unmatched = dynamic_pairs - static_pairs
    if unmatched:
        logger.info(
            "Profiling động thấy %d cặp (caller,callee) KHÔNG có trong PCG "
            "tĩnh (static analysis có thể đã bỏ sót) -- không tự thêm cạnh "
            "mới, chỉ ghi nhận: %s", len(unmatched), unmatched,
        )
    logger.info(
        "Đã áp dữ liệu động vào graph: %d/%d hàm, %d/%d cạnh có dữ liệu động.",
        sum(1 for fn in graph.functions.values() if fn.dynamic_time_pct is not None),
        len(graph.functions), matched_edges, len(graph.call_edges),
    )


# =============================================================================
# Bổ sung: Scalene (ưu tiên) hoặc cProfile (fallback tạm) cho breakdown %
# Python-only vs native/C-extension mỗi hàm.
# =============================================================================

def _run_supplementary_profiler(
    candidates: dict[str, Callable], image: np.ndarray, iterations: int, benchmark_root: Path,
) -> tuple[dict[str, float], dict[str, float], str]:
    try:
        import scalene  # noqa: F401
        has_scalene = True
    except ImportError:
        has_scalene = False

    if has_scalene:
        result = _run_scalene(candidates, iterations, benchmark_root)
        if result is not None:
            return result[0], result[1], "scalene"
        logger.warning(
            "Scalene đã cài nhưng chạy không ra dữ liệu -- fallback sang "
            "cProfile cho phần bổ sung breakdown Python/native."
        )
    else:
        logger.warning(
            "Chưa cài Scalene (pip install scalene) -- stage1_profiling/dynamic_profiler.py "
            "dùng fallback cProfile (có sẵn trong Python) cho phần BỔ SUNG "
            "breakdown %% Python/native. Scalene là công cụ đã chốt CHÍNH "
            "THỨC cho Stage 1 (đồng bộ công cụ) -- cài lại khi môi trường "
            "cho phép, module này tự dùng lại Scalene, không cần sửa code."
        )

    python_pct, native_pct = _run_cprofile_fallback(candidates, image, iterations)
    return python_pct, native_pct, "cprofile-fallback"


def _run_scalene(
    candidates: dict[str, Callable], iterations: int, benchmark_root: Path,
) -> tuple[dict[str, float], dict[str, float]] | None:
    """Chạy `scalene run --profile-all --cpu-only --json` trên 1 script
    driver (tự sinh) gọi lại đúng các hàm trong `candidates`, parse JSON kết
    quả. Trả về None nếu Scalene không cài/chạy lỗi/không đọc được output
    (KHÔNG raise -- caller tự fallback sang cProfile)."""
    tmp_dir = benchmark_root / "results" / ".dynamic_profile_tmp"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Không tạo được thư mục tạm cho Scalene: %s -- bỏ qua.", exc)
        return None

    driver_path = tmp_dir / "_scalene_driver.py"
    outfile_path = tmp_dir / "_scalene_out.json"

    names = list(candidates.keys())
    # Ảnh giả xám (chỉ cần cho Scalene "thấy" cùng loại workload, không cần
    # bit-identical với ảnh thật đang dùng cho nguồn chính -- Scalene chỉ đo
    # % Python/native, không quan tâm giá trị pixel).
    driver_src = (
        "import sys\n"
        f"sys.path.insert(0, {str(benchmark_root)!r})\n"
        "import numpy as np\n"
        "from versions.python_pure import pipeline as pp\n\n"
        "image = np.zeros((256, 256), dtype=np.uint8)\n"
        f"_NAMES = {names!r}\n"
        f"for _ in range({max(iterations, 1)}):\n"
        "    for _name in _NAMES:\n"
        "        getattr(pp, _name)(image)\n"
    )
    try:
        driver_path.write_text(driver_src, encoding="utf-8")
    except OSError as exc:
        logger.warning("Không ghi được script driver cho Scalene: %s -- bỏ qua.", exc)
        return None

    cmd = [
        sys.executable, "-m", "scalene", "run",
        "-o", str(outfile_path), "--cpu-only", "--profile-all",
        str(driver_path),
    ]
    env = dict(os.environ)
    # QUAN TRỌNG: đã kiểm chứng thực tế -- nếu đường dẫn project có ký tự
    # ngoài ASCII (vd "đồ án"), Scalene ghi 1 file redirect nội bộ bằng
    # encoding mặc định của Windows console (cp1252) và crash
    # UnicodeEncodeError nếu không ép UTF-8 mode.
    env["PYTHONUTF8"] = "1"

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Chạy Scalene lỗi (%s: %s) -- bỏ qua phần bổ sung.", type(exc).__name__, exc)
        return None

    if proc.returncode != 0 or not outfile_path.exists():
        logger.warning(
            "Scalene chạy không thành công (exit=%s). stderr=%r -- bỏ qua phần bổ sung.",
            proc.returncode, (proc.stderr or "")[-500:],
        )
        return None

    try:
        data = json.loads(outfile_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Không đọc được output JSON của Scalene: %s -- bỏ qua phần bổ sung.", exc)
        return None

    python_pct: dict[str, float] = {}
    native_pct: dict[str, float] = {}
    for _fname, finfo in data.get("files", {}).items():
        for fn_entry in finfo.get("functions", []):
            fname = fn_entry.get("line")
            if fname in candidates:
                python_pct[fname] = fn_entry.get("n_cpu_percent_python", 0.0)
                native_pct[fname] = fn_entry.get("n_cpu_percent_c", 0.0)

    if not python_pct:
        logger.info(
            "Scalene chạy thành công nhưng không có entry function nào khớp "
            "(thường do hàm quá nhanh với 1 sampling profiler) -- phần bổ "
            "sung Python/native breakdown trống cho lần chạy này, KHÔNG ảnh "
            "hưởng tới count/time chính (đã đo bằng decorator riêng)."
        )
    return python_pct, native_pct


def _run_cprofile_fallback(
    candidates: dict[str, Callable], image: np.ndarray, iterations: int,
) -> tuple[dict[str, float], dict[str, float]]:
    """Thay thế TẠM cho Scalene khi module `scalene` chưa cài được. Suy ra tỉ
    lệ XẤP XỈ python%/native% bằng cProfile: self-time của chính hàm
    (`inlinetime`, luôn là code Python thuần vì đây là hàm mình định nghĩa)
    so với tổng thời gian của các lời gọi built-in/C TRỰC TIẾP bên trong nó
    (`entry.calls` có `code` là `str`, vd "<built-in method numpy.zeros>").
    Không bắt được built-in gọi lồng sâu hơn 1 cấp -- chấp nhận được cho bản
    TẠM THỜI, không phải số liệu chính thức (đã kiểm chứng cấu trúc
    `cProfile.Profile().getstats()` thực tế trước khi viết hàm này)."""
    import cProfile

    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(max(iterations, 1)):
        for fn in candidates.values():
            fn(image)
    profiler.disable()

    python_pct: dict[str, float] = {}
    native_pct: dict[str, float] = {}
    for entry in profiler.getstats():
        code = entry.code
        name = code if isinstance(code, str) else code.co_name
        if name not in candidates:
            continue
        py_self = entry.inlinetime
        native_sub = sum(
            c.totaltime for c in (entry.calls or []) if isinstance(c.code, str)
        )
        total = py_self + native_sub
        if total > 0:
            python_pct[name] = 100.0 * py_self / total
            native_pct[name] = 100.0 * native_sub / total
    return python_pct, native_pct
