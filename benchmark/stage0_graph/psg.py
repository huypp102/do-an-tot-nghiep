"""PSG ĐẦY ĐỦ theo POLO Table 1 -- class, biến toàn cục, kế thừa, sở hữu.

VỊ TRÍ TRONG HỆ THỐNG (đọc kỹ, ba khái niệm dễ nhầm nhau):

  * **PCG (call graph, tĩnh trước, làm giàu bằng runtime nếu có)** --
    `ProgramGraph.functions` + `.call_edges`. Khác POLO gốc: POLO dựng PCG
    hoàn toàn từ runtime (Callgrind), còn ở đây PCG dựng TĨNH bằng
    tree-sitter rồi mới làm giàu bằng số liệu Scalene khi `build_mode=dynamic`.
  * **PSG rút gọn (chỉ import)** -- `ProgramGraph.files` + `.import_edges`.
    Đây là thứ trước nay code gọi là "PSG", nhưng nó chỉ có quan hệ import
    giữa các FILE. Giữ nguyên, không đổi hành vi, vì
    `stage3_context_packaging` đang dùng nó.
  * **PSG đầy đủ (module này)** -- mới thực sự là đối tác của PSG trong POLO
    Table 1: có node Class và Global variable, có cạnh kế thừa
    (superclassOf/subclassOf) và cạnh sở hữu (hasmember/ismember).

CỐ Ý ĐẶT FIELD RIÊNG (`graph.classes`, `graph.global_vars`,
`graph.inheritance_edges`, `graph.ownership_edges`) chứ không nhồi vào
`files`/`import_edges`: PSG rút gọn đang được dùng ở chỗ khác, ghi đè lên nó
là cách nhanh nhất để phá context packeting đang chạy được.

Tại sao PSG đầy đủ đáng có: một hotspot là method thì thông tin quan trọng
nhất về nó không nằm ở "file nào import file nào", mà ở "nó thuộc class nào,
class đó kế thừa ai, và nó gọi method nào khác cùng class". Đó là thứ
Generator Agent cần để viết được shim Tầng 2 đúng.
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.stage0_graph.psg")

# Loại cạnh, đặt tên theo đúng POLO Table 1.
SUPERCLASS_OF = "superclassOf"   # cha -> con
SUBCLASS_OF = "subclassOf"       # con -> cha
HAS_MEMBER = "hasmember"         # class -> method của nó
IS_MEMBER = "ismember"           # method -> class chứa nó


@dataclass
class ClassNode:
    """1 class/struct trong PSG đầy đủ.

    `base_names` là tên lớp cha NGUYÊN VĂN trong source (`Foo`, `mod.Bar`,
    `Generic[T]`). Cố ý không resolve thành id: lớp cha có thể nằm ngoài scope
    (vd `unittest.TestCase`), và đoán bừa thì tệ hơn là ghi lại nguyên văn.
    Việc resolve sang id làm ở `_resolve_inheritance`, chỉ khi khớp chắc chắn.
    """

    id: str
    name: str
    qualified_name: str
    file: str
    lineno_start: int
    lineno_end: int
    base_names: list[str] = field(default_factory=list)
    method_ids: list[str] = field(default_factory=list)   # FunctionNode.id
    method_names: list[str] = field(default_factory=list)
    docstring_first_line: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "qualified_name": self.qualified_name,
            "file": self.file, "lineno_start": self.lineno_start,
            "lineno_end": self.lineno_end, "base_names": self.base_names,
            "method_names": self.method_names, "method_ids": self.method_ids,
            "docstring_first_line": self.docstring_first_line,
        }


@dataclass
class GlobalVarNode:
    """1 biến toàn cục / hằng cấp module.

    `assigned_in` ghi nơi biến được GÁN: "module" nếu gán ở cấp module, hoặc
    `FunctionNode.id` nếu bị gán lại bên trong một hàm (qua `global x`). Biến
    bị hàm gán lại là dấu hiệu trạng thái chia sẻ -- dịch hàm đó sang Rust sẽ
    mất hành vi đó, nên đây là thông tin cần cho prompt.
    """

    id: str
    name: str
    file: str
    lineno: int
    assigned_in: list[str] = field(default_factory=lambda: ["module"])
    is_constant_case: bool = False   # TÊN_KIỂU_HẰNG -> thường là hằng số

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "file": self.file,
            "lineno": self.lineno, "assigned_in": self.assigned_in,
            "is_constant_case": self.is_constant_case,
        }


@dataclass
class PsgEdge:
    """Cạnh trong PSG đầy đủ. `kind` là một trong 4 hằng ở đầu file."""

    src: str
    dst: str
    kind: str
    resolved: bool = True   # False khi dst chỉ là TÊN (lớp cha ngoài scope)

    def as_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "kind": self.kind,
                "resolved": self.resolved}


@dataclass
class RichPsg:
    """Toàn bộ PSG đầy đủ của một scope."""

    classes: dict[str, ClassNode] = field(default_factory=dict)
    global_vars: dict[str, GlobalVarNode] = field(default_factory=dict)
    inheritance_edges: list[PsgEdge] = field(default_factory=list)
    ownership_edges: list[PsgEdge] = field(default_factory=list)
    backend: str = ""

    def class_of_function(self, function_id: str) -> ClassNode | None:
        """Class chứa một hàm, hoặc None nếu hàm ở cấp module."""
        for edge in self.ownership_edges:
            if edge.kind == IS_MEMBER and edge.src == function_id:
                return self.classes.get(edge.dst)
        return None

    def siblings_of(self, function_id: str) -> list[str]:
        """Các method KHÁC cùng class với hàm này (theo id)."""
        cls = self.class_of_function(function_id)
        if cls is None:
            return []
        return [m for m in cls.method_ids if m != function_id]

    def stats(self) -> dict:
        return {
            "backend": self.backend,
            "n_classes": len(self.classes),
            "n_global_vars": len(self.global_vars),
            "n_inheritance_edges": len(self.inheritance_edges),
            "n_ownership_edges": len(self.ownership_edges),
            "n_unresolved_bases": sum(
                1 for e in self.inheritance_edges if not e.resolved
            ),
        }

    def as_dict(self) -> dict:
        return {
            "stats": self.stats(),
            "classes": {k: v.as_dict() for k, v in self.classes.items()},
            "global_vars": {k: v.as_dict() for k, v in self.global_vars.items()},
            "inheritance_edges": [e.as_dict() for e in self.inheritance_edges],
            "ownership_edges": [e.as_dict() for e in self.ownership_edges],
        }


# ---------------------------------------------------------------------------
# Backend tree-sitter (ưu tiên -- chịu được file Python 2 / cú pháp lạ)
# ---------------------------------------------------------------------------
def _ts_text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _ts_class_bases(class_node, src: bytes) -> list[str]:
    """Tên lớp cha, lấy nguyên văn từ `argument_list` của class_definition."""
    bases: list[str] = []
    for child in class_node.named_children:
        if child.type != "argument_list":
            continue
        for arg in child.named_children:
            # Bỏ keyword argument kiểu `metaclass=ABCMeta` -- đó không phải lớp cha.
            if arg.type == "keyword_argument":
                continue
            text = _ts_text(arg, src).strip()
            if text:
                bases.append(text)
    return bases


def _ts_methods(class_node, src: bytes) -> list[tuple[str, int]]:
    """(tên method, dòng bắt đầu) của các hàm định nghĩa TRỰC TIẾP trong class.

    Chỉ lấy con trực tiếp của `block`: hàm lồng trong method không phải member
    của class, và tính nó vào sẽ làm cạnh `hasmember` sai.
    """
    out: list[tuple[str, int]] = []
    for child in class_node.named_children:
        if child.type != "block":
            continue
        for stmt in child.named_children:
            node = stmt
            # `@decorator` bọc hàm trong `decorated_definition`.
            if node.type == "decorated_definition":
                inner = [c for c in node.named_children if c.type == "function_definition"]
                if not inner:
                    continue
                node = inner[0]
            if node.type != "function_definition":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                out.append((_ts_text(name_node, src), node.start_point[0] + 1))
    return out


def _ts_global_vars(root_node, src: bytes, rel_path: str) -> dict[str, GlobalVarNode]:
    """Biến gán ở CẤP MODULE (con trực tiếp của `module`)."""
    out: dict[str, GlobalVarNode] = {}
    for stmt in root_node.named_children:
        if stmt.type != "expression_statement":
            continue
        for expr in stmt.named_children:
            if expr.type not in ("assignment", "augmented_assignment"):
                continue
            left = expr.child_by_field_name("left")
            if left is None:
                continue
            # `a = b = 1` và `a, b = 1, 2`: lấy mọi identifier bên trái.
            targets = (
                [left] if left.type == "identifier"
                else [c for c in left.named_children if c.type == "identifier"]
            )
            for t in targets:
                name = _ts_text(t, src)
                if not name.isidentifier():
                    continue
                gid = f"{rel_path}::{name}"
                if gid not in out:
                    out[gid] = GlobalVarNode(
                        id=gid, name=name, file=rel_path,
                        lineno=expr.start_point[0] + 1,
                        is_constant_case=name.isupper(),
                    )
    return out


def _ts_qualified_prefix(node, src: bytes) -> str:
    """Tiền tố tên đủ điều kiện của một node (chuỗi class/function bao ngoài)."""
    parts: list[str] = []
    cur = node.parent
    while cur is not None:
        if cur.type in ("class_definition", "function_definition"):
            name_node = cur.child_by_field_name("name")
            if name_node is not None:
                parts.append(_ts_text(name_node, src))
        cur = cur.parent
    return ".".join(reversed(parts))


def _build_ts(files: list[Path], root: Path) -> RichPsg:
    from .builder import _get_ts_parser, _rel_posix  # dùng lại parser đã cấu hình

    psg = RichPsg(backend="tree-sitter")
    for path in files:
        try:
            src = path.read_bytes()
            tree = _get_ts_parser().parse(src)
        except (OSError, ValueError) as exc:
            logger.warning("PSG: bỏ qua %s (%s)", path, exc)
            continue
        rel_path = _rel_posix(path, root)
        root_node = tree.root_node

        psg.global_vars.update(_ts_global_vars(root_node, src, rel_path))

        stack = [root_node]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            if node.type != "class_definition":
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            name = _ts_text(name_node, src)
            prefix = _ts_qualified_prefix(node, src)
            qualified = f"{prefix}.{name}" if prefix else name
            lineno = node.start_point[0] + 1
            cid = f"{rel_path}::{qualified}#L{lineno}"
            methods = _ts_methods(node, src)
            psg.classes[cid] = ClassNode(
                id=cid, name=name, qualified_name=qualified, file=rel_path,
                lineno_start=lineno, lineno_end=node.end_point[0] + 1,
                base_names=_ts_class_bases(node, src),
                method_names=[m for m, _ln in methods],
            )
            # Lưu tạm dòng của từng method để khớp sang FunctionNode.id sau.
            psg.classes[cid].method_ids = [
                f"{rel_path}::{qualified}.{m}#L{ln}" for m, ln in methods
            ]
    return psg


# ---------------------------------------------------------------------------
# Backend ast (fallback, khi chưa cài tree-sitter)
# ---------------------------------------------------------------------------
def _build_ast(files: list[Path], root: Path) -> RichPsg:
    from .builder import _rel_posix

    psg = RichPsg(backend="ast-fallback")
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError, ValueError) as exc:
            logger.warning("PSG (ast): bỏ qua %s (%s)", path, exc)
            continue
        rel_path = _rel_posix(path, root)

        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name):
                            gid = f"{rel_path}::{sub.id}"
                            psg.global_vars.setdefault(gid, GlobalVarNode(
                                id=gid, name=sub.id, file=rel_path,
                                lineno=getattr(node, "lineno", 0),
                                is_constant_case=sub.id.isupper(),
                            ))

        def _walk(scope, prefix: str) -> None:
            for child in ast.iter_child_nodes(scope):
                if isinstance(child, ast.ClassDef):
                    qualified = f"{prefix}.{child.name}" if prefix else child.name
                    cid = f"{rel_path}::{qualified}#L{child.lineno}"
                    methods = [
                        (m.name, m.lineno) for m in child.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                    psg.classes[cid] = ClassNode(
                        id=cid, name=child.name, qualified_name=qualified,
                        file=rel_path, lineno_start=child.lineno,
                        lineno_end=getattr(child, "end_lineno", child.lineno),
                        base_names=[ast.unparse(b) for b in child.bases],
                        method_names=[m for m, _ in methods],
                        method_ids=[
                            f"{rel_path}::{qualified}.{m}#L{ln}" for m, ln in methods
                        ],
                        docstring_first_line=(
                            (ast.get_docstring(child) or "").strip().splitlines() or [""]
                        )[0][:120],
                    )
                    _walk(child, qualified)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _walk(child, f"{prefix}.{child.name}" if prefix else child.name)

        _walk(tree, "")
    return psg


# ---------------------------------------------------------------------------
def _resolve_inheritance(psg: RichPsg) -> None:
    """Dựng cạnh kế thừa. Khớp lớp cha theo TÊN với class trong scope.

    Lớp cha ngoài scope (`unittest.TestCase`, `object`) vẫn được ghi cạnh
    nhưng `resolved=False`: thông tin "class này kế thừa TestCase" vẫn hữu ích
    cho prompt, chỉ là không trỏ tới node nào trong graph.
    """
    by_name: dict[str, list[str]] = {}
    for cid, cls in psg.classes.items():
        by_name.setdefault(cls.name, []).append(cid)

    for cid, cls in psg.classes.items():
        for base in cls.base_names:
            # `mod.Base` / `Base[T]` -> lấy phần tên cuối, bỏ tham số generic.
            bare = base.split("[")[0].strip().split(".")[-1]
            targets = by_name.get(bare) or []
            # Tên trùng ở nhiều nơi -> không đoán, để unresolved.
            if len(targets) == 1 and targets[0] != cid:
                psg.inheritance_edges.append(
                    PsgEdge(src=targets[0], dst=cid, kind=SUPERCLASS_OF, resolved=True)
                )
                psg.inheritance_edges.append(
                    PsgEdge(src=cid, dst=targets[0], kind=SUBCLASS_OF, resolved=True)
                )
            else:
                psg.inheritance_edges.append(
                    PsgEdge(src=cid, dst=bare, kind=SUBCLASS_OF, resolved=False)
                )


def _resolve_ownership(psg: RichPsg, functions: dict) -> None:
    """Dựng cạnh hasmember/ismember giữa class và method của nó.

    Khớp `method_ids` (dựng từ file+qualified+dòng) với `FunctionNode.id` thật.
    Id nào không khớp được thì bỏ -- thà thiếu cạnh còn hơn cạnh trỏ sai.
    """
    known = set(functions or {})
    for cid, cls in psg.classes.items():
        matched: list[str] = []
        for mid in cls.method_ids:
            if mid in known:
                matched.append(mid)
                continue
            # Dòng có thể lệch vì decorator: khớp theo (file, qualified) bỏ số dòng.
            head = mid.rsplit("#L", 1)[0]
            cands = [k for k in known if k.rsplit("#L", 1)[0] == head]
            if len(cands) == 1:
                matched.append(cands[0])
        cls.method_ids = matched
        for mid in matched:
            psg.ownership_edges.append(PsgEdge(src=cid, dst=mid, kind=HAS_MEMBER))
            psg.ownership_edges.append(PsgEdge(src=mid, dst=cid, kind=IS_MEMBER))


def build_rich_psg(files: list[Path], root: Path, functions: dict | None = None) -> RichPsg:
    """Điểm vào duy nhất. KHÔNG raise: PSG đầy đủ là thông tin BỔ SUNG, thiếu
    nó thì pipeline vẫn chạy như trước."""
    from .builder import HAS_TREE_SITTER

    try:
        psg = _build_ts(files, Path(root)) if HAS_TREE_SITTER else _build_ast(files, Path(root))
        _resolve_inheritance(psg)
        _resolve_ownership(psg, functions or {})
    except Exception as exc:  # noqa: BLE001 -- không được làm sập Stage 0
        logger.error(
            "Dựng PSG đầy đủ thất bại (%s: %s) -- bỏ qua, pipeline chạy tiếp với "
            "PSG rút gọn như trước.", type(exc).__name__, exc,
        )
        return RichPsg(backend="failed")

    logger.info("PSG đầy đủ (%s): %s", psg.backend, psg.stats())
    return psg
