"""Xuất toàn bộ PCG + PSG + FuncRank (tĩnh/động) + source code từng hàm ra 1
file JSON có cấu trúc rõ ràng -- đây là CONTEXT sẽ đưa cho model (qua
stage4_llm_transpile/model_backend.py) để nó hiểu dependency giữa các hàm/file khi sinh
code Rust, thay vì chỉ thấy 1 hàm rời rạc.

LƯU Ý: module này CHỈ xuất file JSON. Nó KHÔNG tự động gọi
stage4_llm_transpile/model_backend.py, và stage6_benchmark/bench.py hiện tại cũng KHÔNG tự động đưa
context này vào lời gọi model -- đây là bước chuẩn bị dữ liệu cho bước
codegen sau này (TODO: nối context này vào ModelBackend.generate_rust_code()
khi hiện thực codegen thật, vd nhét toàn bộ hoặc 1 phần JSON này vào prompt).

FuncRank xuất ra luôn là kết quả của `rank.func_rank()` (kết hợp động+tĩnh,
xem stage0_graph/rank.py) -- với graph CHƯA chạy profiling động (build_mode=static,
mọi FunctionNode.dynamic_time_pct đều None), hàm này tự động trả về toàn bộ
`rank_source="static_fallback"` với điểm giống hệt PageRank tĩnh thuần tuý,
nên context JSON luôn phản ánh đúng trạng thái graph, không cần tham số
build_mode riêng ở đây.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .models import ProgramGraph
from .rank import func_rank

logger = logging.getLogger("benchmark.stage0_graph.context_export")


def build_context_dict(graph: ProgramGraph) -> dict:
    ranks = func_rank(graph)  # {id: (score, "dynamic" | "static_fallback")}
    return {
        "backend": graph.backend,
        "build_mode": graph.build_mode,
        "num_files": len(graph.files),
        "num_functions": len(graph.functions),
        "files": {
            path: {"imports_raw": node.imports_raw}
            for path, node in sorted(graph.files.items())
        },
        "import_edges": [[a, b] for a, b in graph.import_edges],
        "functions": {
            fid: {
                "name": fn.name,
                "qualified_name": fn.qualified_name,
                "file": fn.file,
                "lineno_start": fn.lineno_start,
                "lineno_end": fn.lineno_end,
                "func_rank": ranks.get(fid, (0.0, "static_fallback"))[0],
                "rank_source": ranks.get(fid, (0.0, "static_fallback"))[1],
                "dynamic_time_pct": fn.dynamic_time_pct,
                "dynamic_call_count": fn.dynamic_call_count,
                "calls_raw": fn.calls_raw,
                "source": fn.source,
            }
            for fid, fn in sorted(graph.functions.items())
        },
        "call_edges": [
            {
                "caller": e.caller,
                "callee": e.callee,
                "count": e.count,
                "time_contribution_pct": e.time_contribution_pct,
            }
            for e in graph.call_edges
        ],
    }


def export_context(graph: ProgramGraph, out_path: Path) -> Path:
    data = build_context_dict(graph)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        "Đã xuất graph context (%s, build_mode=%s): %s (%d hàm, %d file, %d call edge, %d import edge).",
        graph.backend, graph.build_mode, out_path, len(graph.functions), len(graph.files),
        len(graph.call_edges), len(graph.import_edges),
    )
    return out_path


def export_context_default(graph: ProgramGraph, results_dir: Path) -> Path:
    """Xuất ra results/graph_context_<timestamp>.json."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return export_context(graph, Path(results_dir) / f"graph_context_{timestamp}.json")
