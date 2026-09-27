"""Stage 2 -- Decision Gate: pre-filter hotspot TRƯỚC khi dịch sang Rust.

Với mỗi hàm hotspot (do FuncRank của Stage 0/1 xếp hạng), gán 1 trong 3 nhãn:

  "skip"                        -- hàm chỉ ghép lệnh của thư viện đã tối ưu
                                   sẵn (numpy/scipy/cv2/pandas/torch). Dịch
                                   sang Rust gần như vô nghĩa vì phần nặng
                                   đã nằm trong C/Fortran của thư viện rồi.
  "suggest_numpy_vectorization" -- có vòng lặp lồng nhau index vào mảng số,
                                   NHƯNG thân vòng lặp đơn giản (ít lệnh,
                                   không rẽ nhánh phức tạp) -> vector hoá
                                   bằng numpy thường rẻ và hiệu quả hơn là
                                   bỏ công dịch sang Rust.
  "candidate"                   -- code tự viết tay, vòng lặp nặng + logic
                                   rẽ nhánh phức tạp (khó vector hoá) ->
                                   ĐÁNG đưa qua Stage 3/4 để dịch sang Rust.

=== PHÂN BIỆT VỚI DECISION AGENT (Stage 6) ===
Module này là bộ lọc TRƯỚC khi dịch: quyết định "có đáng dịch không", dựa
thuần tuý trên đặc điểm tĩnh của source code, KHÔNG gọi LLM, KHÔNG cần biết
kết quả đo tốc độ.
`stage4_llm_transpile/decision_agent.py` mới là thứ chạy SAU khi đã dịch và
đã benchmark, để quyết định accept/reject bản Rust đó. Hai khái niệm này cố
ý dùng tên khác nhau hoàn toàn (`classify_functions`/`GateLabel` ở đây, so
với `decide_after_benchmark`/`AgentDecision` ở Stage 6) để không ai nhầm.

TODO (hệ thống chính): thay heuristic rule-based ở đây bằng learned MoE gate
(mixture-of-experts) được huấn luyện trên dữ liệu speedup thật đo được. Giữ
nguyên chữ ký `classify_functions(...) -> dict[str, str]` để đổi ruột mà
không phải sửa run_pipeline.py.
"""
from __future__ import annotations

import ast
import logging
import re
import textwrap
from typing import Iterable

logger = logging.getLogger("benchmark.stage2_decision_gate.gate")

LABEL_SKIP = "skip"
LABEL_VECTORIZE = "suggest_numpy_vectorization"
LABEL_CANDIDATE = "candidate"

# Thư viện coi như "đã tối ưu sẵn" (phần nặng chạy trong C/Fortran/CUDA).
OPTIMIZED_LIBS = {
    "np", "numpy", "scipy", "cv2", "pd", "pandas", "torch", "sklearn",
    "skimage", "PIL", "Image", "numba", "cupy", "tf", "tensorflow",
}

# Tỉ lệ lệnh gọi thư viện tối ưu / tổng số lệnh, từ mức này trở lên thì coi
# hàm chỉ là lớp vỏ mỏng bọc thư viện -> "skip".
SKIP_LIB_RATIO = 0.5
# Thân vòng lặp trong cùng có <= ngần này lệnh VÀ không rẽ nhánh -> dễ vector hoá.
SIMPLE_LOOP_MAX_STATEMENTS = 5


def _dedent_source(source: str) -> str:
    """Source của method trong class bị thụt lề -> ast.parse sẽ lỗi nếu không
    dedent trước."""
    return textwrap.dedent(source or "")


def _function_body(tree: ast.AST) -> list[ast.stmt]:
    """Thân hàm (đã bỏ docstring). Nếu `tree` không phải 1 định nghĩa hàm đơn
    lẻ thì trả về toàn bộ body của module."""
    node = tree
    if isinstance(tree, ast.Module) and len(tree.body) == 1:
        node = tree.body[0]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body = list(node.body)
    elif isinstance(tree, ast.Module):
        body = list(tree.body)
    else:
        return []
    # bỏ docstring ở đầu
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return body


def _call_root_name(node: ast.AST) -> str | None:
    """`np.zeros(...)` -> "np"; `foo(...)` -> "foo"; `a.b.c(...)` -> "a"."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_root_name(node.value)
    return None


def _loop_bodies(tree: ast.AST) -> Iterable[list[ast.stmt]]:
    """Trả về thân của các vòng lặp TRONG CÙNG (không chứa vòng lặp khác)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            has_inner_loop = any(
                isinstance(sub, (ast.For, ast.While)) and sub is not node
                for sub in ast.walk(node)
            )
            if not has_inner_loop:
                yield node.body


def _has_nested_loop(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While)):
            for sub in ast.walk(node):
                if sub is not node and isinstance(sub, (ast.For, ast.While)):
                    return True
    return False


def _has_subscript_indexing(tree: ast.AST) -> bool:
    """Có `x[i]` / `x[i][j]` / `x[i, j]` -- dấu hiệu thao tác phần tử mảng."""
    return any(isinstance(node, ast.Subscript) for node in ast.walk(tree))


def _has_elementwise_transform(tree: ast.AST) -> bool:
    """Bắt pattern biến đổi TỪNG PHẦN TỬ của 1 chuỗi bằng biểu thức số học:
        map(lambda x: <số học>, seq)      hoặc
        [<số học> for x in seq]           (list/set/generator/dict comp)
    Đây là trường hợp kinh điển nên thay bằng numpy vectorization (mảng ufunc)
    chứ không phải bỏ công dịch sang Rust."""
    for node in ast.walk(tree):
        # map(lambda x: ..., seq)
        if isinstance(node, ast.Call) and _call_root_name(node.func) == "map":
            for arg in node.args:
                if isinstance(arg, ast.Lambda) and any(
                    isinstance(sub, (ast.BinOp, ast.UnaryOp)) for sub in ast.walk(arg.body)
                ):
                    return True
        # [x*2 for x in seq] và các dạng comprehension khác
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            if any(isinstance(sub, (ast.BinOp, ast.UnaryOp)) for sub in ast.walk(node.elt)):
                return True
    return False


def _classify_with_ast(source: str) -> tuple[str, str]:
    """Trả về (label, lý_do). Raise SyntaxError nếu source không parse được."""
    tree = ast.parse(_dedent_source(source))

    # Đếm lệnh trong THÂN hàm, bỏ chính node `def` và docstring -- nếu tính cả
    # 2 thứ đó thì 1 hàm 2 dòng gọi numpy sẽ bị loãng tỉ lệ và trượt nhãn skip.
    body = _function_body(tree)
    statements = [n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.stmt)]
    n_statements = max(len(statements), 1)

    lib_calls = 0
    total_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            total_calls += 1
            root = _call_root_name(node.func)
            if root in OPTIMIZED_LIBS:
                lib_calls += 1

    nested = _has_nested_loop(tree)
    indexing = _has_subscript_indexing(tree)
    lib_ratio = lib_calls / n_statements

    # 1) Vỏ mỏng bọc thư viện đã tối ưu + không có vòng lặp lồng -> skip.
    if lib_ratio >= SKIP_LIB_RATIO and not nested:
        return (
            LABEL_SKIP,
            f"{lib_calls}/{total_calls} lời gọi là thư viện đã tối ưu "
            f"(tỉ lệ lệnh {lib_ratio:.2f} >= {SKIP_LIB_RATIO}), không có vòng lặp lồng",
        )

    # 2) Vòng lặp lồng + index mảng: đơn giản -> vector hoá, phức tạp -> dịch Rust.
    if nested and indexing:
        for body in _loop_bodies(tree):
            body_stmts = [n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.stmt)]
            has_branch = any(isinstance(n, (ast.If, ast.Try, ast.Match)) for n in body_stmts)
            if has_branch or len(body_stmts) > SIMPLE_LOOP_MAX_STATEMENTS:
                return (
                    LABEL_CANDIDATE,
                    f"vòng lặp lồng + index mảng, thân vòng lặp phức tạp "
                    f"({len(body_stmts)} lệnh, rẽ nhánh={has_branch}) -> khó vector hoá",
                )
        return (
            LABEL_VECTORIZE,
            "vòng lặp lồng index vào mảng nhưng thân đơn giản (không rẽ nhánh, "
            f"<= {SIMPLE_LOOP_MAX_STATEMENTS} lệnh) -> nên thử numpy vectorization trước",
        )

    # 3) Không lồng nhau nhưng vẫn là vỏ thư viện -> skip.
    if lib_ratio >= SKIP_LIB_RATIO:
        return (
            LABEL_SKIP,
            f"chủ yếu gọi thư viện đã tối ưu (tỉ lệ {lib_ratio:.2f})",
        )

    # 4) Biến đổi elementwise qua map(lambda)/comprehension -> vector hoá numpy.
    if _has_elementwise_transform(tree):
        return (
            LABEL_VECTORIZE,
            "biến đổi elementwise qua map(lambda)/comprehension trên 1 chuỗi số "
            "-> thay bằng numpy ufunc rẻ hơn nhiều so với dịch Rust",
        )

    return (LABEL_CANDIDATE, "code tự viết, không phải vỏ mỏng bọc thư viện")


def _classify_with_text(source: str) -> tuple[str, str]:
    """Fallback khi `ast.parse` lỗi -- điển hình là source cú pháp Python 2
    (`print bien`) trong repo viraj7. Dùng heuristic trên text thô."""
    text = source or ""
    code_lines = [
        ln for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    n_lines = max(len(code_lines), 1)

    loop_lines = len(re.findall(r"^\s*(for|while)\b", text, flags=re.MULTILINE))
    branch_lines = len(re.findall(r"^\s*(if|elif)\b", text, flags=re.MULTILINE))
    lib_hits = sum(
        len(re.findall(rf"\b{re.escape(lib)}\s*\.", text)) for lib in OPTIMIZED_LIBS
    )
    indexing = bool(re.search(r"\w\s*\[[^\]]+\]", text))

    lib_ratio = lib_hits / n_lines

    if loop_lines >= 2 and indexing:
        if branch_lines > 0:
            return (
                LABEL_CANDIDATE,
                f"[text-fallback] {loop_lines} vòng lặp + {branch_lines} nhánh if/elif "
                "-> khó vector hoá",
            )
        return (
            LABEL_VECTORIZE,
            f"[text-fallback] {loop_lines} vòng lặp index mảng, không rẽ nhánh",
        )
    if lib_ratio >= SKIP_LIB_RATIO and loop_lines == 0:
        return (LABEL_SKIP, f"[text-fallback] chủ yếu gọi thư viện (tỉ lệ {lib_ratio:.2f})")
    return (LABEL_CANDIDATE, "[text-fallback] code tự viết, không rõ pattern vector hoá")


def classify_function_source(source: str) -> tuple[str, str]:
    """Phân loại 1 hàm từ source code của nó. Trả về (label, lý_do)."""
    try:
        return _classify_with_ast(source)
    except SyntaxError:
        return _classify_with_text(source)
    except RecursionError:  # source bệnh hoạn, không để nó phá cả pipeline
        return (LABEL_CANDIDATE, "không phân tích được AST (quá sâu) -- mặc định candidate")


def classify_functions(graph, function_names: list[str] | None = None) -> dict[str, str]:
    """HÀM CHÍNH của Stage 2.

    graph: ProgramGraph từ stage0_graph/builder.py.
    function_names: giới hạn ở danh sách hàm này (thường là top-K hotspot từ
        FuncRank). None = phân loại mọi hàm trong graph.

    Trả về {tên hàm: nhãn}. Hàm trùng tên ở nhiều file: lấy nhãn "nặng" nhất
    (candidate > suggest_numpy_vectorization > skip) để không bỏ sót hàm đáng
    dịch chỉ vì 1 bản trùng tên ở file khác đơn giản hơn.
    """
    priority = {LABEL_SKIP: 0, LABEL_VECTORIZE: 1, LABEL_CANDIDATE: 2}
    wanted = set(function_names) if function_names else None

    labels: dict[str, str] = {}
    for fn in graph.functions.values():
        if wanted is not None and fn.name not in wanted:
            continue
        label, reason = classify_function_source(fn.source)
        logger.info("Gate[%s] %s (%s:%d) -- %s", label, fn.name, fn.file, fn.lineno_start, reason)
        prev = labels.get(fn.name)
        if prev is None or priority[label] > priority[prev]:
            labels[fn.name] = label

    if wanted:
        for name in wanted:
            if name not in labels:
                logger.warning(
                    "Gate: không tìm thấy hàm '%s' trong graph -- mặc định nhãn "
                    "'%s' để không chặn nhầm.", name, LABEL_CANDIDATE,
                )
                labels[name] = LABEL_CANDIDATE

    summary: dict[str, int] = {}
    for label in labels.values():
        summary[label] = summary.get(label, 0) + 1
    logger.info("Gate tổng kết: %s", summary)
    return labels


if __name__ == "__main__":
    import sys
    from pathlib import Path

    BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(BENCHMARK_ROOT))
    from config_loader import ensure_utf8_stdio
    from stage0_graph.builder import build_graph, discover_python_files

    ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else BENCHMARK_ROOT / "data" / "reference_repo"
    if not target.is_absolute():
        target = BENCHMARK_ROOT / target
    files = discover_python_files(target)
    graph = build_graph(files, target if target.is_dir() else target.parent)
    print(f"\nTarget: {target}  ({len(graph.functions)} hàm)\n")
    for name, label in sorted(classify_functions(graph).items()):
        print(f"  {name:24s} -> {label}")
