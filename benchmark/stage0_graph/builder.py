"""Parse target (1 file, danh sách file, hoặc cả thư mục repo) thành PCG
(Program Call Graph) + PSG (Program Structure Graph).

BACKEND: ưu tiên `tree-sitter` + `tree-sitter-python` (đúng công cụ đã chốt
trong thiết kế Stage 0 chính thức của đồ án). Nếu 2 gói này CHƯA cài được
(vd môi trường Windows chưa có compiler cho 1 số bản tree-sitter cũ, hoặc đơn
giản là chưa `pip install`), tự động fallback sang module `ast` CHUẨN của
Python (không cần cài gì thêm) -- đây là bản THAY THẾ TẠM THỜI, ghi rõ log
cảnh báo mỗi lần dùng. Khi có điều kiện cài tree-sitter, chỉ cần cài đặt gói
(`pip install tree-sitter tree-sitter-python`), KHÔNG cần sửa code gì thêm --
builder tự chuyển sang dùng tree-sitter ở lần chạy sau.

Cả 2 backend cho ra CÙNG 1 cấu trúc dữ liệu (ProgramGraph) nên phần còn lại
của hệ thống (stage0_graph/rank.py, stage0_graph/context_export.py, stage6_benchmark/bench.py)
không cần biết đang chạy backend nào.

GIỚI HẠN ĐÃ BIẾT (bản đơn giản, cố ý không làm resolve type/scope đầy đủ):
  - Nhận diện lời gọi hàm chỉ dựa trên TÊN (identifier cuối cùng), không
    phân biệt được 2 hàm trùng tên ở 2 module khác nhau nếu cả 2 cùng nằm
    trong scope và không cùng file với caller -- khi đó bỏ qua, log cảnh báo,
    KHÔNG đoán bừa (xem _resolve_call_edges).
  - Resolve import (PSG) chỉ khớp theo tên module/đường dẫn dotted đơn giản,
    không xử lý đầy đủ mọi kiểu import lồng gói (namespace package, star
    import, importlib động, ...).
  - `calls_raw` của 1 hàm gồm cả lời gọi bên trong hàm LỒNG bên trong nó
    (closure) -- có thể đếm dư nhẹ, chấp nhận được ở mức scaffold.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path, PurePosixPath

from .models import CallEdge, FileNode, FunctionNode, ProgramGraph

logger = logging.getLogger("benchmark.stage0_graph.builder")

_IGNORE_DIR_NAMES = {
    "__pycache__", ".git", ".venv", "venv", "env", "target",
    "node_modules", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    # --- Thư mục do PHA A..D tạo BÊN TRONG bản copy của repo ---
    # Bắt buộc phải loại: `.rtb_venv` chứa toàn bộ pytest + phụ thuộc của repo,
    # nên nếu quét vào đó thì FuncRank xếp hạng hàm nội bộ của pytest
    # (`__init__` trùng 354 nơi) thay vì hàm của repo, và hotspot chọn ra
    # không phải code cần dịch.
    ".rtb_venv", ".rtb_capture", ".rtb_crates",
}

# --- Chọn backend: tree-sitter (thật, ưu tiên) hay ast (fallback tạm thời) ---
try:
    import tree_sitter_python as _tspython
    from tree_sitter import Language, Parser, Query, QueryCursor

    _PY_LANGUAGE = Language(_tspython.language())
    _FUNC_QUERY = Query(_PY_LANGUAGE, "(function_definition name: (identifier) @name) @def")
    _CALL_QUERY = Query(_PY_LANGUAGE, "(call function: (_) @func) @call")
    _IMPORT_QUERY = Query(
        _PY_LANGUAGE,
        "[(import_statement) @imp (import_from_statement) @imp]",
    )
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False
    logger.warning(
        "Chưa cài tree-sitter/tree-sitter-python (pip install tree-sitter "
        "tree-sitter-python) -> stage0_graph/builder.py dùng fallback bằng module "
        "`ast` chuẩn của Python thay thế TẠM THỜI. tree-sitter là công cụ đã "
        "chốt trong thiết kế Stage 0 chính thức của đồ án -- cài đặt lại khi "
        "có điều kiện, code sẽ tự chuyển sang dùng tree-sitter mà không cần "
        "sửa gì thêm."
    )


def discover_python_files(target: Path) -> list[Path]:
    """target: 1 file .py, hoặc 1 thư mục (quét đệ quy *.py, bỏ qua các thư
    mục rác thường gặp: __pycache__, .git, .venv, target, node_modules, ...).
    """
    target = Path(target)
    if target.is_file():
        if target.suffix != ".py":
            raise ValueError(f"target.path trỏ tới file không phải .py: {target}")
        return [target]
    if not target.is_dir():
        raise FileNotFoundError(f"target.path không tồn tại: {target}")

    files: list[Path] = []
    for p in sorted(target.rglob("*.py")):
        if any(part in _IGNORE_DIR_NAMES for part in p.parts):
            continue
        files.append(p)
    return files


def build_graph(files: list[Path], root: Path) -> ProgramGraph:
    """Xây PCG + PSG từ danh sách file .py. `root` dùng để tính đường dẫn
    tương đối hiển thị trong id/context JSON (không lộ absolute path của máy
    chạy benchmark)."""
    root = Path(root).resolve()
    parse_file = _parse_file_tree_sitter if HAS_TREE_SITTER else _parse_file_ast
    backend = "tree-sitter" if HAS_TREE_SITTER else "ast-fallback"

    functions: dict[str, FunctionNode] = {}
    files_map: dict[str, FileNode] = {}

    for path in files:
        try:
            file_funcs, file_node = parse_file(path, root)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            logger.warning("Bỏ qua file không parse được: %s (%s: %s)", path, type(exc).__name__, exc)
            continue
        files_map[file_node.path] = file_node
        for fn in file_funcs:
            if fn.id in functions:
                logger.warning("Trùng function id (bất thường, bỏ qua bản sau): %s", fn.id)
                continue
            functions[fn.id] = fn

    call_edges, call_resolution = _resolve_call_edges(functions)
    import_edges = _resolve_import_edges(files_map)

    graph = ProgramGraph(
        functions=functions,
        call_edges=call_edges,
        files=files_map,
        import_edges=import_edges,
        backend=backend,
        call_resolution=call_resolution,
    )

    # --- PSG ĐẦY ĐỦ (POLO Table 1): class, biến toàn cục, kế thừa, sở hữu ---
    # Thêm vào field RIÊNG, KHÔNG chạm `files`/`import_edges` (PSG rút gọn) vì
    # stage3_context_packaging đang dùng chúng. Thất bại ở đây chỉ làm mất
    # thông tin bổ sung, không làm sập Stage 0 -- xem psg.build_rich_psg.
    from .psg import build_rich_psg

    rich = build_rich_psg(files, root, functions)
    graph.classes = rich.classes
    graph.global_vars = rich.global_vars
    graph.inheritance_edges = rich.inheritance_edges
    graph.ownership_edges = rich.ownership_edges
    graph.psg_backend = rich.backend
    return graph


def _rel_posix(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        rel = path.resolve()
    return rel.as_posix()


# =============================================================================
# Backend 1: tree-sitter (thật, ưu tiên)
# =============================================================================

_ts_parser: "Parser | None" = None


def _get_ts_parser():
    global _ts_parser
    if _ts_parser is None:
        _ts_parser = Parser(_PY_LANGUAGE)
    return _ts_parser


def _qualified_prefix_ts(def_node, src: bytes) -> str:
    """Ghép tên các class/hàm bao ngoài def_node (vd 'ClassName' hoặc
    'outer_func'), theo thứ tự ngoài -> trong."""
    parts: list[str] = []
    node = def_node.parent
    while node is not None:
        if node.type in ("class_definition", "function_definition"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                parts.append(src[name_node.start_byte:name_node.end_byte].decode("utf-8"))
        node = node.parent
    parts.reverse()
    return ".".join(parts)


def _simple_call_name_ts(func_node, src: bytes) -> str | None:
    if func_node.type == "identifier":
        return src[func_node.start_byte:func_node.end_byte].decode("utf-8")
    if func_node.type == "attribute":
        attr_node = func_node.child_by_field_name("attribute")
        if attr_node is not None:
            return src[attr_node.start_byte:attr_node.end_byte].decode("utf-8")
    return None


def _extract_calls_ts(def_node, src: bytes) -> list[str]:
    names: list[str] = []
    cursor = QueryCursor(_CALL_QUERY)
    for _, captures in cursor.matches(def_node):
        func_node = captures["func"][0]
        name = _simple_call_name_ts(func_node, src)
        if name:
            names.append(name)
    return names


def _extract_imports_ts(root_node, src: bytes) -> list[str]:
    raw: list[str] = []
    cursor = QueryCursor(_IMPORT_QUERY)
    for _, captures in cursor.matches(root_node):
        node = captures["imp"][0]
        raw.append(src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip())
    return raw


def _parse_file_tree_sitter(path: Path, root: Path) -> tuple[list[FunctionNode], FileNode]:
    src = path.read_bytes()
    rel_path = _rel_posix(path, root)
    tree = _get_ts_parser().parse(src)
    root_node = tree.root_node

    funcs: list[FunctionNode] = []
    cursor = QueryCursor(_FUNC_QUERY)
    for _, captures in cursor.matches(root_node):
        def_node = captures["def"][0]
        name_node = captures["name"][0]
        name = src[name_node.start_byte:name_node.end_byte].decode("utf-8")
        prefix = _qualified_prefix_ts(def_node, src)
        qualified = f"{prefix}.{name}" if prefix else name
        lineno_start = def_node.start_point[0] + 1
        lineno_end = def_node.end_point[0] + 1
        source = src[def_node.start_byte:def_node.end_byte].decode("utf-8", errors="replace")
        fid = f"{rel_path}::{qualified}#L{lineno_start}"

        funcs.append(FunctionNode(
            id=fid, name=name, qualified_name=qualified, file=rel_path,
            lineno_start=lineno_start, lineno_end=lineno_end, source=source,
            calls_raw=_extract_calls_ts(def_node, src),
        ))

    imports_raw = _extract_imports_ts(root_node, src)
    return funcs, FileNode(path=rel_path, imports_raw=imports_raw)


# =============================================================================
# Backend 2: ast (fallback tạm thời, không cần cài gì thêm)
# =============================================================================

def _simple_call_name_ast(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _extract_calls_ast(func_node: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            name = _simple_call_name_ast(node.func)
            if name:
                names.append(name)
    return names


def _parse_file_ast(path: Path, root: Path) -> tuple[list[FunctionNode], FileNode]:
    src_text = path.read_text(encoding="utf-8", errors="replace")
    rel_path = _rel_posix(path, root)
    tree = ast.parse(src_text, filename=str(path))  # có thể raise SyntaxError -- caller bắt

    funcs: list[FunctionNode] = []
    imports_raw: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope_stack: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self.scope_stack.append(node.name)
            self.generic_visit(node)
            self.scope_stack.pop()

        def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            prefix = ".".join(self.scope_stack)
            qualified = f"{prefix}.{node.name}" if prefix else node.name
            lineno_start = node.lineno
            lineno_end = getattr(node, "end_lineno", node.lineno) or node.lineno
            source = ast.get_source_segment(src_text, node) or ""
            fid = f"{rel_path}::{qualified}#L{lineno_start}"
            funcs.append(FunctionNode(
                id=fid, name=node.name, qualified_name=qualified, file=rel_path,
                lineno_start=lineno_start, lineno_end=lineno_end, source=source,
                calls_raw=_extract_calls_ast(node),
            ))
            self.scope_stack.append(node.name)
            self.generic_visit(node)
            self.scope_stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_func(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_func(node)

        def visit_Import(self, node: ast.Import) -> None:
            imports_raw.append(ast.unparse(node))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            imports_raw.append(ast.unparse(node))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return funcs, FileNode(path=rel_path, imports_raw=imports_raw)


# =============================================================================
# Resolve PCG call edges (dùng chung cho cả 2 backend)
# =============================================================================

def _resolve_call_edges(
    functions: dict[str, FunctionNode]
) -> tuple[list[CallEdge], dict]:
    """Trả về list[CallEdge] cho PCG TĨNH -- count=0, time_contribution_pct=None
    (chỉ biết CÓ quan hệ gọi hàm qua phân tích tĩnh, chưa đo tần suất/thời
    gian thật; stage1_profiling/dynamic_profiler.py::apply_dynamic_profile sẽ điền thêm
    2 giá trị này khi target.graph.build_mode = "dynamic")."""
    name_index: dict[str, list[str]] = {}
    for fid, fn in functions.items():
        name_index.setdefault(fn.name, []).append(fid)

    edges: list[CallEdge] = []
    warned_names: set[str] = set()
    n_exact = n_heuristic = n_unresolved = n_out_of_scope = 0
    for fid, fn in functions.items():
        for raw_name in fn.calls_raw:
            candidates = name_index.get(raw_name)
            if not candidates:
                # Gọi hàm NGOÀI scope (thư viện, stdlib) -- không phải lỗi, và
                # KHÔNG tính vào unresolved: nó không nói gì về chất lượng graph.
                n_out_of_scope += 1
                continue
            if len(candidates) == 1:
                edges.append(CallEdge(caller=fid, callee=candidates[0], resolution="exact"))
                n_exact += 1
                continue
            same_file = [c for c in candidates if functions[c].file == fn.file]
            if len(same_file) == 1:
                edges.append(CallEdge(
                    caller=fid, callee=same_file[0], resolution="heuristic"
                ))
                n_heuristic += 1
                continue
            # Không đoán được -> ghi nhận là MÙ, cả ở mức graph và mức hàm.
            n_unresolved += 1
            fn.unresolved_call_count += 1
            if raw_name not in warned_names:
                logger.warning(
                    "PCG: tên hàm '%s' trùng ở %d nơi trong scope -- bỏ qua "
                    "resolve cạnh gọi hàm cho tên này (bản đơn giản, không "
                    "resolve theo type/scope thật, không đoán bừa).",
                    raw_name, len(candidates),
                )
                warned_names.add(raw_name)

    total = n_exact + n_heuristic + n_unresolved
    stats = {
        "n_exact": n_exact,
        "n_heuristic": n_heuristic,
        "n_unresolved": n_unresolved,
        "n_out_of_scope": n_out_of_scope,
        "n_in_scope_calls": total,
        "exact_ratio": (n_exact / total) if total else 0.0,
        "heuristic_ratio": (n_heuristic / total) if total else 0.0,
        "unresolved_ratio": (n_unresolved / total) if total else 0.0,
    }
    return edges, stats


# =============================================================================
# Resolve PSG import edges (dùng chung cho cả 2 backend -- tận dụng ast.parse
# trên TỪNG câu import riêng lẻ, luôn là Python hợp lệ độc lập với backend
# nào đã trích ra text của nó)
# =============================================================================

def _module_candidates_for_file(rel_path: str) -> set[str]:
    """Các tên module có thể dùng để `import` được file này: stem (tên file
    không đuôi .py) và dotted-path tương đối scope root."""
    p = PurePosixPath(rel_path)
    stem = p.stem
    if stem == "__init__":
        pkg = str(p.parent).replace("\\", "/")
        if pkg in (".", ""):
            return set()
        dotted = pkg.replace("/", ".")
        return {dotted, PurePosixPath(pkg).name}
    dotted = rel_path[:-3].replace("/", ".") if rel_path.endswith(".py") else rel_path.replace("/", ".")
    return {stem, dotted}


def _import_targets_for_file(imports_raw: list[str], file_rel_path: str) -> list[str]:
    """Trả về danh sách tên module (dotted, best-effort) mà file này có thể
    đang import, để thử khớp với các file khác trong scope."""
    file_dir = str(PurePosixPath(file_rel_path).parent)
    dir_hint = "" if file_dir in (".", "") else file_dir.replace("/", ".")

    targets: list[str] = []
    for stmt in imports_raw:
        try:
            tree = ast.parse(stmt)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    targets.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # import tương đối -- ghép với thư mục chứa file đang xét
                    if node.module:
                        targets.append(f"{dir_hint}.{node.module}" if dir_hint else node.module)
                    for alias in node.names:
                        targets.append(f"{dir_hint}.{alias.name}" if dir_hint else alias.name)
                elif node.module:
                    targets.append(node.module)
                    for alias in node.names:
                        targets.append(f"{node.module}.{alias.name}")
    return targets


def _resolve_import_edges(files_map: dict[str, FileNode]) -> list[tuple[str, str]]:
    module_index: dict[str, list[str]] = {}
    for path in files_map:
        for cand in _module_candidates_for_file(path):
            module_index.setdefault(cand, []).append(path)

    edges: list[tuple[str, str]] = []
    warned: set[str] = set()
    for path, fnode in files_map.items():
        for target in _import_targets_for_file(fnode.imports_raw, path):
            found = [f for f in module_index.get(target, []) if f != path]
            if len(found) == 1:
                edges.append((path, found[0]))
            elif len(found) > 1 and target not in warned:
                logger.warning(
                    "PSG: import '%s' trong %s khớp nhiều file trong scope (%s) "
                    "-- bỏ qua, không đoán bừa.", target, path, found,
                )
                warned.add(target)
    return edges
