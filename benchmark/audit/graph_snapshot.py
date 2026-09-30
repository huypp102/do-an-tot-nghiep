"""AUDIT_RUN4_v2 mục 5 -- xuất PCG/PSG ra JSON làm công cụ QUAN SÁT cho lần
chạy chẩn đoán. KHÔNG sửa logic dựng đồ thị (stage0_graph/), KHÔNG đổi quyết
định Gate hay outcome hotspot nào -- chỉ ĐỌC những gì đã có và ghi ra file.

HAI ĐIỂM GHI (đúng đặc tả mục 5):
  1. `write_snapshot()` -- gọi NGAY SAU khối STAGE 2 trong repo_pipeline.py
     (trước PHA B), ghi graphs/<repo>/pcg.json + psg.json LẦN ĐẦU.
  2. `overlay_outcomes()` -- gọi trong `finish()` (điểm thoát DUY NHẤT của
     `run_repo_pipeline`), ghi đè lớp phủ decision/outcome/in_prompt lên
     pcg.json đã có. Nếu Điểm ghi 1 chưa từng chạy (pipeline chết sớm, vd
     INSTALL_FAILED trước khi có graph), ghi {"graph_available": false} thay
     vì tạo file trông đầy đủ.

QUY ƯỚC SCHEMA (mục 5): khi build_mode KHÔNG phải "dynamic",
`funcrank_dynamic` và `executed_at_runtime` PHẢI là `null`, KHÔNG phải
`false` -- `false` sẽ bị đọc nhầm thành "hàm không bao giờ chạy" (một khẳng
định), trong khi sự thật là "không biết, vì không có profiling động".

GỌI Ở CẢ 2 ĐIỂM PHẢI BỌC try/except NGOÀI module này (ở repo_pipeline.py):
lỗi export tuyệt đối không được đổi outcome hay che lỗi gốc của pipeline.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("benchmark.audit.graph_snapshot")


def _out_dir(results_dir: Path, label: str) -> Path:
    return Path(results_dir) / "graphs" / label


def _function_nodes(graph, *, verdicts: dict, excluded_ids: set, build_mode: str) -> list[dict]:
    from stage0_graph.rank import func_rank_dynamic, func_rank_static

    static_scores = func_rank_static(graph)
    dynamic_scores = func_rank_dynamic(graph)  # {} nếu KHÔNG có hàm nào có dữ liệu động
    is_dynamic_run = build_mode == "dynamic"

    nodes: list[dict] = []
    for fid, fn in graph.functions.items():
        v = verdicts.get(fn.name)
        if is_dynamic_run:
            executed_at_runtime = fn.dynamic_time_pct is not None
        else:
            executed_at_runtime = None  # KHÔNG phải false -- xem docstring module.
        nodes.append({
            "id": fid,
            "name": fn.name,
            "qualified_name": fn.qualified_name,
            "file": fn.file,
            "is_test": fid in excluded_ids,
            "hotspot_level": getattr(v, "hotspot_level", None) if v else None,
            "funcrank_static": static_scores.get(fid),
            "funcrank_dynamic": dynamic_scores.get(fid),  # đã None tự nhiên khi tĩnh
            "executed_at_runtime": executed_at_runtime,
        })
    return nodes


def _call_edges(graph) -> list[dict]:
    """Chỉ gồm cạnh ĐÃ RESOLVE được (exact/heuristic) -- xem CallEdge trong
    stage0_graph/models.py. Lời gọi KHÔNG resolve được (ra ngoài phạm vi repo,
    vd thư viện ngoài) chưa có trong `graph.call_edges` (đó là quyết định của
    stage0_graph/builder.py, KHÔNG sửa ở đây)."""
    return [
        {
            "src": e.caller, "dst": e.callee, "kind": e.resolution,
            "count": e.count,
            "time_contribution_pct": e.time_contribution_pct,
        }
        for e in graph.call_edges
    ]


def _metadata(graph, *, label: str, build_mode: str, gate_enabled: bool) -> dict:
    return {
        "repo": label,
        "build_mode": build_mode,
        "build_mode_note": (
            "Đọc từ graph.build_mode (repo_pipeline.py, gán đúng giá trị "
            "config.graph.build_mode ngay sau khi dựng graph). KHÁC với "
            "workload_source (versions/registry.py::describe_workload_source) "
            "-- 'động' ở workload_source nghĩa là ĐỐI SỐ lấy từ bộ test thật "
            "của repo, LUÔN đúng bất kể build_mode; 'dynamic' ở build_mode "
            "nghĩa là FuncRank có dùng profiling runtime (Scalene) hay không."
        ),
        "dynamic_profiling_used": getattr(graph, "_dynamic_profile", None) is not None,
        "gate_enabled": gate_enabled,
        "graph_backend": getattr(graph, "backend", ""),
        "psg_backend": getattr(graph, "psg_backend", ""),
    }


def write_snapshot(
    graph, *, results_dir: Path, label: str, verdicts: dict, excluded_ids: set,
    build_mode: str, gate_enabled: bool,
) -> None:
    """ĐIỂM GHI 1. `verdicts`: {tên hàm: GateVerdict} (rỗng nếu gate_enabled
    False -- khi đó hotspot_level/decision của mọi node đều null, ĐÚNG đặc tả
    mục 5). `excluded_ids`: id hàm test (stage0_graph.test_filter), đã tính
    sẵn ở Stage 0, không tính lại."""
    out_dir = _out_dir(results_dir, label)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = _metadata(graph, label=label, build_mode=build_mode, gate_enabled=gate_enabled)

    pcg = {
        "graph_available": True,
        "metadata": meta,
        "nodes": _function_nodes(
            graph, verdicts=verdicts, excluded_ids=excluded_ids, build_mode=build_mode,
        ),
        "edges": _call_edges(graph),
    }
    (out_dir / "pcg.json").write_text(
        json.dumps(pcg, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )

    classes = list((getattr(graph, "classes", None) or {}).values())
    global_vars = list((getattr(graph, "global_vars", None) or {}).values())
    psg = {
        "graph_available": True,
        "metadata": meta,
        "nodes": {
            "classes": [asdict(c) for c in classes],
            "global_vars": [asdict(g) for g in global_vars],
            "functions": [
                {"id": fid, "name": fn.name, "qualified_name": fn.qualified_name,
                 "file": fn.file, "is_test": fid in excluded_ids}
                for fid, fn in graph.functions.items()
            ],
        },
        "edges": {
            "inheritance": [e.as_dict() for e in (getattr(graph, "inheritance_edges", None) or [])],
            "ownership": [e.as_dict() for e in (getattr(graph, "ownership_edges", None) or [])],
            "reference": [{"src": a, "dst": b} for a, b in (graph.import_edges or [])],
        },
    }
    (out_dir / "psg.json").write_text(
        json.dumps(psg, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )
    logger.info("audit.graph_snapshot: đã ghi %s (pcg.json, psg.json).", out_dir)


def overlay_outcomes(
    *, results_dir: Path, label: str, records: dict, in_prompt_by_function: dict | None = None,
) -> None:
    """ĐIỂM GHI 2 -- gọi trong `finish()`. `records`: {tên hàm: HotspotRecord}.
    `in_prompt_by_function`: {tên hàm: {ký hiệu: bool}} hoặc None nếu chưa có
    dữ liệu prompt (khi đó cờ in_prompt để trống thay vì đoán).

    Merge vào pcg.json ĐÃ CÓ (ghi bởi `write_snapshot`). Nếu file đó KHÔNG
    tồn tại (pipeline chết sớm, trước khi có graph -- vd INSTALL_FAILED),
    ghi 1 file overlay RIÊNG đánh dấu graph_available=false, KHÔNG dựng 1
    pcg.json giả trông đầy đủ (sẽ đánh lừa người đọc)."""
    out_dir = _out_dir(results_dir, label)
    pcg_path = out_dir / "pcg.json"

    overlay = {
        name: {
            "reason": rec.reason or "MEASURED",
            "gate_decision": rec.gate_decision,
            "gate_hotspot_level": rec.gate_hotspot_level,
            "compiled": rec.compiled,
            "symbols_in_prompt": (in_prompt_by_function or {}).get(name),
        }
        for name, rec in records.items()
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    if not pcg_path.exists():
        (out_dir / "pcg.json").write_text(
            json.dumps(
                {"graph_available": False,
                 "note": "Stage 0/2 chưa chạy xong (pipeline dừng sớm) -- không có graph để phủ.",
                 "overlay": overlay},
                indent=2, ensure_ascii=False, default=str,
            ),
            encoding="utf-8",
        )
        logger.info("audit.graph_snapshot: KHÔNG có pcg.json sẵn (%s) -- ghi graph_available=false.", out_dir)
        return

    try:
        pcg: dict[str, Any] = json.loads(pcg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("audit.graph_snapshot: không đọc lại được %s (%s) -- bỏ qua overlay.", pcg_path, exc)
        return

    by_name = {n.get("name"): n for n in pcg.get("nodes", [])}
    for name, ov in overlay.items():
        node = by_name.get(name)
        if node is not None:
            node["outcome"] = ov
    pcg["overlay_applied"] = True

    pcg_path.write_text(
        json.dumps(pcg, indent=2, ensure_ascii=False, default=str), encoding="utf-8",
    )
    logger.info("audit.graph_snapshot: đã phủ outcome lên %s (%d hàm).", pcg_path, len(overlay))
