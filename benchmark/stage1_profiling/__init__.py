"""Stage 1 -- Profiling động (runtime analysis).

Chạy target dưới profiler thật (Scalene, fallback cProfile) với workload
thật để đo % thời gian + số lần gọi thật của từng hàm và từng cạnh gọi hàm,
làm đầu vào cho FuncRank động (stage0_graph/rank.py::func_rank_dynamic) theo
phương pháp POLO (Bai et al., IJCAI-25, Section 3.1).

Xem dynamic_profiler.py.
"""
