"""FuncRank: xếp hạng hotspot trên PCG.

Có 2 nguồn:
  - `func_rank_static()`: PageRank THUẦN (networkx.pagerank()) trên cấu trúc
    liên kết PCG (không quan tâm thời gian thật) -- ĐÂY LÀ BẢN GỐC trước khi
    có profiling động, GIỮ NGUYÊN 100% để dùng khi `target.graph.build_mode
    = "static"` (hành vi cũ) và làm FALLBACK cho hàm không có dữ liệu động.
  - `func_rank_dynamic()`: implement ĐÚNG công thức Eq.1-2 của paper POLO
    (Bai et al., "POLO: An LLM-Powered Project-Level Code Performance
    Optimization Framework", IJCAI-25, Section 3.1) -- dùng % thời gian thật
    (đo qua stage1_profiling/dynamic_profiler.py) thay vì chỉ cấu trúc liên kết, để
    FuncRank phản ánh đúng MỨC ĐỘ ẢNH HƯỞNG thật của từng hàm, không chỉ số
    lượng liên kết.

`func_rank()` kết hợp cả 2: hàm nào CÓ dữ liệu động (đã chạy trong lần
profiling gần nhất) dùng điểm động; hàm nào KHÔNG có (None -- vd không có
implementation callable, hoặc code path không được thực thi với workload
đang dùng, xem POLO Section 3.2) dùng điểm tĩnh làm fallback. Ghi rõ nguồn
gốc mỗi điểm qua `source: "dynamic" | "static_fallback"`.
"""
from __future__ import annotations

import logging

import networkx as nx

from .models import FunctionNode, ProgramGraph

logger = logging.getLogger("benchmark.stage0_graph.rank")

DEFAULT_ALPHA = 0.5          # damping factor mặc định của POLO (Eq.2)
DEFAULT_EPSILON = 1e-6       # ngưỡng hội tụ giữa 2 vòng lặp
DEFAULT_MAX_ITER = 100       # số vòng lặp tối đa


def func_rank_static(graph: ProgramGraph) -> dict[str, float]:
    """PageRank chuẩn (networkx.pagerank(), damping mặc định 0.85) trực tiếp
    trên đồ thị gọi hàm (PCG) -- CHỈ dùng cấu trúc liên kết (ai gọi ai),
    KHÔNG dùng thời gian thật. Đây là hàm `func_rank()` GỐC trước khi có
    profiling động, giữ nguyên logic 100% (đổi tên để làm rõ đây là nhánh
    tĩnh, dùng làm fallback trong func_rank() bên dưới).

    Hàm không có cạnh nào (không gọi ai, không bị ai gọi) vẫn có điểm
    (PageRank chia đều baseline cho node cô lập) -- không bị loại khỏi kết quả.
    """
    G = nx.DiGraph()
    G.add_nodes_from(graph.functions.keys())
    G.add_edges_from((e.caller, e.callee) for e in graph.call_edges)

    if G.number_of_nodes() == 0:
        return {}
    try:
        return nx.pagerank(G)
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank không hội tụ (đồ thị bất thường) -- fallback điểm đồng đều cho mọi hàm.")
        n = G.number_of_nodes()
        return {node: 1.0 / n for node in G.nodes}


def func_rank_dynamic(
    graph: ProgramGraph,
    alpha: float = DEFAULT_ALPHA,
    epsilon: float = DEFAULT_EPSILON,
    max_iter: int = DEFAULT_MAX_ITER,
) -> dict[str, float]:
    """FuncRank ĐỘNG, implement đúng Eq.1-2 của POLO (Bai et al., IJCAI-25,
    Section 3.1):

        Eq.1:  w_ji = Ac_ji[time] / sum_{u in caller(j)} Ac_ju[time]
               (chỉ tính cho cạnh type="callee", chuẩn hoá theo TỔNG
               time_contribution của MỌI cạnh gọi TỚI j -- caller(j))

        Eq.2:  I_i = (1-alpha)*Ac_i[time] + alpha * sum_{j in callee(i)} w_ji * I_j

    Ký hiệu trong code: với 1 CallEdge(caller=i, callee=j) nghĩa là "i gọi
    j" -- w cho cạnh này (w_ji trong công thức, "trọng số j truyền ngược về
    i") = time_contribution_pct(i->j) / tổng time_contribution_pct của MỌI
    cạnh (bất kỳ ai) -> j. Ac_i[time] = FunctionNode.dynamic_time_pct (self
    time %, đã loại trừ thời gian hàm con -- đúng quy ước "excluding its
    calling relationships" của POLO).

    Lặp Eq.2 tới khi hội tụ (|thay đổi giữa 2 vòng| < epsilon) hoặc đạt
    max_iter. alpha mặc định 0.5 (đúng giá trị mặc định POLO dùng).

    CHỈ tính cho các FunctionNode có `dynamic_time_pct is not None` (đã được
    thực thi trong lần profiling gần nhất, xem stage1_profiling/dynamic_profiler.py).
    Trả về {} nếu không có hàm nào có dữ liệu động.
    """
    dynamic_ids = {fid for fid, fn in graph.functions.items() if fn.dynamic_time_pct is not None}
    if not dynamic_ids:
        return {}

    A_time: dict[str, float] = {fid: graph.functions[fid].dynamic_time_pct for fid in dynamic_ids}  # type: ignore[misc]

    # Chỉ xét cạnh mà CẢ 2 đầu đều có dữ liệu động VÀ đo được time_contribution_pct
    # (tức cạnh này thực sự xảy ra trong lần profiling, không phải cạnh tĩnh suông).
    dyn_edges = [
        e for e in graph.call_edges
        if e.caller in dynamic_ids and e.callee in dynamic_ids and e.time_contribution_pct is not None
    ]

    callee_of: dict[str, list] = {}     # i -> [CallEdge(i, j), ...]  (callee(i))
    incoming_by_callee: dict[str, list] = {}  # j -> [CallEdge(i, j), ...]  (caller(j))
    for e in dyn_edges:
        callee_of.setdefault(e.caller, []).append(e)
        incoming_by_callee.setdefault(e.callee, []).append(e)

    # Eq.1
    weight: dict[tuple[str, str], float] = {}
    for j, incoming in incoming_by_callee.items():
        denom = sum(e.time_contribution_pct for e in incoming)  # type: ignore[misc]
        if denom <= 0:
            continue
        for e in incoming:
            weight[(e.caller, e.callee)] = e.time_contribution_pct / denom  # type: ignore[operator]

    # Eq.2, lặp tới hội tụ
    I: dict[str, float] = dict(A_time)
    for iteration in range(max_iter):
        new_I: dict[str, float] = {}
        max_delta = 0.0
        for i in dynamic_ids:
            propagated = sum(
                weight.get((i, e.callee), 0.0) * I.get(e.callee, 0.0)
                for e in callee_of.get(i, [])
            )
            new_I[i] = (1 - alpha) * A_time[i] + alpha * propagated
            max_delta = max(max_delta, abs(new_I[i] - I[i]))
        I = new_I
        if max_delta < epsilon:
            logger.info("func_rank_dynamic hội tụ sau %d vòng lặp (delta=%.2e).", iteration + 1, max_delta)
            break
    else:
        logger.warning(
            "func_rank_dynamic KHÔNG hội tụ sau %d vòng lặp (delta cuối=%.2e) -- "
            "dùng giá trị hiện tại.", max_iter, max_delta,
        )

    return I


def func_rank(graph: ProgramGraph) -> dict[str, tuple[float, str]]:
    """FuncRank TỔNG HỢP: kết hợp `func_rank_dynamic()` (ưu tiên, nếu hàm có
    dữ liệu profiling thật) và `func_rank_static()` (fallback cho hàm KHÔNG
    có dữ liệu động -- xem POLO Section 3.2: runtime analysis không phủ hết
    mọi code path, vd nhánh if-else không chạy tới với workload đang dùng,
    cần static analysis bổ sung cho lỗ hổng này).

    Trả về {function_id: (score, source)}, source = "dynamic" |
    "static_fallback". Khi `graph` chưa từng chạy profiling động (mọi
    FunctionNode.dynamic_time_pct đều None -- đúng trạng thái mặc định khi
    `target.graph.build_mode = "static"`), hàm này trả về TOÀN BỘ nguồn
    "static_fallback", điểm số giống hệt `func_rank_static()`.
    """
    dynamic_scores = func_rank_dynamic(graph)
    static_scores = func_rank_static(graph)

    result: dict[str, tuple[float, str]] = {}
    for fid in graph.functions:
        if fid in dynamic_scores:
            result[fid] = (dynamic_scores[fid], "dynamic")
        else:
            result[fid] = (static_scores.get(fid, 0.0), "static_fallback")
    return result


def top_k_functions(
    graph: ProgramGraph, k: int, build_mode: str = "static"
) -> list[tuple[FunctionNode, float]]:
    """Trả về tối đa k phần tử (FunctionNode, score), sắp giảm dần theo
    FuncRank (đồng hạng thì sắp theo id để deterministic).

    build_mode="static" (MẶC ĐỊNH): dùng thẳng `func_rank_static()` -- HÀNH
    VI CŨ giữ nguyên 100%, không đụng tới dữ liệu động dù graph có hay không.
    build_mode="dynamic": dùng `func_rank()` (kết hợp động+tĩnh, xem trên).

    Khi PCG không có cạnh nào (vd nhiều file độc lập, không gọi lẫn nhau --
    trường hợp 4 file gốc viraj7 trong ví dụ README), PageRank tĩnh cho điểm
    đồng đều mọi hàm; top-K khi đó chỉ là K hàm đầu tiên theo thứ tự id.
    """
    if build_mode == "static":
        scores = func_rank_static(graph)
        ranked = sorted(graph.functions.values(), key=lambda f: (-scores.get(f.id, 0.0), f.id))
        return [(f, scores.get(f.id, 0.0)) for f in ranked[:k]]

    combined = func_rank(graph)
    ranked = sorted(
        graph.functions.values(),
        key=lambda f: (-combined.get(f.id, (0.0, "static_fallback"))[0], f.id),
    )
    return [(f, combined.get(f.id, (0.0, "static_fallback"))[0]) for f in ranked[:k]]
