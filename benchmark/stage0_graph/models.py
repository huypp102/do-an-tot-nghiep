"""Cấu trúc dữ liệu dùng chung cho PCG (Program Call Graph) và PSG (Program
Structure Graph). Dùng dataclass đơn giản (không ORM/pydantic) vì graph/ chỉ
cần build 1 lần rồi export JSON, không cần validate phức tạp."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FunctionNode:
    """1 node trong PCG -- ứng với 1 định nghĩa hàm/method trong source.

    id: định danh DUY NHẤT trong toàn bộ graph, dạng
        "<đường dẫn tương đối>::<tên đủ (qualified)>#L<dòng bắt đầu>"
        (thêm số dòng để tránh đụng độ khi trùng tên, vd overload theo scope
        khác nhau hoặc hàm lồng nhau cùng tên).
    name: tên hàm "trần" (không kèm class/scope bao ngoài) -- dùng để khớp
        với PIPELINE_REGISTRY của versions/python_pure, rust_pure, hybrid_pyo3
        (các registry đó chỉ có tên trần, không phân biệt class).
    qualified_name: tên đầy đủ kèm scope bao ngoài, vd "ClassName.method".
    calls_raw: danh sách tên (chưa resolve) của các lời gọi hàm xuất hiện
        trong thân hàm này -- vd gọi `foo()` -> "foo", `self.bar()` -> "bar",
        `np.array()` -> "array" (lấy phần cuối của attribute access). Việc
        resolve các tên này thành cạnh PCG thật (edge tới FunctionNode.id cụ
        thể) làm ở stage0_graph/builder.py::_resolve_call_edges, SAU KHI đã thu thập
        hết mọi hàm trong scope (cần biết toàn bộ tên hàm mới resolve được).

    dynamic_time_pct: % thời gian SELF (exclusive, không tính hàm con) mà hàm
        này tiêu tốn trong lần profiling ĐỘNG gần nhất (stage1_profiling/dynamic_profiler.py),
        tính trên tổng self-time mọi hàm -- ứng với Ac_i[time] trong công
        thức POLO (Eq.1-2, Bai et al., IJCAI-25). None nghĩa là hàm này
        KHÔNG được thực thi trong lần profiling đó (không có implementation
        callable, hoặc code path không được chạy tới với workload đang dùng
        -- xem POLO Section 3.2 về hạn chế của runtime analysis).
    dynamic_call_count: tổng số lần hàm này được gọi (bất kể ai gọi) trong
        lần profiling động gần nhất. 0 nếu không có dữ liệu động.
    """

    id: str
    name: str
    qualified_name: str
    file: str
    lineno_start: int
    lineno_end: int
    source: str
    calls_raw: list[str] = field(default_factory=list)
    dynamic_time_pct: float | None = None
    dynamic_call_count: int = 0


@dataclass
class CallEdge:
    """1 cạnh (caller, callee) trong PCG -- ứng với Ac_ij = {type, count,
    time} trong công thức POLO (Bai et al., IJCAI-25, Section 3.1, Eq.1-2).

    caller/callee: FunctionNode.id của 2 đầu cạnh.
    count: số lần caller gọi callee, đo được qua profiling ĐỘNG
        (stage1_profiling/dynamic_profiler.py). 0 nếu build_mode=static (chỉ biết CÓ
        quan hệ gọi hàm qua phân tích tĩnh, không biết tần suất thật) hoặc
        cạnh này không xuất hiện trong lần chạy workload được profiling.
    time_contribution_pct: % ĐÓNG GÓP thời gian của riêng lời gọi này trên
        tổng thời gian chương trình (cumulative, gồm cả hàm con của callee),
        đo được qua profiling ĐỘNG. None nếu build_mode=static hoặc cạnh
        không được thực thi trong lần profiling.
    """

    caller: str
    callee: str
    count: int = 0
    time_contribution_pct: float | None = None


@dataclass
class FileNode:
    """1 node trong PSG -- ứng với 1 file .py trong scope.

    imports_raw: danh sách text nguyên văn từng câu import trong file (vd
        "import os", "from foo.bar import baz"). Việc resolve các câu import
        này thành cạnh PSG thật (file -> file khác TRONG SCOPE) làm ở
        stage0_graph/builder.py::_resolve_import_edges.
    """

    path: str
    imports_raw: list[str] = field(default_factory=list)


@dataclass
class ProgramGraph:
    """Kết quả build: PCG = (functions, call_edges); PSG = (files, import_edges).

    build_mode: "static" (mặc định, hành vi CŨ giữ nguyên -- chỉ phân tích
        tĩnh) hoặc "dynamic" (đã áp thêm dữ liệu profiling thật qua
        stage1_profiling/dynamic_profiler.py::apply_dynamic_profile, xem
        stage0_graph/rank.py::func_rank). Chỉ mang tính thông tin (ghi vào context
        JSON), bản thân ProgramGraph không tự đổi hành vi theo field này.
    """

    functions: dict[str, FunctionNode]
    call_edges: list[CallEdge]
    files: dict[str, FileNode]
    import_edges: list[tuple[str, str]]
    backend: str  # "tree-sitter" | "ast-fallback"
    build_mode: str = "static"  # "static" | "dynamic"
