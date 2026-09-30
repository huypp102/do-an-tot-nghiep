"""Đường chạy ĐỘNG cho một repo thật (PHA 0 + A..F ghép lại).

Tách khỏi `run_pipeline.py` có chủ ý: đường chạy LEGACY (`target.mode=function`,
4 hàm viraj7, workload 1 ảnh, đo in-process) phải giữ nguyên 100%, nên nó ở
lại `run_pipeline.run_once` và không bị file này chạm tới.

LUỒNG (mỗi bước ghi lý do cụ thể cho hotspot thất bại -- xem outcomes.py):

    A  copy repo -> venv riêng -> cài phụ thuộc                  INSTALL_FAILED
    0  Stage 0 dựng PCG/PSG + FuncRank -> top-K hotspot
    0  Stage 2 Decision Gate                                     GATE_SKIPPED
    B  đổi file -> module:qualname                               UNRESOLVABLE_IMPORT
    A  chạy bộ test GỐC kèm plugin ghi đối số                     BASELINE_FAILED
    B  phát lại 2 lần + phân tầng kiểu      NOT_COVERED / UNREPLAYABLE / NONDETERMINISTIC / UNSUPPORTED
    3/4 đóng gói context + Generator Agent sinh Rust theo CHỮ KÝ THẬT   LLM_FAILED
    5  cargo check + vòng sửa lỗi (Pass@1 / DSR@1)                COMPILE_FAILED
    D  maturin develop vào venv repo, Tầng 1 trước Tầng 2         BUILD_FAILED
    B  so khớp Rust vs Python trên đối số thật (CẢ 2 phiên bản)   CORRECTNESS_FAILED
    E  chạy lại bộ test với plugin HOÁN ĐỔI Rust -> hybrid_tests, REGRESSION_FREE
    F  đo python_pure + các bản Rust trong CÙNG 1 tiến trình      MEASURE_FAILED
    F  vòng tối ưu: Decision Agent -> Generator Agent -> build lại -> đo lại
    0  chốt status repo, ghi output kèm metadata tái lập

`summary["ok"]` chỉ True khi có >= 1 hotspot đạt `MEASURED`.
"""
from __future__ import annotations

import copy as _copy
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import outcomes
from stage1_profiling.deep_compare import TIER_KERNEL, TIER_NATIVE

logger = logging.getLogger("benchmark.repo_pipeline")


def _banner(stage: str, title: str) -> None:
    logger.info("-" * 64)
    logger.info("%-9s | %s", stage, title)
    logger.info("-" * 64)


def _mean(values) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


@dataclass
class HotspotRecord:
    """Tất cả những gì biết về MỘT hotspot, gom một chỗ.

    Mỗi hotspot rời pipeline với ĐÚNG MỘT `reason` (Pha 0) -- không có đường
    nào cho phép nó biến mất khỏi báo cáo như trước.
    """

    function_name: str
    reason: str = ""
    detail: str = ""
    gate_label: str = ""
    # PHA 4.4: ba tầng gate giữ RIÊNG, không gộp thành một điểm số. `gate_label`
    # ở trên chỉ là bản map 3 nhãn cũ cho tương thích ngược.
    gate_decision: str = ""          # SELECT | REVIEW | KEEP_PYTHON | REJECT_BLOCKED
    gate_decision_reason: str = ""
    gate_hotspot_level: str = ""
    gate_feasibility: str = ""
    gate_confidence_level: str = ""
    # LẦN CHẠY CHẨN ĐOÁN (mục C): số thô của Gate (GateVerdict), trước đây CHỈ
    # có bản flatten (gate_hotspot_level/gate_feasibility ở trên) chứ không có
    # con số/danh sách gốc -- không quét lại offline được vì thiếu dữ liệu.
    funcrank_static: float | None = None
    funcrank_quantile: float | None = None
    direct_runtime_share_pct: float | None = None
    dependency_roots: list = field(default_factory=list)
    blocked_roots: list = field(default_factory=list)
    # LẦN CHẠY CHẨN ĐOÁN (mục B6): CHỈ điền khi reason=NOT_COVERED_BY_TESTS --
    # để ước lượng có đáng sinh đầu vào giả cho hàm này thay vì loại luôn hay
    # không (hàm dài + nhiều phụ thuộc thì sinh input giả rủi ro cao hơn).
    not_covered_n_lines: int | None = None
    not_covered_dependencies: list = field(default_factory=list)
    translation_unit: str = ""       # FUNCTION | BATCH_CALLER
    tier: str = ""
    tier_reason: str = ""
    module_target: str = ""
    # PHA 2: tên hotspot khớp nhiều node mà không phân biệt được -> đối số ghi
    # được và bản Rust có thể không thuộc cùng một hàm. Phải lộ ra trong kết quả.
    ambiguous_name: bool = False
    ambiguity_detail: str = ""
    n_name_matches: int = 1
    n_captured_calls: int = 0
    n_distinct_inputs: int | None = None
    """CHẾ ĐỘ BÓNG (mục B3): số đầu vào KHÁC NHAU trong n_captured_calls lời
    gọi -- None nếu không đọc lại được call nào."""
    nondeterministic_shadow_pil_recheck: dict | None = None
    """CHẾ ĐỘ BÓNG (mục B5): chỉ có giá trị khi reason=NONDETERMINISTIC VÀ
    kết quả trông giống PIL Image -- xem _replay_runner.py::_pil_shadow_recheck."""
    shadow_mutation: dict | None = None
    """CHẾ ĐỘ BÓNG (mục B2): kết quả mutation test, status = TESTED |
    KHONG_KIEM_DUOC -- xem _replay_runner.py::_generate_mutant_call."""
    observed_arg_types: list[str] = field(default_factory=list)
    observed_kwarg_types: dict = field(default_factory=dict)
    # Stage 5
    compiled: bool | None = None
    pass_at_1: bool | None = None
    compile_attempts: int = 0
    fix_rounds: int = 0
    error_classes: list[str] = field(default_factory=list)
    compiled_at_round: int | None = None
    """Vòng (0-based) mà hotspot này biên dịch được, None nếu không bao giờ
    biên dịch được trong giới hạn max_retries (hoặc bị skip). Pass@1 vẫn LUÔN
    tính từ vòng 0 riêng, không đổi theo max_retries -- xem loop_runner.py."""
    build_status: str = ""
    # correctness theo TỪNG phiên bản: {"rust_pure": "MATCH", "hybrid_pyo3": ...}
    correctness: dict = field(default_factory=dict)
    # Pha F
    input_spec: dict = field(default_factory=dict)
    rounds: list[dict] = field(default_factory=list)
    accepted_round: int | None = None
    accepted_speedup: dict = field(default_factory=dict)   # {version: speedup}
    best_speedup: dict = field(default_factory=dict)       # {version: speedup} (nhãn riêng)
    hybrid_slower: bool = False
    """CHẾ ĐỘ BÓNG (mục B4): hybrid_pyo3 được CHẤP NHẬN nhưng speedup < 1.0 --
    chỉ đo, không đổi quyết định accept."""
    shadow_hardcoding: dict = field(default_factory=dict)
    """CHẾ ĐỘ BÓNG (mục B1): kết quả dò gõ cứng tĩnh trên code Rust ĐÃ biên
    dịch được -- xem stage5_compiler_in_the_loop/shadow_hardcoding.py."""
    stopped_by_cap: bool = False
    # --- PHẦN 1.3: chống "đúng một cách rỗng" ---
    rust_call_count: int | None = None
    """Số lần hàm Rust THẬT SỰ được bộ test của repo gọi ở lượt hybrid.
    `None` = chưa chạy lượt hybrid; `0` = VACUOUS."""
    vacuous: bool = False
    """`rust_call_count == 0`: test xanh nhưng không chạm tới bản Rust, nên
    kết quả đó không chứng minh gì -> loại khỏi tỉ lệ regression-free."""
    regression_free_contrib: bool = False
    """Hotspot này có nằm trong lượt hybrid mà KHÔNG mất test nào không."""
    # --- PHẦN 2: ablation ---
    arm: str = ""
    """Nhánh ablation đã sinh ra bản Rust này: "graph" | "none" | "" (không ablation)."""
    prompt_tokens: int | None = None
    truncated: bool = False
    """Prompt vượt `num_ctx` -> Ollama cắt phần đầu. Hotspot bị cắt ở BẤT KỲ
    nhánh nào sẽ bị gắn CONFOUNDED và tách khỏi phép so sánh chính."""
    confounded: bool = False
    llm_seconds: float | None = None

    def set_reason(self, reason: str, detail: str = "") -> None:
        """Lý do ĐẦU TIÊN thắng: nó là nguyên nhân gốc, các lý do sau chỉ là
        hệ quả (vd không build được thì tất nhiên cũng không đo được)."""
        if self.reason:
            return
        self.reason = outcomes.validate_reason(reason)
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "function": self.function_name,
            "reason": self.reason or outcomes.MEASURED,
            "reason_help": outcomes.REASON_HELP.get(self.reason or outcomes.MEASURED, ""),
            "detail": self.detail,
            "gate_label": self.gate_label,
            "gate_decision": self.gate_decision,
            "gate_decision_reason": self.gate_decision_reason,
            "gate_hotspot_level": self.gate_hotspot_level,
            "gate_feasibility": self.gate_feasibility,
            "gate_confidence_level": self.gate_confidence_level,
            "funcrank_static": self.funcrank_static,
            "funcrank_quantile": self.funcrank_quantile,
            "direct_runtime_share_pct": self.direct_runtime_share_pct,
            "dependency_roots": self.dependency_roots,
            "blocked_roots": self.blocked_roots,
            "not_covered_n_lines": self.not_covered_n_lines,
            "not_covered_dependencies": self.not_covered_dependencies,
            "translation_unit": self.translation_unit,
            "tier": self.tier,
            "tier_reason": self.tier_reason,
            "module_target": self.module_target,
            "ambiguous_name": self.ambiguous_name,
            "ambiguity_detail": self.ambiguity_detail,
            "n_name_matches": self.n_name_matches,
            "n_captured_calls": self.n_captured_calls,
            "n_distinct_inputs": self.n_distinct_inputs,
            "nondeterministic_shadow_pil_recheck": self.nondeterministic_shadow_pil_recheck,
            "shadow_mutation": self.shadow_mutation,
            "observed_arg_types": self.observed_arg_types,
            "observed_kwarg_types": self.observed_kwarg_types,
            "compiled": self.compiled,
            "pass_at_1": self.pass_at_1,
            "compile_attempts": self.compile_attempts,
            "fix_rounds": self.fix_rounds,
            "error_classes": self.error_classes,
            "compiled_at_round": self.compiled_at_round,
            "build_status": self.build_status,
            "correctness": self.correctness,
            "input_spec": self.input_spec,
            "rounds": self.rounds,
            "accepted_round": self.accepted_round,
            "accepted_speedup": self.accepted_speedup,
            "best_speedup_any_round": self.best_speedup,
            "hybrid_slower": self.hybrid_slower,
            "shadow_hardcoding": self.shadow_hardcoding,
            "stopped_by_cap": self.stopped_by_cap,
            "rust_call_count": self.rust_call_count,
            "vacuous": self.vacuous,
            "regression_free_contrib": self.regression_free_contrib,
            "arm": self.arm,
            "prompt_tokens": self.prompt_tokens,
            "truncated": self.truncated,
            "confounded": self.confounded,
            "llm_seconds": self.llm_seconds,
        }


# ---------------------------------------------------------------------------
def run_repo_pipeline(
    cfg: dict,
    repo_path: Path,
    results_dir: Path,
    timestamp: str,
    label: str,
    benchmark_root: Path,
) -> dict:
    """Chạy TRỌN đường động cho 1 repo. KHÔNG raise: mọi lỗi thành status/lý do."""
    import env_metadata
    from stage5_compiler_in_the_loop import repo_runner

    t_start = time.perf_counter()
    ro_cfg = cfg.get("repo_oracle") or {}
    b_cfg = cfg.get("benchmark") or {}
    warmup = int(b_cfg.get("warmup", 5))
    iterations = int(b_cfg.get("iterations", 20))
    budget = int(ro_cfg.get("repo_time_budget_sec", repo_runner.DEFAULT_REPO_BUDGET_SEC))
    test_timeout = int(ro_cfg.get("test_timeout_sec", repo_runner.DEFAULT_TEST_TIMEOUT_SEC))

    summary: dict = {
        "timestamp": timestamp,
        "label": label,
        "mode": "repo_dynamic",
        "target_source": str(repo_path),
        "ok": False,
        "repo_status": outcomes.NO_MEASURABLE_HOTSPOT,
        "environment": env_metadata.collect(cfg, benchmark_root),
        "bench_params": {"warmup": warmup, "iterations": iterations},
        "workload_source": None,
        "stages": {},
        "hotspots": [],
    }

    records: dict[str, HotspotRecord] = {}

    def finish(status: str, note: str = "") -> dict:
        """Chốt summary. Một đường ra DUY NHẤT -> không thể thoát mà thiếu status."""
        summary["repo_status"] = status
        if note:
            summary["status_note"] = note
        summary["hotspots"] = [r.as_dict() for r in records.values()]
        # ------------------------------------- AUDIT_RUN4_v2 mục 5, điểm 2
        # `finish()` là điểm thoát DUY NHẤT nên bắt được MỌI đường ra (kể cả
        # chết sớm trước khi có graph). KHÔNG cần truy cập `graph` ở đây --
        # chỉ cần `records`, và overlay_outcomes() tự xử lý trường hợp chưa
        # có pcg.json (ghi graph_available=false thay vì đoán).
        # symbols_in_prompt CHƯA implement (cần đối chiếu dependency_roots
        # với prompt đã lưu ở generator_agent.py) -- để None, không đoán bừa.
        try:
            from audit.graph_snapshot import overlay_outcomes

            overlay_outcomes(
                results_dir=results_dir, label=label, records=records,
                in_prompt_by_function=None,
            )
        except Exception:  # noqa: BLE001 -- quan sát, không được đổi outcome
            logger.exception("audit.graph_snapshot.overlay_outcomes lỗi -- bỏ qua.")
        summary["ok"] = outcomes.is_success(status)
        summary["duration_sec"] = round(time.perf_counter() - t_start, 2)
        out_path = Path(results_dir) / f"repo_summary_{timestamp}_{label}.json"
        try:
            out_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            summary["summary_path"] = str(out_path)
        except OSError as exc:
            logger.error("Không ghi được %s: %s", out_path, exc)
        return summary

    def over_budget() -> bool:
        return (time.perf_counter() - t_start) > budget

    # ---------------------------------------------------------------- PHA A
    _banner("PHA A", f"Copy repo '{label}' + dựng venv riêng")
    from versions.registry import describe_workload_source

    summary["workload_source"] = describe_workload_source("repo")

    try:
        work_dir = repo_runner.prepare_work_copy(
            # `work_root` rỗng (mặc định trung tính) -> để code tự chọn thư mục
            # tạm của HỆ ĐIỀU HÀNH thay vì hard-code /tmp.
            repo_path,
            Path(str(ro_cfg.get("work_root") or "").strip() or repo_runner.default_work_root()),
        )
    except OSError as exc:
        return finish(outcomes.INSTALL_FAILED, f"không copy được repo: {exc}")

    venv_py, err = repo_runner.create_venv(work_dir)
    if venv_py is None:
        return finish(outcomes.INSTALL_FAILED, err)

    keep_venv = bool(ro_cfg.get("keep_venv", False))
    try:
        ok, err = repo_runner.install_repo(venv_py, work_dir)
        if not ok:
            return finish(outcomes.INSTALL_FAILED, err)

        return _run_after_install(
            cfg=cfg, work_dir=work_dir, venv_py=venv_py, results_dir=results_dir,
            timestamp=timestamp, label=label, benchmark_root=benchmark_root,
            summary=summary, records=records, finish=finish, over_budget=over_budget,
            warmup=warmup, iterations=iterations, test_timeout=test_timeout,
        )
    finally:
        repo_runner.cleanup_venv(work_dir, keep=keep_venv)
        if not keep_venv and bool(ro_cfg.get("cleanup_work_dir", False)):
            import shutil

            shutil.rmtree(work_dir, onerror=repo_runner._force_remove)  # noqa: SLF001
            logger.info("Đã xoá work_dir của repo (cleanup_work_dir=true).")


def _run_after_install(
    *, cfg, work_dir, venv_py, results_dir, timestamp, label, benchmark_root,
    summary, records, finish, over_budget, warmup, iterations, test_timeout,
):
    """Phần sau khi môi trường repo đã sẵn sàng. Tách hàm để `finally` ở trên
    luôn xoá venv, kể cả khi bước nào dưới đây thoát sớm."""
    import env_metadata  # noqa: F401  (đã dùng ở caller, giữ import gần chỗ dùng)
    from stage0_graph.builder import build_graph, discover_python_files
    from stage0_graph.rank import top_k_functions
    from stage1_profiling.module_resolve import build_hotspot_specs
    from stage1_profiling.replay import run_replay_checks
    from stage5_compiler_in_the_loop import crate_builder, repo_runner

    ro_cfg = cfg.get("repo_oracle") or {}
    graph_cfg = cfg.get("graph") or {}
    # Đọc TRƯỚC khi dựng graph -- trước đây dòng `graph.build_mode = "static"`
    # bên dưới bị hard-code TRƯỚC khi biến này tồn tại, nên nhãn build_mode
    # xuất ra (context_export.py, packager.py) luôn ghi "static" dù config đặt
    # "dynamic". KHÔNG có logic nào đọc `graph.build_mode` để RẼ NHÁNH hành vi
    # (đã rà: chỉ dùng làm nhãn/log) nên sửa nhãn ở đây không đổi kết quả.
    build_mode = str(graph_cfg.get("build_mode", "static"))

    # ------------------------------------------------------------ STAGE 0
    _banner("STAGE 0", "Dựng PCG/PSG + FuncRank trên BẢN COPY của repo")
    files = discover_python_files(work_dir)
    if not files:
        return finish(outcomes.NO_MEASURABLE_HOTSPOT, "không có file .py nào trong repo")
    graph = build_graph(files, work_dir)
    graph.build_mode = build_mode
    logger.info(
        "Graph (%s): %d file, %d hàm, %d call edge.",
        graph.backend, len(graph.files), len(graph.functions), len(graph.call_edges),
    )
    # PHẦN 1.1 -- POOL rộng rồi LỌC, thay cho lấy thẳng top-K.
    # Công thức FuncRank KHÔNG đổi và không có điểm cộng thủ công nào:
    # `top_k_functions` vẫn trả về đúng thứ tự FuncRank, ta chỉ xét nhiều hơn
    # rồi lọc bằng tiêu chí khách quan (phát lại được + thuộc Tầng 1/2) và giữ
    # `top_k_translate` hotspot ĐẦU TIÊN theo đúng thứ tự đó.
    pool_size = int(graph_cfg.get("candidate_pool", 30))

    # LỌC HÀM TEST TRƯỚC KHI XẾP HẠNG. Trên lượt chạy thật, 18% chỗ trong pool
    # bị hàm test chiếm (BBuf_onnx_learn: 14/23 = 60%). Dịch hàm test sang Rust
    # là vô nghĩa, và thay nó bằng Rust thì đổi luôn chính ORACLE của ta.
    from stage0_graph.test_filter import exclude_test_functions

    excluded_ids, test_filter_stats = exclude_test_functions(graph)

    from stage0_graph.confidence import compute_graph_confidence

    graph_confidence = compute_graph_confidence(graph)

    functions = list(dict.fromkeys(
        fn.name for fn, _s in top_k_functions(
            graph, pool_size, build_mode=build_mode, exclude_ids=excluded_ids
        )
    ))
    if not functions:
        return finish(outcomes.NO_MEASURABLE_HOTSPOT, "FuncRank không chọn được hotspot nào")
    # Thứ tự này là THỨ TỰ FUNCRANK và được giữ nguyên tới bước cắt top-K.
    funcrank_order = list(functions)
    logger.info(
        "Pool ứng viên theo FuncRank (candidate_pool=%d, build_mode=%s): %d hotspot: %s",
        pool_size, build_mode, len(functions), functions,
    )
    summary["stages"]["stage0"] = {
        "graph_backend": graph.backend,
        "n_files": len(graph.files), "n_functions": len(graph.functions),
        "candidate_pool": pool_size,
        "build_mode": build_mode,
        "funcrank_order": funcrank_order,
        "functions": functions,
        "test_filter": test_filter_stats,
        # PHA 4.1: graph này đáng tin đến đâu. KHÔNG dùng để loại repo, chỉ để
        # đọc kèm mọi kết luận dựa trên graph.
        "graph_confidence": graph_confidence,
        "call_resolution": dict(getattr(graph, "call_resolution", None) or {}),
        # PHA 3: PSG ĐẦY ĐỦ (class/biến toàn cục/kế thừa/sở hữu). Khác hẳn
        # `import_edges` ở PSG rút gọn -- xem stage0_graph/psg.py.
        "psg_full": {
            "backend": getattr(graph, "psg_backend", ""),
            "n_classes": len(getattr(graph, "classes", None) or {}),
            "n_global_vars": len(getattr(graph, "global_vars", None) or {}),
            "n_inheritance_edges": len(getattr(graph, "inheritance_edges", None) or []),
            "n_ownership_edges": len(getattr(graph, "ownership_edges", None) or []),
        },
    }
    for name in functions:
        records[name] = HotspotRecord(function_name=name)

    # ------------------------------------------------------------ STAGE 2
    gate_enabled = bool((cfg.get("decision_gate") or {}).get("enabled", True))
    if gate_enabled:
        _banner("STAGE 2", "Decision Gate 3 TẦNG -- hotspot / feasibility / confidence")
        from stage2_decision_gate.gate import LABEL_CANDIDATE
        from stage2_decision_gate.gate3 import evaluate_functions, summarize

        graph_conf = graph_confidence   # đã tính ở Stage 0, không tính lại
        verdicts = evaluate_functions(
            graph, functions, profile=getattr(graph, "_dynamic_profile", None)
        )
        # `simple_label` map 4 quyết định -> 3 nhãn CŨ, nên phần còn lại của
        # pipeline không phải sửa gì (PHA 4.5). Chi tiết 3 tầng vẫn được giữ
        # đủ trong `stages.stage2.gate3`.
        labels = {name: v.as_dict()["simple_label"] for name, v in verdicts.items()}
        for name, v in verdicts.items():
            if name not in records:
                continue
            rec = records[name]
            rec.gate_label = labels[name]
            rec.gate_decision = v.decision
            rec.gate_decision_reason = v.decision_reason
            rec.gate_hotspot_level = v.hotspot_level
            rec.gate_feasibility = v.feasibility
            rec.gate_confidence_level = v.confidence_level
            rec.funcrank_static = v.funcrank_static
            rec.funcrank_quantile = v.funcrank_quantile
            rec.direct_runtime_share_pct = v.direct_runtime_share_pct
            rec.dependency_roots = list(v.dependency_roots or [])
            rec.blocked_roots = list(v.blocked_roots or [])
            rec.translation_unit = v.translation_unit
            if labels[name] != LABEL_CANDIDATE:
                rec.set_reason(
                    outcomes.GATE_SKIPPED,
                    f"Gate: {v.decision} (hotspot={v.hotspot_level}, "
                    f"feasibility={v.feasibility}, confidence={v.confidence_level}) "
                    f"-- {v.decision_reason}",
                )
        candidates = [n for n in functions if labels.get(n) == LABEL_CANDIDATE]
        summary["stages"]["stage2"] = {
            "labels": labels,
            "candidates": candidates,
            "gate3": summarize(verdicts, graph_conf),
        }
    else:
        labels = {}
        candidates = list(functions)
        verdicts = {}
        logger.info("STAGE 2 | Decision Gate TẮT -- mọi hotspot là candidate.")
        summary["stages"]["stage2"] = {"labels": labels, "candidates": candidates}

    # ------------------------------------------ AUDIT_RUN4_v2 mục 5, điểm 1
    # Công cụ QUAN SÁT -- KHÔNG đổi quyết định Gate hay outcome hotspot nào.
    # Bọc try/except NGOÀI: lỗi export tuyệt đối không được làm sập pipeline
    # hay che lỗi gốc.
    try:
        from audit.graph_snapshot import write_snapshot

        write_snapshot(
            graph, results_dir=results_dir, label=label, verdicts=verdicts,
            excluded_ids=excluded_ids, build_mode=build_mode, gate_enabled=gate_enabled,
        )
    except Exception:  # noqa: BLE001 -- quan sát, không được phép làm hỏng lượt chạy
        logger.exception("audit.graph_snapshot.write_snapshot lỗi -- bỏ qua, KHÔNG dừng pipeline.")

    # ------------------------------------------------------- PHA B (specs)
    _banner("PHA B", "Đổi đường dẫn file -> module:qualname")
    specs, unresolved = build_hotspot_specs(functions, graph, work_dir)
    for name, why in unresolved.items():
        if name in records:
            records[name].set_reason(outcomes.UNRESOLVABLE_IMPORT, why)
    n_ambiguous = 0
    for spec in specs:
        rec = records[spec.function_name]
        rec.module_target = spec.target
        rec.ambiguous_name = spec.ambiguous_name
        rec.ambiguity_detail = spec.ambiguity_detail
        rec.n_name_matches = spec.n_name_matches
        if spec.ambiguous_name:
            n_ambiguous += 1
    if n_ambiguous:
        logger.warning(
            "PHA 2 | %d/%d hotspot có tên TRÙNG không phân biệt được "
            "(AMBIGUOUS_NAME) -- số liệu của chúng cần đọc kèm cảnh báo.",
            n_ambiguous, len(specs),
        )
    if not specs:
        return finish(outcomes.NO_MEASURABLE_HOTSPOT,
                      "không hotspot nào đổi được thành module:qualname")

    # --------------------------------------------- PHA A (chạy test gốc)
    _banner("PHA A", "Chạy BỘ TEST GỐC của repo + plugin ghi đối số thật")
    capture_dir = Path(work_dir) / ".rtb_capture"
    import os

    baseline = repo_runner.run_pytest(
        venv_py, work_dir, label="baseline",
        plugin_args=["-p", "stage1_profiling.capture_plugin"],
        extra_env={
            "PYTHONPATH": os.pathsep.join(
                [str(Path(benchmark_root).resolve()), str(Path(work_dir).resolve())]
            ),
            "RTB_CAPTURE_TARGETS": json.dumps([
                {"function_name": s.function_name, "module": s.module,
                 "qualname": s.qualname, "import_root": s.import_root}
                for s in specs
            ]),
            "RTB_CAPTURE_OUT": str(capture_dir),
            "RTB_CAPTURE_MAX_CALLS": str(int(ro_cfg.get("max_captured_calls", 20))),
            "RTB_CAPTURE_MAX_ARG_BYTES": str(int(ro_cfg.get("max_arg_bytes", 5 * 1024 * 1024))),
        },
        timeout_sec=test_timeout, results_dir=capture_dir,
    )
    summary["stages"]["baseline_tests"] = baseline.as_dict()

    failed, why = repo_runner.baseline_failed(baseline)
    if failed:
        # Repo bị LOẠI khỏi so sánh. Lỗi này KHÔNG được tính cho hybrid.
        for rec in records.values():
            rec.set_reason(
                outcomes.NOT_COVERED_BY_TESTS,
                "repo bị loại vì baseline Python đã fail sẵn -- không đánh giá được bản dịch",
            )
        return finish(outcomes.BASELINE_FAILED, why)

    if "TIMEOUT" in (baseline.error or ""):
        return finish(outcomes.TIMEOUT, baseline.error)

    # ------------------------------------------ PHA B (phát lại + phân tầng)
    _banner("PHA B", "Phát lại 2 lần trên bản Python + phân tầng kiểu")
    corr_cfg = cfg.get("correctness") or {}
    rtol = float(corr_cfg.get("rtol", 1e-5))
    atol = float(corr_cfg.get("atol", 1e-8))

    verdicts, err = run_replay_checks(
        venv_py, work_dir, capture_dir, benchmark_root, rtol=rtol, atol=atol,
    )
    if err:
        return finish(outcomes.NO_MEASURABLE_HOTSPOT, f"phát lại thất bại: {err}")

    # mục B6: tra CHỈ khi cần (NOT_COVERED_BY_TESTS) -- tránh quét toàn graph
    # mỗi hotspot khi không dùng tới. capture_plugin.py chạy trong subprocess
    # riêng (venv của repo) nên KHÔNG có `graph` ở đó; nơi DUY NHẤT có cả
    # reason lẫn graph để ghép là đây.
    _fn_by_name: dict[str, object] | None = None

    def _lookup_function_node(fn_name: str):
        nonlocal _fn_by_name
        if _fn_by_name is None:
            _fn_by_name = {}
            for fid, fnode in graph.functions.items():
                _fn_by_name.setdefault(fnode.name, fnode)
        return _fn_by_name.get(fn_name)

    for name, v in verdicts.items():
        rec = records.get(name)
        if rec is None:
            continue
        rec.tier = v.tier
        rec.tier_reason = v.tier_reason
        rec.n_captured_calls = v.n_calls
        rec.n_distinct_inputs = v.n_distinct_inputs
        rec.nondeterministic_shadow_pil_recheck = v.nondeterministic_shadow_pil_recheck
        rec.observed_arg_types = v.observed_arg_types
        rec.observed_kwarg_types = v.observed_kwarg_types
        if v.reason:
            rec.set_reason(v.reason, v.detail)
            if v.reason == outcomes.NOT_COVERED_BY_TESTS:
                fnode = _lookup_function_node(name)
                if fnode is not None:
                    rec.not_covered_n_lines = fnode.lineno_end - fnode.lineno_start + 1
                    callee_ids = {
                        e.callee for e in graph.call_edges if e.caller == fnode.id
                    }
                    rec.not_covered_dependencies = sorted(
                        graph.functions[cid].name for cid in callee_ids if cid in graph.functions
                    )
        elif v.tier not in (TIER_NATIVE, TIER_KERNEL):
            rec.set_reason(outcomes.UNSUPPORTED_KIND, v.tier_reason or "không phân tầng được")
    summary["stages"]["replay"] = {n: v.as_dict() for n, v in verdicts.items()}

    # --- PHẦN 1.1: lọc pool rồi CẮT top_k_translate theo THỨ TỰ FUNCRANK ---
    rank_of = {name: i for i, name in enumerate(funcrank_order)}
    survivors = [
        s for s in specs
        if not records[s.function_name].reason and s.function_name in candidates
    ]
    survivors.sort(key=lambda s: rank_of.get(s.function_name, 10**6))

    top_k_translate = int(graph_cfg.get("top_k_translate", 5))
    eligible = survivors[:top_k_translate]
    over_quota = survivors[top_k_translate:]
    for s in over_quota:
        # KHÔNG phải thất bại: hotspot hợp lệ nhưng xếp sau hạn mức. Ghi lý do
        # riêng để bảng phễu không tính nhầm thành lỗi kỹ thuật.
        records[s.function_name].set_reason(
            outcomes.EXCLUDED_BY_TOP_K,
            f"hợp lệ nhưng xếp hạng FuncRank #{rank_of.get(s.function_name, '?') + 1}, "
            f"ngoài hạn mức top_k_translate={top_k_translate}",
        )

    logger.info(
        "PHẦN 1.1 | pool=%d -> qua được ghi/phát lại + phân tầng: %d -> "
        "dịch %d hotspot đầu theo FuncRank: %s",
        len(specs), len(survivors), len(eligible),
        [s.function_name for s in eligible] or "(không có)",
    )
    # Log lý do loại TỪNG hotspot (yêu cầu 1.1).
    for name in funcrank_order:
        rec = records.get(name)
        if rec is None or not rec.reason:
            continue
        logger.info(
            "PHẦN 1.1 | loại '%s' (FuncRank #%d): %s -- %s",
            name, rank_of.get(name, -1) + 1, rec.reason, (rec.detail or "")[:160],
        )
    summary["stages"]["hotspot_selection"] = {
        "candidate_pool": len(specs),
        "n_survivors": len(survivors),
        "top_k_translate": top_k_translate,
        "selected": [s.function_name for s in eligible],
        "over_quota": [s.function_name for s in over_quota],
        "dropped": {
            name: {"funcrank_position": rank_of.get(name, -1) + 1,
                   "reason": rec.reason, "detail": (rec.detail or "")[:300]}
            for name, rec in records.items() if rec.reason
        },
    }
    if not eligible:
        return finish(
            outcomes.NO_MEASURABLE_HOTSPOT,
            "không hotspot nào vượt qua được bước ghi/phát lại đối số",
        )
    if over_budget():
        return finish(outcomes.TIMEOUT, "vượt repo_time_budget_sec sau Pha B")

    # =====================================================================
    # PHẦN 2 -- TỪ ĐÂY LÀ PHẦN CHẠY THEO NHÁNH ABLATION.
    #
    # Mọi thứ phía trên (copy, venv, Stage 0, gate, ghi đối số, phát lại,
    # chọn hotspot) dùng CHUNG cho cả hai nhánh: chúng không phụ thuộc prompt,
    # nên chạy lại chỉ thêm nhiễu và tốn giờ GPU. Đây chính là yêu cầu 2.3.
    # =====================================================================
    import ablation as ab
    import copy as _copy_mod

    ablation_on = ab.is_enabled(cfg)
    arm_names = ab.arms(cfg) if ablation_on else [""]
    if ablation_on:
        # max_rounds = 1 cho CẢ HAI nhánh: đa vòng sẽ trộn tác động của vòng
        # tối ưu vào tác động của context, không tách ra được nữa (yêu cầu 2.1).
        cfg = _copy_mod.deepcopy(cfg)
        cfg.setdefault("optimization_loop", {})["max_rounds"] = 1
        logger.info(
            "PHẦN 2 | ABLATION BẬT -- nhánh: %s, repeats=%d, seed gốc=%s, "
            "temperature=%s, optimization_loop.max_rounds bị ép về 1.",
            arm_names, int((cfg.get("ablation") or {}).get("repeats", 1)),
            (cfg.get("ablation") or {}).get("seed"),
            (cfg.get("ablation") or {}).get("temperature"),
        )

    arm_outputs: dict[str, dict] = {}
    base_records = {n: _copy_mod.deepcopy(r) for n, r in records.items()}

    for arm in arm_names:
        # Mỗi nhánh có BẢN SAO RIÊNG của records: nhánh sau không được thấy
        # lý do/kết quả nhánh trước, nếu không hai nhánh không còn độc lập.
        arm_records = {n: _copy_mod.deepcopy(r) for n, r in base_records.items()}
        for r in arm_records.values():
            r.arm = arm
        arm_summary: dict = {"stages": {}}

        # --- PHA 5: nhánh NHIỄU NỀN chỉ chạy trên tối đa N hotspot ---------
        # Mỗi hotspot là một lời gọi LLM nữa; chạy hết cả dataset sẽ nhân đôi
        # hoá đơn GPU để đo một thứ (mức nhiễu) mà vài hotspot là đủ.
        arm_eligible = eligible
        if arm == ab.ARM_GRAPH_REPEAT:
            limit = ab.noise_floor_max_hotspots(cfg)
            arm_eligible = eligible[:limit]
            logger.info(
                "PHA 5 | nhánh nhiễu nền '%s': chạy lại %d/%d hotspot (giới hạn "
                "noise_floor_max_hotspots=%d). KHÔNG chạy lại baseline/capture.",
                arm, len(arm_eligible), len(eligible), limit,
            )
            arm_summary["stages"]["noise_floor"] = {
                "n_hotspots": len(arm_eligible),
                "limit": limit,
                "note": (
                    "Lượt sinh ĐỘC LẬP thứ hai của CHÍNH nhánh graph: cùng prompt, "
                    "seed khác. Dùng để đo mức bất đồng do ngẫu nhiên của LLM."
                ),
            }

        if arm:
            _banner("ABLATION", f"NHÁNH '{arm}' (context graph: "
                                f"{'CÓ' if ab.includes_graph_context(arm) else 'KHÔNG'})")
            # CÁCH LY (yêu cầu 2.4): maturin cài extension theo TÊN MODULE, và
            # tên module giống nhau ở hai nhánh (cố ý -- nó nằm trong prompt).
            # Nên trước mỗi nhánh phải GỠ CÀI bản của nhánh trước, nếu không
            # nhánh sau sẽ import trúng .so của nhánh trước và kết quả so sánh
            # thành vô nghĩa.
            removed = repo_runner.uninstall_extensions(
                venv_py,
                [crate_builder.ext_module_name(s.function_name) for s in eligible]
                + [crate_builder.shim_module_name(s.function_name) for s in eligible],
                work_dir,
            )
            arm_summary["stages"]["isolation"] = {"uninstalled": removed}

        # --- PHẦN 3.4: RESUME ở mức (repo, arm) --------------------------
        # Lượt chạy trên máy thuê có thể bị cắt giữa đường (hết giờ, mất mạng).
        # Mỗi nhánh xong được ghi ngay ra đĩa; lần chạy sau nạp lại thay vì gọi
        # LLM lần nữa -- đó là phần tốn tiền nhất.
        cache_path = _arm_cache_path(cfg, label, arm)
        if cache_path is not None and cache_path.exists() and cfg.get("_resume"):
            cached = _load_arm_cache(cache_path, arm_records)
            if cached is not None:
                logger.info(
                    "RESUME | dùng lại kết quả nhánh '%s' của repo '%s' từ %s "
                    "(không gọi lại LLM).", arm or "đơn", label, cache_path.name,
                )
                arm_outputs[arm] = cached
                continue
            logger.warning(
                "RESUME | %s có nhưng không đọc được -- chạy lại nhánh này.",
                cache_path.name,
            )

        out = _run_one_arm(
            cfg=cfg, arm=arm, eligible=arm_eligible, records=arm_records, graph=graph,
            benchmark_root=benchmark_root, work_dir=work_dir, venv_py=venv_py,
            capture_dir=capture_dir, verdicts=verdicts, baseline=baseline,
            ro_cfg=ro_cfg, rtol=rtol, atol=atol, warmup=warmup,
            iterations=iterations, test_timeout=test_timeout, label=label,
            over_budget=over_budget, arm_summary=arm_summary,
        )
        arm_outputs[arm] = out
        if cache_path is not None:
            _save_arm_cache(cache_path, out)

    # --- Nhánh CHÍNH để lên bảng/phễu của repo -----------------------------
    # Khi có ablation, nhánh `graph` là cấu hình thật của hệ thống, nên nó là
    # nhánh đại diện trong bảng kết quả; nhánh `none` chỉ tồn tại để so sánh.
    primary = ab.ARM_GRAPH if (ablation_on and ab.ARM_GRAPH in arm_outputs) else arm_names[0]
    main = arm_outputs[primary]
    records.clear()
    records.update(main["records"])
    summary["stages"].update(main["summary"]["stages"])
    summary["primary_arm"] = primary
    if ablation_on:
        summary["arms"] = {
            arm: {
                "hotspots": [r.as_dict() for r in out["records"].values()],
                "stages": out["summary"]["stages"],
            }
            for arm, out in arm_outputs.items()
        }
        summary["ablation_arm_results"] = {
            arm: {n: vars(ar) for n, ar in out["arm_results"].items()}
            for arm, out in arm_outputs.items()
        }

    hybrid_tests = main["hybrid_tests"]

    # -------------------------------------------------------------- PHA 0
    for rec in records.values():
        if not rec.reason:
            rec.reason = outcomes.MEASURED
    # --- PHẦN 1.2: phễu của repo này, mỗi bước có mẫu số rõ ---
    # Dựng phễu TRƯỚC khi chốt status: status cần biết đã sinh được code chưa
    # để phân biệt ALL_HOTSPOTS_FAILED_COMPILE với NO_MEASURABLE_HOTSPOT.
    import funnel as funnel_mod

    rf = funnel_mod.build_repo_funnel(label, baseline.ok and baseline.n_passed > 0, records)
    summary["funnel"] = rf.as_dict()

    reasons = [r.reason for r in records.values()]
    status = outcomes.decide_repo_status(
        reasons,
        n_generated=rf.count("generated"),
        n_compiled=rf.count("compiled"),
    )
    summary["metrics"] = _repo_metrics(records, baseline, hybrid_tests)
    return finish(status)


def _run_one_arm(
    *, cfg, arm, eligible, records, graph, benchmark_root, work_dir, venv_py,
    capture_dir, verdicts, baseline, ro_cfg, rtol, atol, warmup, iterations,
    test_timeout, label, over_budget, arm_summary,
):
    """Chạy Stage 3/4 → 5 → D → so khớp → Pha E → Pha F cho MỘT nhánh ablation.

    Trả về dict {records, summary, hybrid_tests, arm_results}. KHÔNG raise, và
    KHÔNG gọi `finish`: một nhánh thất bại không được kết thúc cả repo -- nhánh
    kia vẫn phải chạy để còn dữ liệu so sánh.
    """
    import ablation as ab
    from stage1_profiling.replay import run_replay_checks
    from stage5_compiler_in_the_loop import crate_builder

    ab_cfg = cfg.get("ablation") or {}
    temperature = ab_cfg.get("temperature") if arm else None
    repeat = 0
    seeds = {
        s.function_name: ab.seed_for(
            int(ab_cfg.get("seed", 1234)), label, s.function_name, arm, repeat
        ) if arm else None
        for s in eligible
    }
    prompt_dir = Path(work_dir) / ".rtb_prompts"

    def _empty(reason_note: str) -> dict:
        arm_summary.setdefault("stages", {})["note"] = reason_note
        return {
            "records": records, "summary": arm_summary, "hybrid_tests": None,
            "arm_results": _collect_arm_results(arm, records),
        }

    # --------------------------------------------------------- STAGE 3 + 4
    rust_by_function, stage34, agents = _run_stage34(
        cfg, eligible, records, graph, benchmark_root,
        arm=arm, verdicts=verdicts, seeds=seeds, temperature=temperature,
        prompt_dir=prompt_dir,
    )
    arm_summary["stages"]["stage3_4"] = stage34
    if not rust_by_function:
        return _empty("Stage 4 không sinh được code Rust cho hotspot nào")

    # ------------------------------------------------------------- STAGE 5
    tiers = {name: records[name].tier for name in rust_by_function}
    plan = crate_builder.plan_by_tier(
        tiers, str(ro_cfg.get("signature_support", crate_builder.SIGNATURE_EXTENDED))
    )
    for name, why in plan.unsupported.items():
        records[name].set_reason(outcomes.UNSUPPORTED_KIND, why)
        rust_by_function.pop(name, None)

    arm_summary["stages"]["stage5"] = _run_stage5(
        cfg, plan, rust_by_function, tiers, records, work_dir, agents, arm=arm,
    )

    # -------------------------------------------------------------- PHA D
    _banner("PHA D", f"maturin develop vào venv của repo (nhánh '{arm or 'đơn'}')")
    crates = crate_builder.build_all(
        plan=plan, rust_by_function=rust_by_function, tiers=tiers,
        work_dir=work_dir, venv_python=venv_py,
        timeout_sec=int((cfg.get("optimization_loop") or {}).get("build_timeout_sec", 600)),
        arm=arm,
    )
    built_extensions: dict[str, dict] = {}
    for name, crate in crates.items():
        records[name].build_status = crate.status
        if crate.ok:
            built_extensions[name] = crate.version_targets()
        else:
            records[name].set_reason(
                outcomes.BUILD_FAILED, f"{crate.status}: {(crate.output or '')[:300]}",
            )
    arm_summary["stages"]["phase_d"] = {n: c.as_dict() for n, c in crates.items()}

    if not built_extensions:
        return _empty("không build được extension Rust nào (xem stages.phase_d)")

    # ----------------------------------------- registry ĐỘNG (PHA C) + PHA B
    from versions.registry import build_dynamic_registry

    registry = build_dynamic_registry(eligible, built_extensions)
    arm_summary["stages"]["registry"] = registry.as_dict()

    _banner("PHA B/E", "So khớp Rust vs Python trên ĐỐI SỐ THẬT (cả 2 phiên bản)")
    verdicts2, err = run_replay_checks(
        venv_py, work_dir, capture_dir, benchmark_root, rtol=rtol, atol=atol,
        rust_targets=registry.rust_targets_payload(),
    )
    if err:
        logger.error("So khớp thất bại: %s", err)
    for name, v in verdicts2.items():
        rec = records.get(name)
        if rec is None or not v.correctness:
            continue
        rec.correctness = {
            version: {"status": e.get("status"), "detail": e.get("detail", ""),
                      "n_matched": e.get("n_matched"), "n_samples": e.get("n_samples"),
                      "rtol": rtol, "atol": atol,
                      # LẦN CHẠY CHẨN ĐOÁN (mục C): trước đây chỉ giữ `detail`
                      # (lời gọi MISMATCH ĐẦU TIÊN) -- `mismatches` giữ tới 10
                      # lời gọi lệch (stage1_profiling/_replay_runner.py), cần
                      # đủ để quét offline xem lệch có theo QUY LUẬT không
                      # (vd luôn lệch ở kiểu float) hay ngẫu nhiên.
                      "mismatches": e.get("mismatches", [])}
            for version, e in v.correctness.items()
        }
        rec.shadow_mutation = v.shadow_mutation
        if v.reason == outcomes.CORRECTNESS_FAILED:
            rec.set_reason(outcomes.CORRECTNESS_FAILED, v.detail)

    # -------------------------------------------------------------- PHA E
    hybrid_tests = _run_phase_e(
        cfg, records, registry, venv_py, work_dir, benchmark_root,
        capture_dir, baseline, test_timeout, arm_summary,
    )

    # -------------------------------------------------------------- PHA F
    if over_budget():
        for r in records.values():
            r.set_reason(outcomes.MEASURE_FAILED, "vượt repo_time_budget_sec trước Pha F")
    else:
        _run_phase_f(
            cfg=cfg, records=records, registry=registry, venv_py=venv_py,
            work_dir=work_dir, capture_dir=capture_dir, benchmark_root=benchmark_root,
            warmup=warmup, iterations=iterations, summary=arm_summary,
            crates=crates, rust_by_function=rust_by_function, agents=agents,
        )

    return {
        "records": records, "summary": arm_summary, "hybrid_tests": hybrid_tests,
        "arm_results": _collect_arm_results(arm, records),
    }


def _collect_arm_results(arm: str, records: dict) -> dict:
    """Đổi `HotspotRecord` thành `ablation.ArmResult` để so sánh theo cặp."""
    import ablation as ab
    import funnel as funnel_mod

    out: dict = {}
    for name, rec in records.items():
        out[name] = ab.ArmResult(
            arm=arm,
            flags=funnel_mod.funnel_from_record(rec),
            reason=rec.reason or outcomes.MEASURED,
            prompt_tokens=rec.prompt_tokens,
            truncated=rec.truncated,
            fix_rounds=rec.fix_rounds,
            llm_seconds=rec.llm_seconds,
            vacuous=rec.vacuous,
            speedup=dict(rec.accepted_speedup),
        )
    return out


# ---------------------------------------------------------------------------
def _run_stage34(
    cfg, eligible, records, graph, benchmark_root,
    arm: str = "", verdicts: dict | None = None, seeds: dict | None = None,
    temperature: float | None = None, prompt_dir=None,
) -> tuple[dict, dict, dict]:
    """Stage 3 (đóng gói context) + Stage 4 (Generator Agent sinh Rust).

    Điểm MỚI của Pha D ở đây: prompt mang theo CHỮ KÝ THẬT (kiểu quan sát được
    từ đối số mà bộ test truyền vào) và TẦNG đã phân loại bằng code -- thay cho
    chữ ký ảnh `Vec<u8>` + width/height cứng của chế độ legacy.
    """
    from stage3_context_packaging.packager import package_context_for
    from stage4_llm_transpile.generator_agent import GeneratorAgent
    from stage4_llm_transpile.model_backend import (
        GENERATOR_ROLE,
        ModelBackendError,
        get_model_backend,
        resolve_model_for_role,
        resolve_num_ctx_for_role,
    )
    import ablation as _ab
    from stage5_compiler_in_the_loop.crate_builder import ext_module_name

    _banner(
        "STAGE 3/4",
        "Đóng gói context + sinh Rust theo CHỮ KÝ THẬT"
        + (f" (nhánh ablation '{arm}', context graph: "
           f"{'CÓ' if _ab.includes_graph_context(arm) else 'KHÔNG'})" if arm else ""),
    )
    stage34: dict = {"results": [], "agents": {}}

    llm_cfg = cfg.get("llm") or {}
    if not llm_cfg.get("enabled", False):
        for spec in eligible:
            records[spec.function_name].set_reason(
                outcomes.LLM_FAILED,
                "llm.enabled=false -- chế độ repo động cần LLM để sinh Rust cho "
                "chữ ký bất kỳ (chế độ legacy dùng code viết tay thì không cần)",
            )
        stage34["skipped"] = "llm.enabled=false"
        return {}, stage34, {}

    try:
        backend = get_model_backend(cfg)
    except ModelBackendError as exc:
        for spec in eligible:
            records[spec.function_name].set_reason(
                outcomes.LLM_FAILED, f"không khởi tạo được backend: {exc}")
        stage34["skipped"] = f"backend lỗi: {exc}"
        return {}, stage34, {}

    model = resolve_model_for_role(cfg, GENERATOR_ROLE)
    num_ctx = resolve_num_ctx_for_role(cfg, GENERATOR_ROLE)
    logger.info("Generator Agent: model '%s' (num_ctx=%s).", model, num_ctx or "(mặc định)")

    profile_data = getattr(graph, "_dynamic_profile", None)
    rust_by_function: dict[str, dict] = {}
    agents: dict[str, object] = {}

    for spec in eligible:
        name = spec.function_name
        rec = records[name]
        context = package_context_for(name, graph, profile_data)
        agent = GeneratorAgent(
            backend, model=model, num_ctx=num_ctx, prompt_dir=prompt_dir, arm=arm,
        )
        agents[name] = agent
        verdict = (verdicts or {}).get(name)
        result = agent.generate_rust(
            name, context,
            signature={
                "tier": rec.tier,
                "observed_arg_types": rec.observed_arg_types,
                "observed_kwarg_types": rec.observed_kwarg_types,
                "ext_module": ext_module_name(name),
                # Ví dụ vào/ra có ở CẢ HAI nhánh ablation -- đó là đặc tả tối
                # thiểu để viết chữ ký PyO3, không phải context graph.
                "io_examples": getattr(verdict, "io_examples", None) or [],
            },
            include_graph_context=_ab.includes_graph_context(arm) if arm else True,
            temperature=temperature,
            seed=(seeds or {}).get(name),
        )
        # Số liệu cho ablation: độ dài prompt, có bị cắt không, thời gian LLM.
        rec.prompt_tokens = result.get("prompt_tokens")
        rec.truncated = bool(result.get("truncated"))
        rec.llm_seconds = result.get("llm_seconds")
        if rec.truncated:
            # CONFOUNDED: prompt bị Ollama cắt -> hotspot này không dùng để so
            # sánh hai nhánh được (nhánh graph dài hơn nên dễ bị cắt hơn).
            rec.confounded = True
            logger.warning(
                "PHẦN 2.5 | '%s' (nhánh %s): prompt ~%s token VƯỢT num_ctx -> "
                "CONFOUNDED, tách khỏi phép so sánh chính.",
                name, arm or "đơn", result.get("prompt_tokens"),
            )
        stage34["results"].append({
            k: v for k, v in result.items() if k != "rust_code"
        })
        if not result.get("ok"):
            rec.set_reason(outcomes.LLM_FAILED, str(result.get("error", "?")))
            continue
        if rec.tier == TIER_KERNEL and not result.get("python_shim"):
            rec.set_reason(
                outcomes.LLM_FAILED,
                "hotspot ở Tầng 2 nhưng response không có khối `## Python shim` "
                "-- không có gì để gọi kernel Rust",
            )
            continue
        rust_by_function[name] = result

    stage34["agents"] = {"generator_model": model, "generator_num_ctx": num_ctx}
    stage34["arm"] = arm
    stage34["include_graph_context"] = _ab.includes_graph_context(arm) if arm else True
    stage34["temperature"] = temperature
    stage34["seeds"] = seeds or {}
    # `agents` trả RIÊNG, không nhét vào `stage34`: dict đó được ghi thẳng ra
    # JSON kết quả, mà GeneratorAgent không serialize được.
    return rust_by_function, stage34, agents


def _run_stage5(cfg, plan, rust_by_function, tiers, records, work_dir, agents,
                arm: str = "") -> dict:
    """Stage 5 -- `cargo check` trên crate RIÊNG của từng hotspot + vòng sửa lỗi.

    Dùng lại nguyên `loop_runner.run_compile_loop_for` (yêu cầu Pha D: "vòng
    sửa lỗi biên dịch dùng lại cơ chế hiện có"), chỉ khác `crate_dir` giờ là
    crate riêng của hotspot thay vì một crate nháp dùng chung.
    """
    from stage5_compiler_in_the_loop.compiler_loop import cargo_available
    from stage5_compiler_in_the_loop.crate_builder import make_crate
    from stage5_compiler_in_the_loop.loop_runner import compute_metrics, run_compile_loop_for

    cl_cfg = cfg.get("compiler_loop") or {}
    if not cl_cfg.get("enabled", True):
        _banner("STAGE 5", "BỎ QUA (compiler_loop.enabled=false)")
        return {"skipped": True, "reason": "compiler_loop.enabled=false"}
    if not cargo_available():
        _banner("STAGE 5", "BỎ QUA -- không có cargo trên máy này")
        return {"skipped": True, "reason": "không thấy cargo trong PATH"}

    _banner("STAGE 5", "cargo check + Generator Agent sửa lỗi (Pass@1 / DSR@1)")
    outcomes_list = []
    for name in list(plan.tier1) + list(plan.tier2):
        info = rust_by_function.get(name)
        if not info:
            continue
        crate = make_crate(
            function_name=name, rust_code=info.get("rust_code") or "",
            work_dir=work_dir, tier=tiers.get(name, ""),
            python_shim=info.get("python_shim") or "", arm=arm,
        )
        agent = agents.get(name)
        if agent is None:
            continue
        outcome = run_compile_loop_for(
            function_name=name, rust_code=info.get("rust_code") or "",
            generator_agent=agent, crate_dir=Path(crate.crate_dir),
            max_retries=int(cl_cfg.get("max_retries", 3)),
            cargo_timeout_sec=int(cl_cfg.get("cargo_timeout_sec", 300)),
        )
        outcomes_list.append(outcome)
        rec = records[name]
        rec.compiled = outcome.compiled
        rec.pass_at_1 = None if outcome.skipped else outcome.passed_first_try
        rec.compile_attempts = outcome.attempts
        rec.fix_rounds = outcome.fix_rounds
        rec.error_classes = list(outcome.error_classes)
        rec.compiled_at_round = outcome.compiled_at_round
        if outcome.compiled and outcome.final_code:
            # LẦN CHẠY CHẨN ĐOÁN (mục B1) -- CHẾ ĐỘ BÓNG: chỉ đo, không đổi
            # rec.compiled hay reason nào ở trên/dưới đoạn này.
            try:
                from stage5_compiler_in_the_loop.shadow_hardcoding import (
                    detect_static_hardcoding,
                )

                rec.shadow_hardcoding = detect_static_hardcoding(outcome.final_code).as_dict()
            except Exception:  # noqa: BLE001 -- quan sát, không được làm hỏng Stage 5
                logger.exception(
                    "Stage 5 [%s]: shadow_hardcoding lỗi -- bỏ qua, KHÔNG đổi kết quả.", name,
                )
        if not outcome.compiled and not outcome.skipped:
            rec.set_reason(
                outcomes.COMPILE_FAILED,
                f"lỗi cuối: {outcome.error_classes[-1] if outcome.error_classes else '?'} "
                f"-- {(outcome.final_error or '')[:300]}",
            )
            rust_by_function.pop(name, None)

    from dataclasses import asdict

    return {
        "metrics": compute_metrics(outcomes_list),
        "outcomes": [asdict(o) for o in outcomes_list],
    }


def _run_phase_e(
    cfg, records, registry, venv_py, work_dir, benchmark_root,
    capture_dir, baseline, test_timeout, summary,
):
    """PHA E -- chạy lại bộ test của repo với hotspot ĐÃ THAY bằng Rust."""
    import os

    from stage5_compiler_in_the_loop import repo_runner

    _banner("PHA E", "Chạy lại BỘ TEST của repo với hotspot thay bằng Rust")

    swap_specs = []
    for name, per_version in registry.targets.items():
        if records[name].reason:
            continue  # hotspot đã bị loại -> không thay vào repo
        target = per_version.get("hybrid_pyo3")
        py_target = per_version.get("python_pure")
        if target is None or py_target is None:
            continue
        swap_specs.append({
            "function_name": name,
            "module": py_target.module,
            "qualname": py_target.qualname,
            "import_root": py_target.import_root,
            "ext_module": target.module,
            "ext_func": target.qualname,
            "ext_root": target.import_root,
        })

    if not swap_specs:
        logger.warning("PHA E | không hotspot nào còn hợp lệ để hoán đổi -- bỏ qua.")
        summary["stages"]["hybrid_tests"] = {
            "skipped": True, "reason": "không có hotspot hợp lệ để hoán đổi"}
        return None

    hybrid = repo_runner.run_pytest(
        venv_py, work_dir, label="hybrid",
        plugin_args=["-p", "stage1_profiling.capture_plugin"],
        extra_env={
            "PYTHONPATH": os.pathsep.join(
                [str(Path(benchmark_root).resolve()), str(Path(work_dir).resolve())]
            ),
            "RTB_SWAP_TARGETS": json.dumps(swap_specs),
            "RTB_CAPTURE_OUT": str(capture_dir),
        },
        timeout_sec=test_timeout, results_dir=capture_dir,
    )
    reg_free = repo_runner.regression_free(baseline, hybrid)
    breakdown = repo_runner.regression_breakdown(baseline, hybrid)
    summary["stages"]["hybrid_tests"] = {
        **hybrid.as_dict(),
        "regression_free": reg_free,
        # Tách "chuyển sang FAIL" (hồi quy thật do bản dịch) khỏi "bị SKIP"
        # (thường là điều kiện môi trường / skipif của repo).
        "regression_breakdown": breakdown,
        "n_swapped": len(swap_specs),
        "swapped": [s["function_name"] for s in swap_specs],
    }
    swap_report_path = Path(capture_dir) / "swap_report.json"
    swap_report: dict = {}
    if swap_report_path.exists():
        try:
            swap_report = json.loads(swap_report_path.read_text(encoding="utf-8"))
            summary["stages"]["hybrid_tests"]["swap_report"] = swap_report
        except (OSError, json.JSONDecodeError):
            pass

    # --- PHẦN 1.3: gắn VACUOUS cho hotspot mà bộ test không hề gọi tới -------
    # Test xanh mà hàm Rust chưa từng chạy thì lượt hybrid KHÔNG chứng minh
    # điều gì về bản Rust. Đây là cái bẫy dễ làm số liệu regression-free trông
    # đẹp một cách giả tạo, nên phải tách ra bằng con số đếm được.
    for spec in swap_specs:
        name = spec["function_name"]
        rec = records[name]
        info = swap_report.get(name) or {}
        rec.rust_call_count = info.get("rust_call_count")
        rec.vacuous = rec.rust_call_count == 0
        # Đóng góp vào regression-free: chỉ tính khi lượt hybrid chạy được,
        # không mất test nào, VÀ hàm Rust thật sự được gọi.
        rec.regression_free_contrib = bool(reg_free) and not rec.vacuous
        if rec.vacuous:
            logger.error(
                "PHA E [%s]: VACUOUS -- đã thay bằng Rust nhưng bộ test gọi nó "
                "0 lần. Loại khỏi tỉ lệ regression-free (test xanh ở đây không "
                "chứng minh gì về bản Rust).", name,
            )
    n_vacuous = sum(1 for s in swap_specs if records[s["function_name"]].vacuous)
    summary["stages"]["hybrid_tests"]["n_vacuous"] = n_vacuous
    summary["stages"]["hybrid_tests"]["rust_call_counts"] = {
        s["function_name"]: records[s["function_name"]].rust_call_count
        for s in swap_specs
    }
    if n_vacuous:
        logger.warning(
            "PHA E | %d/%d hotspot VACUOUS -- tỉ lệ regression-free chỉ tính "
            "trên %d hotspot còn lại.",
            n_vacuous, len(swap_specs), len(swap_specs) - n_vacuous,
        )

    logger.info(
        "PHA E | hybrid: %d/%d test pass (baseline %d/%d), REGRESSION_FREE=%s",
        hybrid.n_passed, hybrid.n_total, baseline.n_passed, baseline.n_total, reg_free,
    )
    if reg_free is False:
        lost = sorted(set(baseline.passed_ids) - set(hybrid.passed_ids))[:10]
        n_fail = breakdown.get("n_lost_now_failing", 0)
        n_skip = breakdown.get("n_lost_now_skipped", 0)
        logger.error(
            "PHA E | HỒI QUY: %d test pass ở baseline nhưng không pass ở hybrid "
            "(%d chuyển sang FAIL, %d bị SKIP, %d biến mất). Ví dụ: %s",
            breakdown.get("n_lost", len(lost)), n_fail, n_skip,
            breakdown.get("n_lost_missing", 0), lost,
        )
        if n_fail == 0 and n_skip:
            logger.warning(
                "PHA E | Không test nào CHUYỂN SANG FAIL -- toàn bộ phần mất là "
                "test BỊ SKIP ở lượt hybrid. Đây thường là điều kiện môi trường "
                "hoặc `skipif` của repo, KHÔNG phải bản Rust sai. Xem "
                "regression_breakdown.regression_free_ignoring_skips."
            )
        summary["stages"]["hybrid_tests"]["regressed_tests"] = lost
    return hybrid


def _run_phase_f(
    *, cfg, records, registry, venv_py, work_dir, capture_dir, benchmark_root,
    warmup, iterations, summary, crates, rust_by_function, agents,
):
    """PHA F -- đo ghép cặp + vòng tối ưu tốc độ.

    KHÁC BẢN CŨ ở hai điểm cốt lõi:
      1. Mọi phiên bản được đo trong CÙNG 1 tiến trình, cùng đối số, cùng vòng
         lặp -> tỉ số hợp lệ ở MỌI vòng (không chỉ vòng 1).
      2. Số liệu báo cáo là của vòng ĐƯỢC DECISION AGENT CHẤP NHẬN cuối cùng
         (phiên bản sẽ thật sự dùng), không phải MAX qua các vòng.
    """
    from stage4_llm_transpile.decision_agent import decide_after_benchmark
    from stage5_compiler_in_the_loop import crate_builder
    from stage6_benchmark.measure_subprocess import measure_pair_in_repo_venv

    _banner("PHA F", "Đo python_pure + các bản Rust trong CÙNG 1 tiến trình")

    opt_cfg = cfg.get("optimization_loop") or {}
    max_rounds = int(opt_cfg.get("max_rounds", 3))
    build_timeout = int(opt_cfg.get("build_timeout_sec", 600))
    llm_cfg = cfg.get("llm") or {}
    num_agents = int(llm_cfg.get("num_agents", 1)) if llm_cfg.get("enabled") else 1

    decision_backend, decision_session = _make_decision_session(cfg, num_agents)
    agents = agents or {}

    active = [n for n, r in records.items() if not r.reason and registry.has(n, "python_pure")]
    if not active:
        logger.warning("PHA F | không hotspot nào còn hợp lệ để đo.")
        summary["stages"]["phase_f"] = {"skipped": True, "reason": "không có hotspot hợp lệ"}
        return

    rounds_log: list[dict] = []
    for round_index in range(1, max_rounds + 1):
        targets = {
            name: {v: t.as_dict() for v, t in registry.targets[name].items()}
            for name in active
        }
        measured, err = measure_pair_in_repo_venv(
            venv_python=venv_py, work_dir=work_dir, capture_dir=capture_dir,
            benchmark_root=benchmark_root, targets=targets,
            warmup=warmup, iterations=iterations,
        )
        if err:
            for name in active:
                records[name].set_reason(outcomes.MEASURE_FAILED, err)
            summary["stages"]["phase_f"] = {"error": err, "rounds": rounds_log}
            return

        continue_names: list[str] = []
        for name in list(active):
            rec = records[name]
            hs = (measured.get("hotspots") or {}).get(name) or {}
            if hs.get("error"):
                rec.set_reason(outcomes.MEASURE_FAILED, hs["error"])
                active.remove(name)
                continue
            if not rec.input_spec:
                rec.input_spec = hs.get("input_spec") or {}

            per_version = hs.get("versions") or {}
            base = (per_version.get("python_pure") or {}).get("durations")
            if not base:
                rec.set_reason(
                    outcomes.MEASURE_FAILED,
                    (per_version.get("python_pure") or {}).get("error")
                    or "không đo được baseline Python",
                )
                active.remove(name)
                continue

            speedups: dict[str, float] = {}
            stats: dict[str, dict] = {}
            for version, vres in per_version.items():
                durations = vres.get("durations")
                if not durations:
                    stats[version] = {"error": vres.get("error", "không có số đo")}
                    continue
                stats[version] = _stats(durations)
                m = _mean(durations)
                if m and m > 0:
                    speedups[version] = _mean(base) / m

            round_entry = {
                "round": round_index,
                "function": name,
                "build_status": records[name].build_status or "INITIAL",
                "stats_ms": {v: s for v, s in stats.items()},
                "speedup": {v: s for v, s in speedups.items() if v != "python_pure"},
            }

            rust_speedup = speedups.get("rust_pure") or speedups.get("hybrid_pyo3")
            decision = decide_after_benchmark(
                function_name=name,
                before_sec=_mean(base),
                after_sec=None if rust_speedup is None else _mean(base) / rust_speedup,
                num_agents=num_agents,
                backend=decision_backend,
                session=decision_session,
                round_index=round_index,
                max_rounds=max_rounds,
            )
            round_entry["decision"] = {
                "accepted": decision.accepted,
                "source": decision.source,
                "reason": decision.reason,
                "continue_optimizing": decision.continue_optimizing,
                "next_strategy": decision.next_strategy,
                "stopped_by_cap": decision.stopped_by_cap,
            }
            rec.rounds.append(round_entry)
            rounds_log.append(round_entry)
            rec.stopped_by_cap = decision.stopped_by_cap

            # Số liệu BÁO CÁO = vòng được ACCEPT gần nhất (phiên bản sẽ dùng thật).
            if decision.accepted:
                rec.accepted_round = round_index
                rec.accepted_speedup = dict(round_entry["speedup"])
                # LẦN CHẠY CHẨN ĐOÁN (mục B4) -- CHẾ ĐỘ BÓNG: chỉ ĐO, không đổi
                # quyết định accept. Decision Agent (LLM) có thể chấp nhận vì lý
                # do khác tốc độ (rule-based ACCEPT_THRESHOLD=1.0 thường chặn
                # trước, nhưng nhánh LLM có thể không).
                hp = rec.accepted_speedup.get("hybrid_pyo3")
                rec.hybrid_slower = hp is not None and hp < 1.0
            for version, sp in round_entry["speedup"].items():
                prev = rec.best_speedup.get(version)
                rec.best_speedup[version] = sp if prev is None else max(prev, sp)

            if decision.continue_optimizing and agents.get(name):
                continue_names.append(name)

        if not continue_names:
            break
        if round_index >= max_rounds:
            break

        # --- Sinh bản mới + build lại cho các hotspot muốn tối ưu tiếp ---
        still: list[str] = []
        for name in continue_names:
            rec = records[name]
            last = rec.rounds[-1]
            opt = agents[name].optimize_further(
                name, (last.get("decision") or {}).get("next_strategy"), round_index + 1
            )
            if not opt.get("ok"):
                logger.error("PHA F [%s]: không tối ưu tiếp được (%s) -- dừng.",
                             name, opt.get("error"))
                continue
            crate = crate_builder.make_crate(
                function_name=name, rust_code=opt.get("rust_code") or "",
                work_dir=work_dir, tier=rec.tier,
                python_shim=opt.get("python_shim") or rust_by_function.get(name, {}).get("python_shim") or "",
            )
            crate = crate_builder.build_crate(crate, venv_py, build_timeout)
            rec.build_status = crate.status
            if not crate.ok:
                logger.error(
                    "PHA F [%s]: build lại THẤT BẠI ở vòng %d -- GIỮ kết quả vòng %d, "
                    "KHÔNG báo speedup cho vòng lỗi. %s",
                    name, round_index + 1, round_index, (crate.output or "")[:200],
                )
                continue
            still.append(name)
        active = still
        if not active:
            break

    summary["stages"]["phase_f"] = {
        "max_rounds": max_rounds,
        "n_rounds_run": max(([r["round"] for r in rounds_log] or [0])),
        "rounds": rounds_log,
    }


def _make_decision_session(cfg, num_agents):
    """Session RIÊNG cho Decision Agent (tách hẳn khỏi Generator Agent)."""
    llm_cfg = cfg.get("llm") or {}
    if not (llm_cfg.get("enabled") and num_agents >= 2):
        return None, None
    try:
        from stage4_llm_transpile.agent_session import AgentSession
        from stage4_llm_transpile.decision_agent import DECISION_SYSTEM_PROMPT
        from stage4_llm_transpile.model_backend import (
            DECISION_ROLE,
            ModelBackendError,
            get_model_backend,
            resolve_model_for_role,
            resolve_num_ctx_for_role,
        )

        backend = get_model_backend(cfg)
        model = resolve_model_for_role(cfg, DECISION_ROLE)
        num_ctx = resolve_num_ctx_for_role(cfg, DECISION_ROLE)
        logger.info("Decision Agent: model '%s' (num_ctx=%s).", model, num_ctx or "(mặc định)")
        return backend, AgentSession(
            role_name="decision", system_prompt=DECISION_SYSTEM_PROMPT,
            backend=backend, model=model, num_ctx=num_ctx, think=False,
        )
    except ModelBackendError as exc:
        logger.error("Decision Agent: không có backend (%s) -- dùng rule.", exc)
        return None, None


def _stats(durations: list[float]) -> dict:
    """mean/median/std/n theo ms -- GHI VÀO FILE, không chỉ in ra stdout
    (lỗ hổng #8 trong AUDIT_REPORT.md)."""
    import statistics

    return {
        "mean_ms": statistics.mean(durations) * 1000,
        "median_ms": statistics.median(durations) * 1000,
        "std_ms": (statistics.pstdev(durations) if len(durations) > 1 else 0.0) * 1000,
        "n": len(durations),
    }


def _repo_metrics(records, baseline, hybrid) -> dict:
    """Số liệu tổng hợp MỨC REPO -- đúng các cột mà bảng cuối cần (Pha F)."""
    recs = list(records.values())
    measured = [r for r in recs if r.reason == outcomes.MEASURED]
    evaluated_compile = [r for r in recs if r.compiled is not None]

    def _corr_rate(version: str) -> float | None:
        judged = [r for r in recs if version in (r.correctness or {})]
        if not judged:
            return None
        return sum(
            1 for r in judged if r.correctness[version].get("status") == "MATCH"
        ) / len(judged)

    def _mean_speedup(version: str) -> float | None:
        vals = [r.accepted_speedup.get(version) for r in measured]
        vals = [v for v in vals if v is not None]
        return (sum(vals) / len(vals)) if vals else None

    from stage5_compiler_in_the_loop.repo_runner import (
        regression_breakdown,
        regression_free,
    )

    return {
        "n_hotspots": len(recs),
        "n_measured": len(measured),
        "reason_counts": {
            reason: sum(1 for r in recs if r.reason == reason)
            for reason in sorted({r.reason for r in recs if r.reason})
        },
        "compile_ok": (
            sum(1 for r in evaluated_compile if r.compiled) / len(evaluated_compile)
            if evaluated_compile else None
        ),
        "pass_at_1": (
            sum(1 for r in recs if r.pass_at_1) / len([r for r in recs if r.pass_at_1 is not None])
            if any(r.pass_at_1 is not None for r in recs) else None
        ),
        "correctness_match_rate": {
            "rust_pure": _corr_rate("rust_pure"),
            "hybrid_pyo3": _corr_rate("hybrid_pyo3"),
        },
        "baseline_tests": f"{baseline.n_passed}/{baseline.n_total}" if baseline.ok else None,
        "baseline_pass_rate": baseline.pass_rate,
        "hybrid_tests": (
            f"{hybrid.n_passed}/{hybrid.n_total}" if (hybrid is not None and hybrid.ok) else None
        ),
        "hybrid_pass_rate": hybrid.pass_rate if hybrid is not None else None,
        "regression_free": regression_free(baseline, hybrid) if hybrid is not None else None,
        "regression_breakdown": (
            regression_breakdown(baseline, hybrid) if hybrid is not None else None
        ),
        # PHẦN 1.3: tỉ lệ regression-free CHỈ tính trên hotspot không VACUOUS.
        # Mẫu số ghi kèm để người đọc biết đã loại bao nhiêu.
        "n_vacuous": sum(1 for r in recs if r.vacuous),
        "regression_free_rate": (
            sum(1 for r in recs if r.regression_free_contrib)
            / len([r for r in recs if r.rust_call_count is not None and not r.vacuous])
            if [r for r in recs if r.rust_call_count is not None and not r.vacuous]
            else None
        ),
        "n_regression_free_judged": len(
            [r for r in recs if r.rust_call_count is not None and not r.vacuous]
        ),
        "mean_accepted_speedup": {
            "rust_pure": _mean_speedup("rust_pure"),
            "hybrid_pyo3": _mean_speedup("hybrid_pyo3"),
        },
        "mean_rounds": (
            sum(len(r.rounds) for r in measured) / len(measured) if measured else None
        ),
    }


# ---------------------------------------------------------------------------
# PHẦN 3.4 -- cache theo (repo, arm) cho `--resume`
# ---------------------------------------------------------------------------
def _arm_cache_path(cfg: dict, label: str, arm: str) -> Path | None:
    """Đường dẫn file cache của một (repo, arm). None nếu chưa bật cache."""
    d = cfg.get("_arm_cache_dir")
    if not d:
        return None
    out = Path(d)
    out.mkdir(parents=True, exist_ok=True)
    safe_arm = arm or "single"
    return out / f"{label}.{safe_arm}.json"


def _save_arm_cache(path: Path, out: dict) -> None:
    """Ghi kết quả một nhánh. Lỗi ghi KHÔNG được làm sập lượt chạy -- cache chỉ
    là tiện nghi, mất nó thì chạy lại chứ không mất kết quả."""
    try:
        payload = {
            "records": {n: r.as_dict() for n, r in out["records"].items()},
            "stages": (out.get("summary") or {}).get("stages") or {},
            "hybrid_tests": (
                out["hybrid_tests"].as_dict() if out.get("hybrid_tests") is not None else None
            ),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Không ghi được cache nhánh %s: %s", path.name, exc)


def _load_arm_cache(path: Path, arm_records: dict) -> dict | None:
    """Nạp lại kết quả một nhánh đã chạy xong.

    Chỉ nạp những khoá mà `HotspotRecord` thật sự có -- file cache của phiên bản
    code cũ hơn sẽ thiếu/thừa field, và nạp bừa sẽ tạo ra record nửa vời khó
    lần ra. Thiếu hotspot nào thì coi cache không dùng được, chạy lại cho chắc.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Cache nhánh %s không đọc được: %s", path.name, exc)
        return None

    raw_records = payload.get("records") or {}
    if not raw_records:
        return None

    allowed = set(HotspotRecord.__dataclass_fields__)
    rebuilt: dict[str, HotspotRecord] = {}
    for name, data in raw_records.items():
        rec = arm_records.get(name) or HotspotRecord(function_name=name)
        # `as_dict()` dùng khoá "function" cho tên, còn field là `function_name`.
        for key, value in data.items():
            field_name = "function_name" if key == "function" else key
            if field_name in allowed:
                setattr(rec, field_name, value)
        rebuilt[name] = rec

    hybrid = None
    if payload.get("hybrid_tests"):
        from stage5_compiler_in_the_loop.repo_runner import RepoTestResult

        h = payload["hybrid_tests"]
        hybrid = RepoTestResult(
            label=h.get("label", "hybrid"), ok=bool(h.get("ok")),
            n_passed=int(h.get("n_passed") or 0), n_total=int(h.get("n_total") or 0),
            n_failed=int(h.get("n_failed") or 0), n_errors=int(h.get("n_errors") or 0),
            n_skipped=int(h.get("n_skipped") or 0),
        )
    return {
        "records": rebuilt,
        "summary": {"stages": payload.get("stages") or {}},
        "hybrid_tests": hybrid,
        "arm_results": _collect_arm_results(
            next(iter(rebuilt.values())).arm if rebuilt else "", rebuilt
        ),
    }
