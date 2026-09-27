"""Chạy benchmark 3 phiên bản (python_pure / rust_pure / hybrid_pyo3) của
cùng 1 pipeline tiền xử lý ảnh, rồi in + lưu báo cáo so sánh tốc độ.

Chạy:  python stage6_benchmark/bench.py             (từ trong thư mục benchmark/)
   hoặc python "đường/dẫn/tới/benchmark/stage6_benchmark/bench.py"  (từ bất kỳ đâu)

3 CẤP ĐỘ (target.mode trong config.yaml -- xem README.md mục "3 cấp độ
benchmark"):
  - "function" (MẶC ĐỊNH, hành vi CŨ giữ nguyên 100%): dùng thẳng danh sách
    `benchmark.functions` cứng trong config.yaml, không đụng gì tới graph/.
  - "file" / "repo": build PCG/PSG bằng stage0_graph/builder.py, xếp hạng hotspot
    bằng stage0_graph/rank.py (FuncRank = PageRank), lấy top-K hàm thay cho danh
    sách cứng, xuất context JSON bằng stage0_graph/context_export.py, rồi benchmark
    top-K đó ở 2 mức: per-function (giống mode function) và whole-scope (gọi
    toàn bộ chuỗi 1 lần theo thứ tự topological của PCG).
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# --- Bootstrap sys.path để import được các package top-level của benchmark/
#     (data, versions, codegen, config_loader, runner.report, graph) bất kể cwd. ---
BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from config_loader import ensure_utf8_stdio, load_config  # noqa: E402
from data.loader import load_image  # noqa: E402
from versions.python_pure import pipeline as python_pure_pipeline  # noqa: E402
from versions.hybrid_pyo3 import pipeline as hybrid_pipeline  # noqa: E402
from versions.rust_pure import pipeline as rust_pure_pipeline  # noqa: E402
from stage6_benchmark import report  # noqa: E402

# Phải gọi TRƯỚC logging.basicConfig()/mọi print() -- xem docstring hàm này.
ensure_utf8_stdio()

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("benchmark.stage6_benchmark.bench")

_PIPELINE_MODULES = {
    "python_pure": python_pure_pipeline,
    "hybrid_pyo3": hybrid_pipeline,
    "rust_pure": rust_pure_pipeline,
}


def _time_callable(fn, warmup: int, iterations: int) -> list[float]:
    """Đo thời gian chạy fn() bằng perf_counter -- dùng cho Python thuần &
    hybrid (chạy trong cùng process, không có overhead spawn subprocess)."""
    for _ in range(warmup):
        fn()
    durations: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        durations.append(time.perf_counter() - start)
    return durations


def _bench_version(
    label: str, module, image, functions: list[str], warmup: int, iterations: int
) -> dict[str, list[float]]:
    """Đo per-function cho 1 phiên bản (python_pure/hybrid_pyo3/rust_pure).
    Hàm nào chưa có trong PIPELINE_REGISTRY của `module` (vd hàm hotspot mới
    phát hiện từ mode=file/repo mà chưa ai điền tay Rust/hybrid) bị BỎ QUA
    riêng lẻ (log warning rõ ràng), không crash cả benchmark -- áp dụng đồng
    nhất cho cả mode=function (ở đó luôn có đủ cả 4 hàm nên thực tế không có
    gì bị bỏ qua, hành vi không đổi)."""
    results: dict[str, list[float]] = {}
    for name in functions:
        fn = module.PIPELINE_REGISTRY.get(name)
        if fn is None:
            logger.warning(
                "%s: chưa có implementation cho hàm '%s' -- bỏ qua riêng hàm "
                "này (không crash cả benchmark).", label, name,
            )
            continue
        results[name] = _time_callable(lambda fn=fn: fn(image), warmup, iterations)
    return results


def _bench_python_pure(image, functions: list[str], warmup: int, iterations: int) -> dict[str, list[float]]:
    return _bench_version("python_pure", python_pure_pipeline, image, functions, warmup, iterations)


def _bench_hybrid(image, functions: list[str], warmup: int, iterations: int) -> dict[str, list[float]]:
    return _bench_version("hybrid_pyo3", hybrid_pipeline, image, functions, warmup, iterations)


def _bench_rust_pure(image, functions: list[str], warmup: int, iterations: int) -> dict[str, list[float]] | None:
    """Đo rust_pure IN-PROCESS qua extension PyO3 (versions/rust_pure/pyo3_ext/),
    bằng đúng time.perf_counter() như python_pure/hybrid_pyo3 -- xem docstring
    versions/rust_pure/pipeline.py để biết lý do đổi từ subprocess sang cách này.

    Nếu extension chưa build (`maturin develop --release` trong
    versions/rust_pure/pyo3_ext/), BỎ QUA đo rust_pure lần chạy này (không
    fallback dummy Python, không crash) -- python_pure và hybrid_pyo3 vẫn
    được đo bình thường.
    """
    if not rust_pure_pipeline.HAS_EXT:
        logger.warning(
            "Chưa build extension Rust 'rust_pure_ext' (chạy `maturin develop "
            "--release` trong versions/rust_pure/pyo3_ext/) -> bỏ qua đo "
            "rust_pure lần chạy này (KHÔNG phải lỗi -- vẫn tiếp tục đo "
            "python_pure và hybrid_pyo3). versions/rust_pure/src/main.rs (CLI) "
            "vẫn build/chạy độc lập được bằng `cargo build --release`, nhưng "
            "không còn được stage6_benchmark/bench.py dùng để đo tốc độ."
        )
        return None
    return _bench_version("rust_pure", rust_pure_pipeline, image, functions, warmup, iterations)


def _select_target_functions(cfg: dict, image) -> tuple[list[str], object | None]:
    """Trả về (functions, graph).

    - target.mode == "function" (mặc định, KHÔNG có trong config.yaml cũ thì
      .get() trả về "function" -- tương thích ngược 100%): trả thẳng
      cfg["benchmark"]["functions"] như code cũ, graph=None, KHÔNG import bất
      cứ gì trong stage0_graph/ (không phụ thuộc tree-sitter/networkx/scalene).
    - target.mode == "file" | "repo": build PCG/PSG bằng stage0_graph/builder.py.
      Nếu graph.build_mode == "dynamic" (config.yaml), chạy thêm profiling
      ĐỘNG (stage1_profiling/dynamic_profiler.py, theo POLO -- xem README mục "PCG động
      vs tĩnh") với `image` làm workload thật, áp kết quả vào graph TRƯỚC
      khi xuất context JSON. Cuối cùng lấy top-K hàm theo FuncRank
      (stage0_graph/rank.py) làm danh sách hàm để benchmark thay cho danh sách cứng.
    """
    target_cfg = cfg.get("target") or {}
    mode = target_cfg.get("mode", "function")

    if mode == "function":
        return cfg["benchmark"]["functions"], None

    if mode not in ("file", "repo"):
        raise ValueError(f"target.mode không hỗ trợ: '{mode}' (function | file | repo)")

    # target.source (tên MỚI) chấp nhận cả đường dẫn local lẫn URL GitHub;
    # target.path (tên CŨ) vẫn đọc được để không phá config của ai đang dùng.
    target_source = target_cfg.get("source")
    if not target_source:
        legacy_path = target_cfg.get("path")
        if legacy_path:
            logger.warning(
                "config.yaml dùng `target.path` (tên CŨ) -- đã đổi thành "
                "`target.source` (chấp nhận cả local path lẫn URL GitHub). "
                "Vẫn chạy được, nhưng nên đổi tên khoá trong config."
            )
            target_source = legacy_path
    if not target_source:
        raise ValueError(
            f"target.mode='{mode}' yêu cầu phải điền target.source trong config.yaml"
        )

    # Import "lazy" -- chỉ chạm tới stage0_graph/ (và phụ thuộc tree-sitter/networkx
    # của nó) khi thật sự cần, để mode=function không bị ảnh hưởng.
    from input.intake import IntakeError, resolve_target
    from stage0_graph.builder import build_graph, discover_python_files
    from stage0_graph.context_export import export_context_default
    from stage0_graph.rank import top_k_functions

    graph_cfg = cfg.get("graph") or {}
    build_mode = graph_cfg.get("build_mode", "static")
    if build_mode not in ("static", "dynamic"):
        raise ValueError(f"graph.build_mode không hỗ trợ: '{build_mode}' (static | dynamic)")

    # input/intake.py là nơi DUY NHẤT phân biệt local path vs URL GitHub
    # (clone về data/cloned_repos/ nếu là URL).
    try:
        target = resolve_target(target_source)
    except IntakeError as exc:
        logger.error(
            "Không resolve được target.source=%r: %s -- bỏ qua phần benchmark "
            "theo graph (không crash).", target_source, exc,
        )
        return [], None

    files = discover_python_files(target)
    if not files:
        logger.warning(
            "target.mode='%s' nhưng không tìm thấy file .py nào tại '%s' -- "
            "không có gì để benchmark.", mode, target,
        )
        return [], None

    scope_root = target if target.is_dir() else target.parent
    graph = build_graph(files, scope_root)
    graph.build_mode = build_mode
    logger.info(
        "Graph builder (%s, build_mode=%s): %d file, %d hàm, %d call edge, %d import edge.",
        graph.backend, build_mode, len(graph.files), len(graph.functions),
        len(graph.call_edges), len(graph.import_edges),
    )

    if build_mode == "dynamic":
        from stage1_profiling.dynamic_profiler import apply_dynamic_profile, run_dynamic_profiling

        dyn_cfg = graph_cfg.get("dynamic") or {}
        workload_iterations = int(dyn_cfg.get("workload_iterations", 20))
        candidate_names = list(dict.fromkeys(fn.name for fn in graph.functions.values()))
        profile = run_dynamic_profiling(candidate_names, image, workload_iterations, BENCHMARK_ROOT)
        apply_dynamic_profile(graph, profile)
        # Gắn kèm kết quả profiling thô vào graph để Stage 3
        # (stage3_context_packaging/packager.py) dùng lại mà không phải chạy
        # profiling lần nữa. bench.py chạy độc lập không dùng tới field này.
        graph._dynamic_profile = profile  # noqa: SLF001
        logger.info(
            "Profiling động (%s): %d hàm có dữ liệu thật, tổng self-time=%.6fs.",
            profile.profiler_backend, len(profile.self_time), profile.total_time,
        )

    results_dir = BENCHMARK_ROOT / cfg["paths"]["results_dir"]
    export_context_default(graph, results_dir)

    top_k = int(graph_cfg.get("top_k_hotspots", 5))
    top = top_k_functions(graph, top_k, build_mode=build_mode)
    # dict.fromkeys giữ thứ tự, khử trùng lặp TÊN (vd 2 file khác nhau cùng
    # định nghĩa hàm "gen_gauss1d_k" -- PIPELINE_REGISTRY chỉ khoá theo tên
    # trần nên đo 2 lần cũng chỉ ra 1 kết quả, khử trùng ở đây để khỏi tốn
    # công đo lặp vô ích; xem giới hạn "khớp theo tên" trong stage0_graph/builder.py).
    functions = list(dict.fromkeys(fn.name for fn, _score in top))
    logger.info("Top-%d hotspot theo FuncRank (%s, build_mode=%s): %s", top_k, mode, build_mode, functions)
    return functions, graph


def _topological_order(graph, functions: list[str]) -> list[str]:
    """Sắp `functions` theo thứ tự topological của PCG (cạnh A->B nghĩa là A
    gọi B, nên A đứng trước B trong thứ tự -- phản ánh thứ tự chương trình
    thật sẽ "chạm" tới các hàm này). Nếu PCG con (giới hạn trong `functions`)
    có chu trình (đệ quy vòng), không topological-sort được -- fallback giữ
    nguyên thứ tự FuncRank, log warning, KHÔNG crash."""
    import networkx as nx

    id_by_name: dict[str, str] = {}
    name_by_id: dict[str, str] = {}
    for fid, fn in graph.functions.items():
        name_by_id[fid] = fn.name
        if fn.name in functions:
            id_by_name.setdefault(fn.name, fid)  # best-effort: 1 id đại diện / tên

    sub_ids = [id_by_name[n] for n in functions if n in id_by_name]
    G = nx.DiGraph()
    G.add_nodes_from(sub_ids)
    for e in graph.call_edges:
        if e.caller in sub_ids and e.callee in sub_ids:
            G.add_edge(e.caller, e.callee)

    try:
        order_ids = list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible:
        logger.warning(
            "PCG trong scope top-K có chu trình (đệ quy vòng) -- giữ nguyên "
            "thứ tự theo FuncRank cho lượt đo whole-scope."
        )
        return list(functions)

    ordered = [name_by_id[i] for i in order_ids]
    remaining = [n for n in functions if n not in ordered]  # hàm không nằm trong sub-graph (không có cạnh nào)
    return ordered + remaining


def _bench_whole_scope(label: str, module, image, ordered_functions: list[str]) -> float | None:
    """Đo 1 LẦN DUY NHẤT toàn bộ chuỗi `ordered_functions` (gọi lần lượt,
    KHÔNG lặp N lần như per-function), trả về tổng thời gian (giây). Hàm nào
    chưa có implementation bị bỏ qua (log warning), không crash. Trả về None
    nếu không có hàm nào trong chuỗi có implementation."""
    callables = []
    for name in ordered_functions:
        fn = module.PIPELINE_REGISTRY.get(name)
        if fn is None:
            logger.warning(
                "%s (whole-scope): bỏ qua hàm '%s' (chưa có implementation).",
                label, name,
            )
            continue
        callables.append(fn)
    if not callables:
        return None
    start = time.perf_counter()
    for fn in callables:
        fn(image)
    return time.perf_counter() - start


def main() -> None:
    cfg = load_config()
    b_cfg = cfg["benchmark"]
    warmup: int = b_cfg["warmup"]
    iterations: int = b_cfg["iterations"]

    target_cfg = cfg.get("target") or {}
    mode = target_cfg.get("mode", "function")

    # Nạp ảnh TRƯỚC khi chọn hàm: mode=file/repo với graph.build_mode=dynamic
    # cần ảnh mẫu thật làm workload cho profiling động (xem
    # stage1_profiling/dynamic_profiler.py) -- mode=function không dùng image ở bước
    # này (giữ nguyên hành vi cũ: chỉ nạp ảnh 1 lần, dùng cho benchmark).
    logger.info("Nạp ảnh input theo data.source='%s'...", cfg["data"]["source"])
    image = load_image(cfg)
    logger.info("Ảnh input: shape=%s dtype=%s", image.shape, image.dtype)

    functions, graph = _select_target_functions(cfg, image)
    if not functions:
        logger.error("Không có hàm nào để benchmark (target rỗng) -- dừng.")
        return

    all_results: dict[str, dict[str, list[float]] | None] = {}

    logger.info("Đo python_pure (warmup=%d, iterations=%d)...", warmup, iterations)
    all_results["python_pure"] = _bench_python_pure(image, functions, warmup, iterations)

    logger.info("Đo hybrid_pyo3 (warmup=%d, iterations=%d)...", warmup, iterations)
    all_results["hybrid_pyo3"] = _bench_hybrid(image, functions, warmup, iterations)

    logger.info("Đo rust_pure (in-process qua PyO3, warmup=%d, iterations=%d)...", warmup, iterations)
    all_results["rust_pure"] = _bench_rust_pure(image, functions, warmup, iterations)

    results_dir = BENCHMARK_ROOT / cfg["paths"]["results_dir"]
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    # --- Per-function: LƯU Ý format file raw_*.json giữ NGUYÊN như trước khi
    # có stage0_graph/ (chỉ {version: {function: [durations]}}) -- để không phá vỡ
    # report.py::regenerate_latest() hay bất kỳ tool nào khác đọc file cũ. ---
    raw_path = results_dir / f"raw_{timestamp}.json"
    with raw_path.open("w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    logger.info("Đã lưu kết quả thô: %s", raw_path)

    table = report.build_report(all_results)
    print("\n" + table)

    report_path = results_dir / f"report_{timestamp}.md"
    report_lines = [table]

    # --- Whole-scope: CHỈ chạy khi mode=file/repo (có graph) -- mode=function
    # không đổi gì so với trước (không có bước này). ---
    if mode in ("file", "repo") and graph is not None:
        ordered = _topological_order(graph, functions)
        logger.info("Thứ tự whole-scope (topological theo PCG): %s", ordered)

        whole_scope_results: dict[str, float | None] = {}
        for label, module in _PIPELINE_MODULES.items():
            if label == "rust_pure" and not rust_pure_pipeline.HAS_EXT:
                whole_scope_results[label] = None
                continue
            whole_scope_results[label] = _bench_whole_scope(label, module, image, ordered)

        whole_scope_path = results_dir / f"whole_scope_{timestamp}.json"
        whole_scope_path.write_text(
            json.dumps(
                {"mode": mode, "target_path": target_cfg.get("path"), "order": ordered, "results_sec": whole_scope_results},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        logger.info("Đã lưu kết quả whole-scope: %s", whole_scope_path)

        whole_scope_table = report.build_whole_scope_report(whole_scope_results, ordered)
        print("\n" + whole_scope_table)
        report_lines.append(whole_scope_table)

    report_path.write_text("\n\n".join(report_lines), encoding="utf-8")
    logger.info("Đã lưu báo cáo: %s", report_path)


if __name__ == "__main__":
    main()
