"""Stage 3 -- Đóng gói context cho ĐÚNG 1 hotspot.

Bước trung gian giữa:
  - `stage0_graph/context_export.py`: xuất TOÀN BỘ graph ra JSON (để lưu trữ,
    để người đọc, để debug) -- quá lớn và quá nhiễu để nhét thẳng vào prompt.
  - `stage4_llm_transpile/generator_agent.py`: chỉ cần context của ĐÚNG 1 hàm
    đang định dịch.

Cấu trúc context bám theo POLO (Bai et al., IJCAI-25) Section 3.3:
    N_node(u) = {v ∈ V^s ∪ V^c | (u,v) ∈ E^s ∪ E^c}
    N_edge(u) = {e = (u,v) | e ∈ E^s ∪ E^c}
tức là: lấy các node LÁNG GIỀNG của hotspot u trong cả PCG (quan hệ gọi hàm)
lẫn PSG (quan hệ cấu trúc/import), kèm thuộc tính của cạnh nối tới chúng.
Prompt template Fig.5 của POLO dùng đúng các mảnh này:
    (1) "The hotspot calls {node} {N} times: <code>"      -> callees
    (2) "The {node} calls hotspot {N} times: <code>"        -> callers
    (3)(4) "The hotspot references {node} / {node} references hotspot" -> PSG
    (5) "Class {node} that hotspot belongs to: <code>"      -> class_context

Khác với POLO (C++, có class/struct/global variable qua Clang LibTooling),
scaffold này phân tích Python nên:
  - "class" lấy từ `FunctionNode.qualified_name` (vd "MyClass.method").
  - quan hệ PSG ở mức FILE (import giữa các file), không có global variable.
Đây là đơn giản hoá có chủ đích, đã ghi rõ trong README.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("benchmark.stage3_context_packaging.packager")

# Cắt bớt source rất dài trước khi nhét vào prompt (giữ chi phí token có kiểm soát).
MAX_SNIPPET_CHARS = 4000


def _truncate(source: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    src = source or ""
    if len(src) <= limit:
        return src
    return src[:limit] + f"\n// ... (đã cắt bớt, còn {len(src) - limit} ký tự)"


def _node_brief(fn, edge=None) -> dict[str, Any]:
    """Tóm tắt 1 FunctionNode láng giềng + thuộc tính cạnh nối tới hotspot."""
    brief: dict[str, Any] = {
        "id": fn.id,
        "name": fn.name,
        "qualified_name": fn.qualified_name,
        "file": fn.file,
        "lineno_start": fn.lineno_start,
        "lineno_end": fn.lineno_end,
        "source": _truncate(fn.source),
    }
    if edge is not None:
        brief["edge"] = {
            # count/time_contribution_pct chỉ khác mặc định khi graph.build_mode
            # = dynamic (đã chạy Stage 1) -- xem stage0_graph/models.py::CallEdge.
            "count": edge.count,
            "time_contribution_pct": edge.time_contribution_pct,
        }
    return brief


def _class_detail_from_psg(graph, hotspot) -> dict[str, Any] | None:
    """Context class lấy từ PSG ĐẦY ĐỦ (stage0_graph/psg.py). None nếu hotspot
    không phải method, hoặc PSG đầy đủ không dựng được.

    Trả về: tên class, lớp cha, và các method KHÁC cùng class -- tách riêng
    những method mà hotspot GỌI và những method GỌI hotspot, vì hai quan hệ đó
    nói hai điều khác nhau về việc có tách hàm ra được hay không.
    """
    classes = getattr(graph, "classes", None) or {}
    ownership = getattr(graph, "ownership_edges", None) or []
    if not classes or not ownership:
        return None

    owner_id = next(
        (e.dst for e in ownership if e.kind == "ismember" and e.src == hotspot.id), None
    )
    if owner_id is None:
        return None
    cls = classes.get(owner_id)
    if cls is None:
        return None

    # Lớp cha: kèm cờ resolved để người đọc biết cha có trong scope hay không.
    bases: list[dict[str, Any]] = []
    for edge in getattr(graph, "inheritance_edges", None) or []:
        if edge.kind != "subclassOf" or edge.src != owner_id:
            continue
        if edge.resolved:
            parent = classes.get(edge.dst)
            bases.append({
                "name": parent.qualified_name if parent else edge.dst,
                "in_scope": True,
                "methods": list(parent.method_names) if parent else [],
            })
        else:
            bases.append({"name": edge.dst, "in_scope": False, "methods": []})

    sibling_ids = [m for m in cls.method_ids if m != hotspot.id]
    calls_out, called_by = [], []
    for edge in graph.call_edges:
        if edge.caller == hotspot.id and edge.callee in sibling_ids:
            fn = graph.functions.get(edge.callee)
            if fn is not None:
                calls_out.append(fn.name)
        elif edge.callee == hotspot.id and edge.caller in sibling_ids:
            fn = graph.functions.get(edge.caller)
            if fn is not None:
                called_by.append(fn.name)

    return {
        "class_name": cls.qualified_name,
        "class_file": cls.file,
        "class_lineno": cls.lineno_start,
        "bases": bases,
        "n_methods": len(cls.method_ids),
        "sibling_methods": [
            graph.functions[m].name for m in sibling_ids if m in graph.functions
        ],
        "hotspot_calls_siblings": sorted(set(calls_out)),
        "siblings_call_hotspot": sorted(set(called_by)),
        "docstring_first_line": cls.docstring_first_line,
    }


def _find_hotspot_nodes(graph, function_name: str) -> list:
    """Mọi FunctionNode có tên trần khớp `function_name` (có thể >1 nếu trùng
    tên ở nhiều file -- giữ hết, không đoán bừa; xem giới hạn 'khớp theo tên'
    trong stage0_graph/builder.py)."""
    return [fn for fn in graph.functions.values() if fn.name == function_name]


def package_context_for(
    function_name: str, graph, profiling_data: Any | None = None
) -> dict[str, Any]:
    """HÀM CHÍNH của Stage 3.

    function_name: tên hàm hotspot (trùng key của FuncRank/Decision Gate).
    graph:         ProgramGraph từ stage0_graph/builder.py (đã áp dữ liệu
                   động nếu chạy build_mode=dynamic).
    profiling_data: DynamicProfileResult từ stage1_profiling (hoặc None nếu
                   chạy chế độ tĩnh) -- dùng để bổ sung self_time/call_count.

    Trả về dict context có cấu trúc, sẵn sàng cho generator_agent.py ghép vào
    prompt. Trả về dict có khoá "found": False nếu không tìm thấy hàm (KHÔNG
    raise -- caller chỉ cần bỏ qua hotspot đó và chạy tiếp).
    """
    hotspots = _find_hotspot_nodes(graph, function_name)
    if not hotspots:
        logger.warning(
            "Stage 3: không tìm thấy hàm '%s' trong graph -- bỏ qua đóng gói "
            "context cho hotspot này.", function_name,
        )
        return {"found": False, "function_name": function_name}

    if len(hotspots) > 1:
        logger.info(
            "Stage 3: hàm '%s' xuất hiện ở %d file (%s) -- đóng gói context "
            "cho TẤT CẢ để model tự đối chiếu, không đoán bừa bản nào đúng.",
            function_name, len(hotspots), [fn.file for fn in hotspots],
        )

    occurrences: list[dict[str, Any]] = []
    for hotspot in hotspots:
        callers: list[dict[str, Any]] = []   # ai gọi hotspot  (POLO Fig.5 phần 2)
        callees: list[dict[str, Any]] = []   # hotspot gọi ai  (POLO Fig.5 phần 1)
        for edge in graph.call_edges:
            if edge.callee == hotspot.id:
                caller_fn = graph.functions.get(edge.caller)
                if caller_fn is not None:
                    callers.append(_node_brief(caller_fn, edge))
            elif edge.caller == hotspot.id:
                callee_fn = graph.functions.get(edge.callee)
                if callee_fn is not None:
                    callees.append(_node_brief(callee_fn, edge))

        # Class bao ngoài (POLO Fig.5 phần 5). Python: suy ra từ qualified_name.
        class_context = None
        if "." in hotspot.qualified_name:
            class_context = hotspot.qualified_name.rsplit(".", 1)[0]

        # --- PHA 3.3: context class LẤY TỪ PSG ĐẦY ĐỦ ---------------------
        # `class_context` ở trên chỉ là một chuỗi tên suy từ qualified_name.
        # PSG đầy đủ (stage0_graph/psg.py) cho biết thêm: lớp cha, và các method
        # KHÁC cùng class mà hotspot gọi / bị gọi bởi. Với hotspot là method,
        # đó là thông tin quan trọng hơn cả quan hệ import giữa các file --
        # Generator Agent cần nó để viết shim Tầng 2 đúng.
        class_detail = _class_detail_from_psg(graph, hotspot)

        # Quan hệ PSG ở mức file (POLO Fig.5 phần 3-4, đơn giản hoá cho Python).
        file_node = graph.files.get(hotspot.file)
        imports_this_file = [b for a, b in graph.import_edges if a == hotspot.file]
        imported_by = [a for a, b in graph.import_edges if b == hotspot.file]

        occurrences.append({
            "hotspot": {
                "id": hotspot.id,
                "name": hotspot.name,
                "qualified_name": hotspot.qualified_name,
                "file": hotspot.file,
                "lineno_start": hotspot.lineno_start,
                "lineno_end": hotspot.lineno_end,
                "source": _truncate(hotspot.source),
                "dynamic_time_pct": hotspot.dynamic_time_pct,
                "dynamic_call_count": hotspot.dynamic_call_count,
            },
            "callers": callers,
            "callees": callees,
            "class_context": class_context,
            "class_detail": class_detail,
            "file_context": {
                "path": hotspot.file,
                "imports_raw": list(file_node.imports_raw) if file_node else [],
                "imports_files_in_scope": imports_this_file,
                "imported_by_files_in_scope": imported_by,
            },
        })

    profiling: dict[str, Any] = {"available": False}
    if profiling_data is not None:
        self_time = getattr(profiling_data, "self_time", {}) or {}
        if function_name in self_time:
            total = getattr(profiling_data, "total_time", 0.0) or 1e-12
            profiling = {
                "available": True,
                "backend": getattr(profiling_data, "profiler_backend", "unknown"),
                "self_time_sec": self_time[function_name],
                "self_time_pct": 100.0 * self_time[function_name] / total,
                "call_count": (getattr(profiling_data, "call_count", {}) or {}).get(function_name, 0),
                "python_pct": (getattr(profiling_data, "python_pct", {}) or {}).get(function_name),
                "native_pct": (getattr(profiling_data, "native_pct", {}) or {}).get(function_name),
            }
        else:
            profiling = {
                "available": False,
                "reason": (
                    "hàm không được thực thi trong lần profiling động (không có "
                    "implementation callable, hoặc code path không chạy tới) -- "
                    "xem POLO Section 3.2"
                ),
            }

    context = {
        "found": True,
        "function_name": function_name,
        "graph_backend": graph.backend,
        "graph_build_mode": graph.build_mode,
        "occurrences": occurrences,
        "profiling": profiling,
    }
    logger.info(
        "Stage 3: đóng gói context cho '%s' -- %d vị trí, %d caller, %d callee, profiling=%s.",
        function_name, len(occurrences),
        sum(len(o["callers"]) for o in occurrences),
        sum(len(o["callees"]) for o in occurrences),
        profiling.get("available"),
    )
    return context


def format_context_for_prompt(context: dict[str, Any]) -> str:
    """Trải `package_context_for(...)` thành text thuần để nhét vào prompt,
    theo đúng thứ tự các mảnh trong POLO Fig.5 (Generator Agent template)."""
    if not context.get("found"):
        return f"(Không có context cho hàm {context.get('function_name')!r}.)"

    parts: list[str] = []
    prof = context.get("profiling") or {}
    if prof.get("available"):
        parts.append(
            "### Số liệu profiling thật (khi chạy workload)\n"
            f"- Chiếm {prof['self_time_pct']:.2f}% tổng thời gian (self time), "
            f"được gọi {prof['call_count']} lần.\n"
            + (f"- Python {prof['python_pct']:.1f}% / native {prof['native_pct']:.1f}%\n"
               if prof.get("python_pct") is not None and prof.get("native_pct") is not None else "")
        )

    for idx, occ in enumerate(context["occurrences"], start=1):
        hs = occ["hotspot"]
        header = f"### Vị trí {idx}: {hs['file']}:{hs['lineno_start']}-{hs['lineno_end']}"
        parts.append(header)

        cd = occ.get("class_detail")
        if cd:
            # PSG ĐẦY ĐỦ: class + lớp cha + method cùng class (POLO Table 1).
            lines = [f"Hotspot là method của class `{cd['class_name']}` "
                     f"({cd['n_methods']} method, {cd['class_file']}:{cd['class_lineno']})"]
            if cd.get("docstring_first_line"):
                lines.append(f"  docstring class: {cd['docstring_first_line']}")
            for b in cd.get("bases") or []:
                scope = "trong scope" if b["in_scope"] else "NGOÀI scope"
                extra = f", method: {', '.join(b['methods'][:8])}" if b["methods"] else ""
                lines.append(f"  kế thừa `{b['name']}` ({scope}{extra})")
            if cd.get("hotspot_calls_siblings"):
                lines.append("  hotspot GỌI method cùng class: "
                             + ", ".join(cd["hotspot_calls_siblings"]))
            if cd.get("siblings_call_hotspot"):
                lines.append("  method cùng class GỌI hotspot: "
                             + ", ".join(cd["siblings_call_hotspot"]))
            others = [m for m in (cd.get("sibling_methods") or [])
                      if m not in set(cd.get("hotspot_calls_siblings") or [])
                      | set(cd.get("siblings_call_hotspot") or [])]
            if others:
                lines.append("  method khác cùng class (không có quan hệ gọi trực "
                             "tiếp): " + ", ".join(others[:12]))
            parts.append("\n".join(lines))
        elif occ.get("class_context"):
            parts.append(f"Hotspot là thành viên của class: {occ['class_context']}")

        for callee in occ["callees"]:
            edge = callee.get("edge") or {}
            times = f" {edge['count']} lần" if edge.get("count") else ""
            parts.append(
                f"Hotspot GỌI `{callee['name']}`{times} ({callee['file']}):\n"
                f"```python\n{callee['source']}\n```"
            )
        for caller in occ["callers"]:
            edge = caller.get("edge") or {}
            times = f" {edge['count']} lần" if edge.get("count") else ""
            parts.append(
                f"`{caller['name']}` GỌI hotspot{times} ({caller['file']}):\n"
                f"```python\n{caller['source']}\n```"
            )

        fc = occ["file_context"]
        if fc["imports_raw"]:
            parts.append("Import của file chứa hotspot:\n" + "\n".join(fc["imports_raw"]))
        if fc["imported_by_files_in_scope"]:
            parts.append("File khác trong scope import file này: "
                         + ", ".join(fc["imported_by_files_in_scope"]))

    return "\n\n".join(parts)


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(BENCHMARK_ROOT))
    from config_loader import ensure_utf8_stdio
    from stage0_graph.builder import build_graph, discover_python_files

    ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

    fname = sys.argv[1] if len(sys.argv) > 1 else "edge_det"
    target = BENCHMARK_ROOT / "data" / "reference_repo"
    graph = build_graph(discover_python_files(target), target)
    ctx = package_context_for(fname, graph)
    print(json.dumps(ctx, indent=2, ensure_ascii=False)[:2500])
    print("\n===== PROMPT TEXT =====\n")
    print(format_context_for_prompt(ctx)[:2000])
