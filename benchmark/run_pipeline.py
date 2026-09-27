"""Điều phối FULL PIPELINE theo kiến trúc 6 giai đoạn của hệ thống chính.

    input/intake.py          -> resolve local path | clone URL GitHub
    stage0_graph/            -> PCG + PSG (tree-sitter, fallback ast) + FuncRank
    stage1_profiling/        -> profiling động (Scalene, fallback cProfile)
    stage2_decision_gate/    -> phân loại skip / vectorize / candidate
    stage3_context_packaging/-> đóng gói context 1 hotspot (POLO Fig.5)   [chỉ khi llm.enabled]
    stage4_llm_transpile/    -> Generator Agent sinh Rust, Decision Agent  [chỉ khi llm.enabled]
    (stage5: compiler-in-the-loop -- FUTURE WORK, không thuộc benchmark này)
    stage6_benchmark/        -> đo 3 phiên bản python_pure/rust_pure/hybrid_pyo3

QUAN HỆ VỚI `stage6_benchmark/bench.py`:
  - `bench.py` VẪN CHẠY ĐỘC LẬP như trước, không bị bắt buộc đi qua file này.
    Ai chỉ cần đo tốc độ 3 phiên bản (đã điền code tay) thì dùng bench.py.
  - `run_pipeline.py` là đường chạy ĐẦY ĐỦ hơn cho ai muốn test cả input
    intake (GitHub URL), Decision Gate và LLM transpile.

Khi `llm.enabled = false` (MẶC ĐỊNH): bỏ qua hẳn stage3/stage4 -- KHÔNG cần
cài `anthropic`, KHÔNG cần API key, và kết quả benchmark giống hệt chạy
bench.py trực tiếp.

Chạy:  python run_pipeline.py
"""
from __future__ import annotations

import copy
import json
import logging
import sys
import time
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from config_loader import ensure_utf8_stdio, load_config  # noqa: E402
from data.loader import load_image  # noqa: E402
from stage6_benchmark import bench, report  # noqa: E402

ensure_utf8_stdio()
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("benchmark.run_pipeline")


def _banner(stage: str, title: str) -> None:
    logger.info("=" * 64)
    logger.info("%-8s | %s", stage, title)
    logger.info("=" * 64)


def _mean(values: list[float] | None) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def run_once(cfg: dict, image, results_dir: Path, timestamp: str, label: str = "single") -> dict:
    """Chạy TRỌN pipeline 1 lần cho đúng 1 target (đã nằm trong `cfg`).

    Tách riêng khỏi main() để nhánh dataset có thể gọi lại hàm này cho TỪNG
    repo mà không nhân bản logic. Trả về dict summary kèm khoá "ok".
    """
    target_cfg = cfg.get("target") or {}
    mode = target_cfg.get("mode", "function")
    llm_cfg = cfg.get("llm") or {}
    llm_enabled = bool(llm_cfg.get("enabled", False))
    gate_enabled = bool((cfg.get("decision_gate") or {}).get("enabled", True))

    summary: dict = {
        "timestamp": timestamp,
        "label": label,
        "target_mode": mode,
        "target_source": target_cfg.get("source"),
        "llm_enabled": llm_enabled,
        "decision_gate_enabled": gate_enabled,
        "ok": False,
        "stages": {},
    }

    # ------------------------------------------------------------------
    # STAGE 0/1: dựng graph, chọn hotspot.
    # Dùng LẠI y nguyên logic của bench.py (_select_target_functions) để
    # không có 2 bản logic song song dễ lệch nhau: hàm đó đã tự gọi
    # input/intake.py, stage0_graph/ và stage1_profiling/ theo config.
    # ------------------------------------------------------------------
    _banner("STAGE 0/1", "Dựng PCG/PSG + FuncRank (+ profiling động nếu build_mode=dynamic)")
    try:
        functions, graph = bench._select_target_functions(cfg, image)
    except ValueError as exc:
        logger.error("Cấu hình target không hợp lệ: %s -- dừng target này.", exc)
        summary["error"] = str(exc)
        return summary

    if not functions:
        logger.error("Không có hàm nào để benchmark -- dừng target này.")
        summary["error"] = "không có hàm để benchmark"
        return summary
    logger.info("Hotspot được chọn: %s", functions)
    summary["stages"]["stage0_1"] = {
        "functions": list(functions),
        "graph_backend": getattr(graph, "backend", None),
        "graph_build_mode": getattr(graph, "build_mode", None),
    }

    # ------------------------------------------------------------------
    # STAGE 2: Decision Gate (pre-filter). Chỉ có ý nghĩa khi có graph
    # (mode=file/repo) -- mode=function dùng danh sách hàm cứng nên không lọc.
    # ------------------------------------------------------------------
    gate_labels: dict[str, str] = {}
    if gate_enabled and graph is not None:
        _banner("STAGE 2", "Decision Gate -- phân loại skip / vectorize / candidate")
        from stage2_decision_gate.gate import LABEL_CANDIDATE, classify_functions

        gate_labels = classify_functions(graph, functions)
        candidates = [n for n in functions if gate_labels.get(n) == LABEL_CANDIDATE]
        logger.info("Hotspot nhãn 'candidate' (đáng dịch sang Rust): %s", candidates)
    else:
        from stage2_decision_gate.gate import LABEL_CANDIDATE

        if not gate_enabled:
            logger.info("STAGE 2 | Decision Gate bị TẮT (decision_gate.enabled=false) -- bỏ qua.")
        else:
            logger.info(
                "STAGE 2 | target.mode='function' (danh sách hàm cứng, không có "
                "graph) -- bỏ qua Decision Gate."
            )
        candidates = list(functions)
    summary["stages"]["stage2"] = {"labels": gate_labels, "candidates": list(candidates)}

    # ------------------------------------------------------------------
    # STAGE 3 + 4: chỉ khi llm.enabled = true.
    # ------------------------------------------------------------------
    transpile_results: list[dict] = []
    generator_agents: dict[str, object] = {}  # tên hàm -> GeneratorAgent (giữ cho Stage 5)
    if not llm_enabled:
        _banner("STAGE 3/4", "BỎ QUA (llm.enabled=false) -- dùng code đã điền tay trong versions/")
        logger.info(
            "Không cần cài `anthropic`, không cần API key. Bật bằng cách đặt "
            "llm.enabled: true trong config.yaml."
        )
        summary["stages"]["stage3_4"] = {"skipped": True, "reason": "llm.enabled=false"}
    elif not candidates:
        _banner("STAGE 3/4", "BỎ QUA -- Decision Gate không để lại hotspot nhãn 'candidate' nào")
        summary["stages"]["stage3_4"] = {"skipped": True, "reason": "không có candidate"}
    else:
        _banner("STAGE 3/4", "Đóng gói context (POLO Fig.5) + Generator Agent sinh Rust")
        from stage3_context_packaging.packager import package_context_for
        from stage4_llm_transpile.generator_agent import GeneratorAgent
        from stage4_llm_transpile.model_backend import ModelBackendError, get_model_backend

        backend = None
        try:
            backend = get_model_backend(cfg)
        except ModelBackendError as exc:
            # Yêu cầu: KHÔNG crash cả chương trình khi thiếu API key/package.
            logger.error(
                "Không khởi tạo được LLM backend: %s\n"
                "-> BỎ QUA Stage 3/4, pipeline vẫn chạy tiếp tới Stage 6 để "
                "benchmark code đã có sẵn trong versions/.", exc,
            )
            summary["stages"]["stage3_4"] = {"skipped": True, "reason": f"backend lỗi: {exc}"}

        if backend is not None and graph is None:
            # target.mode=function dùng danh sách hàm CỨNG, không dựng graph.
            # Mà Stage 3 cần graph để lấy neighbor node/edge làm context, nên
            # không có graph thì không đóng gói context được.
            logger.warning(
                "STAGE 3/4 | target.mode='function' nên không có graph -- BỎ QUA "
                "Stage 3/4 (đóng gói context cần PCG/PSG). Đổi target.mode sang "
                "'file' hoặc 'repo' nếu muốn dùng LLM dịch code."
            )
            summary["stages"]["stage3_4"] = {
                "skipped": True,
                "reason": "target.mode=function -> không có graph để lấy context",
            }
            backend = None  # để khối dưới không chạy

        if backend is not None:
            profile_data = getattr(graph, "_dynamic_profile", None)
            for name in candidates:
                context = package_context_for(name, graph, profile_data)
                # MỖI hotspot 1 GeneratorAgent riêng -> session/lịch sử riêng.
                # Giữ lại agent để Stage 5 nhờ đúng nó sửa lỗi biên dịch, nhờ
                # vậy nó còn nhớ code vừa viết.
                agent = GeneratorAgent(backend)
                generator_agents[name] = agent
                result = agent.generate_rust(name, context)
                transpile_results.append(result)
                if not result.get("ok"):
                    logger.error(
                        "Stage 4: dịch '%s' thất bại (%s) -- tiếp tục hotspot khác.",
                        name, result.get("error"),
                    )
            summary["stages"]["stage3_4"] = {
                "skipped": False,
                "backend": backend.name,
                "results": transpile_results,
            }
            logger.warning(
                "Code Rust sinh ra là BẢN NHÁP trong "
                "versions/rust_pure/pyo3_ext/generated/ -- CHƯA được biên dịch "
                "vào extension. Muốn benchmark nó, phải tự đọc/merge vào "
                "pyo3_ext/src/lib.rs rồi `maturin develop --release` "
                "(stage5 compiler-in-the-loop tự động hoá bước này là FUTURE WORK)."
            )

    # ------------------------------------------------------------------
    # STAGE 5: compiler-in-the-loop. Chỉ có việc để làm khi Stage 4 đã sinh
    # ra code Rust. Vòng sửa lỗi CHỈ dùng Generator Agent, không dùng
    # Decision Agent (xem docstring stage5_compiler_in_the_loop/loop_runner.py).
    # ------------------------------------------------------------------
    cl_cfg = cfg.get("compiler_loop") or {}
    stage5_enabled = bool(cl_cfg.get("enabled", True))
    ok_transpiles = [r for r in transpile_results if r.get("ok")]

    if not stage5_enabled:
        _banner("STAGE 5", "BỎ QUA (compiler_loop.enabled=false)")
        summary["stages"]["stage5"] = {"skipped": True, "reason": "compiler_loop.enabled=false"}
    elif not ok_transpiles:
        _banner("STAGE 5", "BỎ QUA -- Stage 4 không sinh được code Rust nào để biên dịch")
        summary["stages"]["stage5"] = {"skipped": True, "reason": "không có code Rust từ Stage 4"}
    else:
        _banner("STAGE 5", "Compiler-in-the-loop: cargo check + Generator Agent sửa lỗi")
        from stage5_compiler_in_the_loop.loop_runner import MAX_COMPILE_RETRIES, run_stage5

        stage5 = run_stage5(
            transpile_results=ok_transpiles,
            generator_agents=generator_agents,
            benchmark_root=BENCHMARK_ROOT,
            max_retries=int(cl_cfg.get("max_retries", MAX_COMPILE_RETRIES)),
            cargo_timeout_sec=int(cl_cfg.get("cargo_timeout_sec", 300)),
        )
        summary["stages"]["stage5"] = stage5
        m = stage5["metrics"]
        if m.get("n_skipped"):
            logger.warning(
                "Stage 5: %d/%d hotspot bị bỏ qua -- %s",
                m["n_skipped"], m["n_total"], m.get("skip_reason", ""),
            )
        if m.get("pass_at_1") is not None:
            logger.info(
                "Stage 5: Pass@1 = %.2f | DSR@1 = %s",
                m["pass_at_1"],
                "n/a" if m.get("dsr_at_1") is None else f"{m['dsr_at_1']:.2f}",
            )

    # ------------------------------------------------------------------
    # CORRECTNESS: chạy SAU Stage 5, TRƯỚC Stage 6.
    # Đo tốc độ của một bản dịch SAI là vô nghĩa (code sai thường nhanh hơn
    # vì bỏ bớt việc), nên chặn ngay tại đây.
    # ------------------------------------------------------------------
    from stage5_compiler_in_the_loop.correctness import (
        MISMATCH as CORRECTNESS_MISMATCH,
        run_correctness_checks,
    )

    # pass@1 theo TỪNG hotspot, lấy từ outcome của Stage 5 ở trên.
    pass_at_1_by_function: dict[str, bool | None] = {}
    for o in (summary["stages"].get("stage5") or {}).get("outcomes", []) or []:
        pass_at_1_by_function[o["function_name"]] = (
            None if o.get("skipped") else bool(o.get("passed_first_try"))
        )

    correctness_results: dict = {}
    corr_cfg = cfg.get("correctness") or {}
    if corr_cfg.get("enabled", True):
        _banner("CORRECTNESS", "So khớp output Python vs Rust (trước khi đo tốc độ)")
        correctness_results = run_correctness_checks(
            function_names=functions,
            image=image,
            python_registry=bench.python_pure_pipeline.PIPELINE_REGISTRY,
            rust_registry=bench.rust_pure_pipeline.PIPELINE_REGISTRY,
            rtol=float(corr_cfg.get("rtol", 1e-5)),
            atol=float(corr_cfg.get("atol", 1e-8)),
        )
        summary["stages"]["correctness"] = {
            name: {"status": r.status, "detail": r.detail,
                   "n_matched": r.n_matched, "n_samples": r.n_samples}
            for name, r in correctness_results.items()
        }
    else:
        logger.info("CORRECTNESS | bị TẮT (correctness.enabled=false) -- bỏ qua.")

    # ------------------------------------------------------------------
    # STAGE 6: benchmark 3 phiên bản -- dùng lại đúng hàm của bench.py.
    # ------------------------------------------------------------------
    _banner("STAGE 6", "Benchmark python_pure / rust_pure / hybrid_pyo3 (in-process)")
    b_cfg = cfg["benchmark"]
    warmup, iterations = b_cfg["warmup"], b_cfg["iterations"]

    all_results: dict[str, dict[str, list[float]] | None] = {
        "python_pure": bench._bench_python_pure(image, functions, warmup, iterations),
        "hybrid_pyo3": bench._bench_hybrid(image, functions, warmup, iterations),
        "rust_pure": bench._bench_rust_pure(image, functions, warmup, iterations),
    }

    suffix = f"{timestamp}" if label == "single" else f"{timestamp}_{label}"
    raw_path = results_dir / f"pipeline_raw_{suffix}.json"
    raw_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    table = report.build_report(all_results)
    print("\n" + table)

    # ------------------------------------------------------------------
    # Quyết định accept/reject sau benchmark (rule nếu num_agents=1).
    # ------------------------------------------------------------------
    _banner("STAGE 6", "Decision Agent -- accept/reject bản Rust sau khi đo")
    from stage4_llm_transpile.decision_agent import decide_after_benchmark

    num_agents = int(llm_cfg.get("num_agents", 1))
    decision_backend = None
    decision_session = None
    if llm_enabled and num_agents >= 2:
        try:
            from stage4_llm_transpile.model_backend import ModelBackendError, get_model_backend

            decision_backend = get_model_backend(cfg)
            # Session RIÊNG cho Decision Agent, TÁCH HẲN khỏi session của các
            # Generator Agent ở Stage 3/4. Hai vai trò dùng chung kết nối tới
            # model nhưng KHÔNG thấy lịch sử hội thoại của nhau.
            from stage4_llm_transpile.agent_session import AgentSession
            from stage4_llm_transpile.decision_agent import DECISION_SYSTEM_PROMPT

            decision_session = AgentSession(
                role_name="decision",
                system_prompt=DECISION_SYSTEM_PROMPT,
                backend=decision_backend,
            )
        except ModelBackendError as exc:
            logger.error("Decision Agent: không có backend (%s) -- dùng rule.", exc)

    # Rebuild + đo lại trong subprocess giữa các vòng -- xem docstring của 2
    # module này để biết vì sao KHÔNG đo in-process ở bối cảnh này.
    from stage5_compiler_in_the_loop.rebuild import (
        BUILD_FAILED,
        SKIPPED_NO_CARGO,
        rebuild_from_draft,
        restore_original_lib,
    )
    from stage6_benchmark.measure_subprocess import measure_in_subprocess

    decisions: list[dict] = []
    hotspot_rows: list[dict] = []
    py_results = all_results.get("python_pure") or {}
    rs_results = dict(all_results.get("rust_pure") or {})
    opt_cfg = cfg.get("optimization_loop") or {}
    max_rounds = int(opt_cfg.get("max_rounds", 3))
    build_timeout = int(opt_cfg.get("build_timeout_sec", 300))
    pyo3_crate = BENCHMARK_ROOT / "versions" / "rust_pure" / "pyo3_ext"

    # try/finally: dù vòng lặp có lỗi giữa chừng, code Rust VIẾT TAY trong
    # pyo3_ext/src/lib.rs vẫn phải được khôi phục -- pipeline không được phép
    # làm người dùng mất code.
    try:
      for name in functions:
        corr = correctness_results.get(name)
        corr_status = corr.status if corr else None
        pass1 = pass_at_1_by_function.get(name)

        # --- CORRECTNESS CHẶN TRƯỚC ---------------------------------------
        # Bản dịch sai thì không đo tốc độ, và cũng không vào vòng tối ưu tốc
        # độ nào cả. Đây là vòng lặp KHÁC với optimization_loop.max_rounds:
        # sai kết quả là việc của Stage 5 (sửa cho đúng), không phải Stage 6.
        if corr_status == CORRECTNESS_MISMATCH:
            logger.error(
                "Stage 6 [%s]: correctness=MISMATCH -> BỎ QUA đo tốc độ và bỏ "
                "qua vòng tối ưu. Lý do: %s", name, corr.detail,
            )
            rs_results.pop(name, None)
            hotspot_rows.append({
                "function": name, "correctness": corr_status, "pass_at_1": pass1,
                "speedup": None, "rounds": 0, "stopped_by_cap": False,
            })
            decisions.append({
                "function": name, "accepted": False, "speedup": None,
                "source": "correctness-gate", "reason": f"MISMATCH: {corr.detail}",
                "continue_optimizing": False, "next_strategy": None, "rounds": 0,
            })
            continue

        # --- VÒNG TỐI ƯU TỐC ĐỘ -------------------------------------------
        rounds_done = 0
        stopped_by_cap = False
        best_speedup: float | None = None
        agent = generator_agents.get(name)
        # Vòng 1 đo trên extension đang có sẵn (chưa build lại lần nào).
        round_build_status = "INITIAL"
        rounds_build_status: list[dict] = [{"round": 1, "build_status": round_build_status}]

        for round_index in range(1, max_rounds + 1):
            rounds_done = round_index
            decision = decide_after_benchmark(
                function_name=name,
                before_sec=_mean(py_results.get(name)),
                after_sec=_mean(rs_results.get(name)),
                num_agents=num_agents if llm_enabled else 1,
                backend=decision_backend,
                session=decision_session,
                round_index=round_index,
                max_rounds=max_rounds,
            )
            if decision.speedup is not None:
                best_speedup = (
                    decision.speedup if best_speedup is None
                    else max(best_speedup, decision.speedup)
                )
            stopped_by_cap = decision.stopped_by_cap
            decisions.append({
                "function": decision.function_name,
                "accepted": decision.accepted,
                "speedup": decision.speedup,
                "source": decision.source,
                "reason": decision.reason,
                "continue_optimizing": decision.continue_optimizing,
                "next_strategy": decision.next_strategy,
                "round": round_index,
                "stopped_by_cap": decision.stopped_by_cap,
            })

            if not decision.continue_optimizing:
                break  # Decision Agent chủ động dừng sớm -- đúng thiết kế POLO

            if agent is None:
                logger.warning(
                    "Stage 6 [%s]: Decision Agent muốn tối ưu tiếp nhưng không có "
                    "Generator Agent (llm.enabled=false?) -- dừng vòng lặp.", name,
                )
                break

            opt = agent.optimize_further(name, decision.next_strategy, round_index + 1)
            if not opt.get("ok"):
                logger.error(
                    "Stage 6 [%s]: Generator Agent không tối ưu tiếp được (%s) -- dừng.",
                    name, opt.get("error"),
                )
                break

            # --- REBUILD rồi ĐO LẠI TRONG SUBPROCESS -----------------------
            # Nếu chỉ ghi file draft rồi đo lại in-process, extension native
            # đang nạp vẫn là bản CŨ -> mọi vòng ra speedup giống hệt nhau.
            rebuild = rebuild_from_draft(
                Path(opt["output_path"]), pyo3_crate, build_timeout,
            )
            round_build_status = rebuild.status
            decisions[-1]["build_status_next_round"] = rebuild.status

            if rebuild.status == BUILD_FAILED:
                logger.error(
                    "Stage 6 [%s]: build lại THẤT BẠI ở vòng %d -- dừng vòng lặp, "
                    "GIỮ kết quả vòng %d làm kết quả cuối, KHÔNG báo speedup cho "
                    "vòng lỗi. Chi tiết: %s",
                    name, round_index + 1, round_index, rebuild.output[:300],
                )
                break
            if rebuild.status == SKIPPED_NO_CARGO:
                logger.warning(
                    "Stage 6 [%s]: không build lại được (%s) -- dừng vòng lặp thay "
                    "vì đo lại bản cũ rồi báo speedup sai.", name, rebuild.output,
                )
                break

            durations, err = measure_in_subprocess(
                function_name=name, image=image, benchmark_root=BENCHMARK_ROOT,
                warmup=warmup, iterations=iterations,
            )
            if durations is None:
                logger.error(
                    "Stage 6 [%s]: build OK nhưng đo lại thất bại (%s) -- dừng vòng lặp.",
                    name, err,
                )
                break
            rs_results[name] = durations
            rounds_build_status.append({
                "round": round_index + 1, "build_status": rebuild.status,
            })

        hotspot_rows.append({
            "function": name, "correctness": corr_status, "pass_at_1": pass1,
            "speedup": best_speedup, "rounds": rounds_done,
            "stopped_by_cap": stopped_by_cap,
            "build_status_by_round": rounds_build_status,
            "last_build_status": round_build_status,
        })
    finally:
        # Trả lại nguyên vẹn code Rust viết tay đã bị ghi đè khi rebuild.
        if restore_original_lib(pyo3_crate):
            logger.info(
                "Đã khôi phục versions/rust_pure/pyo3_ext/src/lib.rs về bản viết tay "
                "ban đầu (bản do LLM sinh vẫn còn trong pyo3_ext/generated/)."
            )

    hotspot_table = report.build_hotspot_summary(hotspot_rows)
    print("\n" + hotspot_table)
    round_table = report.build_round_table(hotspot_rows)
    print("\n" + round_table)

    summary["stages"]["stage6"] = {
        "raw_results": str(raw_path),
        "max_rounds": max_rounds,
        "hotspots": hotspot_rows,
        "decisions": decisions,
    }
    summary["ok"] = True

    summary_path = results_dir / f"pipeline_summary_{suffix}.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== TỔNG KẾT PIPELINE ({label}) ===")
    print(f"Target mode      : {mode}")
    print(f"LLM              : {'BẬT (' + str(num_agents) + ' agent)' if llm_enabled else 'TẮT'}")
    print(f"Decision Gate    : {'bật' if gate_enabled else 'tắt'}")
    if gate_labels:
        for fn_name, gate_label in gate_labels.items():
            print(f"  gate: {fn_name:24s} -> {gate_label}")
    s5 = summary["stages"].get("stage5") or {}
    if s5.get("metrics"):
        m = s5["metrics"]
        p1 = "n/a" if m.get("pass_at_1") is None else f"{m['pass_at_1']:.2f}"
        d1 = "n/a" if m.get("dsr_at_1") is None else f"{m['dsr_at_1']:.2f}"
        print(f"  stage5: Pass@1={p1}  DSR@1={d1}  (bỏ qua {m.get('n_skipped', 0)})")
    for d in decisions:
        sp = "n/a" if d["speedup"] is None else f"{d['speedup']:.2f}x"
        verdict = "ACCEPT" if d["accepted"] else "REJECT"
        print(f"  decision[{d['source']}]: {d['function']:20s} {verdict} (speedup={sp})")
    print(f"\nĐã lưu: {raw_path.name}, {summary_path.name} trong {results_dir}")
    return summary


def main() -> int:
    cfg = load_config()  # đã mở rộng ${VAR:-default} -- xem config_loader.py
    results_dir = BENCHMARK_ROOT / (cfg.get("paths") or {}).get("results_dir", "results")
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    _banner("INPUT", "Nạp ảnh workload")
    image = load_image(cfg)
    logger.info("Ảnh input: shape=%s dtype=%s", image.shape, image.dtype)

    ds_cfg = cfg.get("dataset") or {}
    if not ds_cfg.get("enabled", False):
        _banner("INPUT", "Chế độ TARGET ĐƠN (dataset.enabled=false)")
        result = run_once(cfg, image, results_dir, timestamp, label="single")
        return 0 if result.get("ok") else 1

    # ------------------------------------------------------------------
    # Chế độ DATASET: chạy lại toàn bộ pipeline cho TỪNG repo.
    # ------------------------------------------------------------------
    _banner("INPUT", "Chế độ DATASET -- chạy pipeline cho từng repo")
    from input.intake import IntakeError, resolve_dataset_repos

    try:
        repos = resolve_dataset_repos(
            ds_cfg.get("source_root", ""), int(ds_cfg.get("max_repos", 0) or 0)
        )
    except IntakeError as exc:
        # Đây là đường đi khi teammate chưa tải dataset / chưa mount volume.
        # Thông điệp đã có hướng dẫn cụ thể; KHÔNG in traceback.
        logger.error("Không dùng được dataset:\n%s", exc)
        return 1

    per_repo: list[dict] = []
    for idx, repo in enumerate(repos, start=1):
        _banner("DATASET", f"Repo {idx}/{len(repos)}: {repo.name}")
        repo_cfg = copy.deepcopy(cfg)
        repo_cfg.setdefault("target", {})
        repo_cfg["target"]["mode"] = "repo"
        repo_cfg["target"]["source"] = str(repo)
        try:
            result = run_once(repo_cfg, image, results_dir, timestamp, label=repo.name)
        except Exception as exc:  # noqa: BLE001 -- 1 repo hỏng không được dừng cả dataset
            logger.exception("Repo '%s' lỗi bất ngờ -- bỏ qua, chạy tiếp.", repo.name)
            result = {"ok": False, "label": repo.name, "error": f"{type(exc).__name__}: {exc}"}
        per_repo.append(result)

    n_ok = sum(1 for r in per_repo if r.get("ok"))
    dataset_summary = {
        "timestamp": timestamp,
        "source_root": ds_cfg.get("source_root"),
        "n_repos": len(repos),
        "n_ok": n_ok,
        "repos": [
            {"name": r.get("label"), "ok": r.get("ok"), "error": r.get("error")}
            for r in per_repo
        ],
    }
    ds_path = results_dir / f"dataset_summary_{timestamp}.json"
    ds_path.write_text(json.dumps(dataset_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=== TỔNG KẾT DATASET ===")
    print(f"Số repo chạy được: {n_ok}/{len(repos)}")
    for r in per_repo:
        mark = "OK  " if r.get("ok") else "LỖI "
        print(f"  {mark} {r.get('label')}" + ("" if r.get("ok") else f"  ({r.get('error')})"))
    print(f"\nĐã lưu: {ds_path.name}")
    return 0 if n_ok else 1


if __name__ == "__main__":
    sys.exit(main())
