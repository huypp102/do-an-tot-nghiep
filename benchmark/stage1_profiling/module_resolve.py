"""PHA B -- Đổi đường dẫn file + tên hàm thành `module:qualname` import được.

Stage 0 nhận diện hotspot theo (đường dẫn file, `qualified_name`). Nhưng để
plugin pytest bọc được hàm thật, nó cần một chuỗi `module:qualname` mà
`importlib.import_module` chấp nhận -- hai thứ này KHÔNG phải một.

Vì sao không chỉ đổi `/` thành `.`: repo trong dataset có nhiều layout khác
nhau, và chọn sai gốc import là import sai module (hoặc ImportError):

    repo/mypkg/utils.py            -> "mypkg.utils"        (gốc = repo/)
    repo/src/mypkg/utils.py        -> "mypkg.utils"        (gốc = repo/src/)  <- layout src/
    repo/tools/script.py (không có __init__.py) -> "script" (gốc = repo/tools/)
    repo/mypkg/__init__.py         -> "mypkg"              (bỏ đuôi __init__)

CÁCH LÀM: đi NGƯỢC LÊN từ file, qua từng thư mục còn có `__init__.py`. Thư
mục cuối cùng còn `__init__.py` là package ngoài cùng; cha của nó là gốc
import. Cách này tự xử lý được cả layout `src/` mà không cần liệt kê tên thư
mục đặc biệt -- `src/` thường KHÔNG có `__init__.py` nên vòng lặp dừng đúng
chỗ.

Không resolve được -> caller ghi lý do `UNRESOLVABLE_IMPORT` (xem outcomes.py).
Module này chỉ dùng stdlib: nó cũng được import từ trong venv riêng của repo.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("benchmark.stage1_profiling.module_resolve")


@dataclass(frozen=True)
class HotspotSpec:
    """Một hotspot ở dạng plugin pytest dùng được."""

    function_name: str      # tên trần, để khớp với kết quả Stage 0 và báo cáo
    module: str             # vd "mypkg.utils"
    qualname: str           # vd "normalize" hoặc "Foo.method"
    import_root: str        # thư mục phải có trong sys.path để import được
    file: str               # đường dẫn gốc, giữ lại để báo lỗi cho người đọc
    # --- Tên trùng (PHA 2) ---
    # `ambiguous_name=True` nghĩa là tên hotspot khớp nhiều node mà KHÔNG phân
    # biệt được. Vẫn chạy (giữ hành vi cũ) nhưng phải lộ ra trong kết quả: đối
    # số ghi được và bản Rust có thể không thuộc cùng một hàm.
    ambiguous_name: bool = False
    ambiguity_detail: str = ""
    n_name_matches: int = 1

    @property
    def target(self) -> str:
        """Chuỗi `module:qualname` -- định dạng truyền qua dòng lệnh/env."""
        return f"{self.module}:{self.qualname}"


def _is_identifier_path(parts: list[str]) -> bool:
    """Mọi thành phần phải là identifier hợp lệ. Thư mục như `my-tools` hay
    file `2to3.py` không import được bằng tên thường."""
    return bool(parts) and all(p.isidentifier() for p in parts)


def resolve_module(file_path: Path, repo_root: Path) -> tuple[str | None, str | None, str]:
    """Trả về (module, import_root, lý do lỗi).

    `file_path` có thể là tương đối (tính từ `repo_root`) hoặc tuyệt đối.
    """
    repo_root = Path(repo_root).resolve()
    path = Path(file_path)
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve()

    if path.suffix != ".py":
        return None, None, f"không phải file .py: {path.name}"
    if not path.exists():
        return None, None, f"file không tồn tại trong bản copy: {path}"

    # Đi ngược lên qua các thư mục còn __init__.py để tìm gốc import.
    pkg_parts: list[str] = []
    current = path.parent
    while (current / "__init__.py").exists():
        pkg_parts.insert(0, current.name)
        if current == repo_root or current.parent == current:
            break
        current = current.parent
    import_root = current

    stem = path.stem
    mod_parts = list(pkg_parts)
    if stem != "__init__":
        mod_parts.append(stem)

    if not mod_parts:
        return None, None, f"không suy ra được tên module từ {path}"
    if not _is_identifier_path(mod_parts):
        bad = [p for p in mod_parts if not p.isidentifier()]
        return None, None, (
            f"tên không import được bằng cú pháp module: {bad} "
            f"(từ {path.relative_to(repo_root) if repo_root in path.parents else path})"
        )

    # Gốc import phải nằm trong repo: nếu vòng lặp trên leo ra ngoài repo thì
    # cấu trúc package đã sai, thà báo lỗi rõ hơn là import lung tung.
    try:
        import_root.relative_to(repo_root)
    except ValueError:
        return None, None, f"gốc import {import_root} nằm ngoài repo {repo_root}"

    return ".".join(mod_parts), str(import_root), ""


def build_hotspot_specs(
    function_names: list[str],
    graph,
    repo_root: Path,
) -> tuple[list[HotspotSpec], dict[str, str]]:
    """Đổi danh sách hotspot của Stage 0/2 thành `HotspotSpec`.

    Trả về (specs, {tên hàm: lý do bỏ}). Lý do bỏ dùng nguyên văn cho
    `outcomes.UNRESOLVABLE_IMPORT`.

    `graph` là `ProgramGraph` của Stage 0. Một tên hàm trần có thể ứng với
    NHIỀU node (2 file cùng định nghĩa `normalize`, hay `__init__` khớp 4 class).
    Việc chọn node nào do `_pick_node` lo, theo thứ tự ưu tiên rõ ràng; khi
    không phân biệt được thì hotspot bị gắn cờ `ambiguous_name` thay vì lặng lẽ
    lấy node đầu tiên.
    """
    specs: list[HotspotSpec] = []
    skipped: dict[str, str] = {}

    by_name: dict[str, list] = {}
    for node in (graph.functions or {}).values():
        by_name.setdefault(node.name, []).append(node)

    # Nơi Stage 0 phát hiện hotspot: dùng làm căn cứ ƯU TIÊN khi tên trùng.
    hint_files = {n: nodes[0].file for n, nodes in by_name.items() if len(nodes) == 1}

    for name in function_names:
        nodes = by_name.get(name) or []
        if not nodes:
            skipped[name] = f"Stage 0 không có node nào tên '{name}'"
            continue

        node, ambiguous, why = _pick_node(name, nodes, hint_files.get(name))
        module, import_root, err = resolve_module(Path(node.file), repo_root)
        if err or module is None or import_root is None:
            skipped[name] = err or "không resolve được module"
            logger.warning("Hotspot '%s': %s", name, skipped[name])
            continue
        specs.append(HotspotSpec(
            function_name=name,
            module=module,
            qualname=node.qualified_name or name,
            import_root=import_root,
            file=node.file,
            ambiguous_name=ambiguous,
            ambiguity_detail=why,
            n_name_matches=len(nodes),
        ))

    return specs, skipped


def _pick_node(name: str, nodes: list, hint_file: str | None):
    """Chọn node cho một tên hotspot khi tên đó khớp NHIỀU node.

    VẤN ĐỀ THẬT (pilot 1): `__init__` khớp 4 node khác nhau, và bản cũ lặng lẽ
    lấy node đầu tiên. Nghĩa là đối số được ghi từ `A.__init__` có thể bị đem so
    với bản Rust dịch từ `B.__init__` -- sai mà không ai biết, vì không có dấu
    hiệu nào trong kết quả.

    Thứ tự ưu tiên:
      (a) node NẰM CÙNG FILE với nơi hotspot được phát hiện;
      (b) nếu vẫn còn nhiều hơn 1, chọn node có `qualified_name` ĐẦY ĐỦ NHẤT
          (vd `module.ClassName.method` thay vì tên trần) -- tên đủ điều kiện
          phân biệt được method của 2 class khác nhau;
      (c) hết cách thì giữ hành vi cũ (lấy đầu tiên, theo thứ tự id cho tất
          định) NHƯNG gắn cờ `AMBIGUOUS_NAME` để không dùng nhầm âm thầm.

    Trả về (node, ambiguous: bool, lý do).
    """
    if len(nodes) == 1:
        return nodes[0], False, ""

    # Sắp theo id trước để mọi nhánh đều tất định.
    ordered = sorted(nodes, key=lambda n: n.id)

    # (a) cùng file với nơi phát hiện.
    if hint_file:
        same_file = [n for n in ordered if n.file == hint_file]
        if len(same_file) == 1:
            logger.info(
                "Hotspot '%s' khớp %d node -> chọn node cùng file với nơi phát "
                "hiện (%s).", name, len(nodes), hint_file,
            )
            return same_file[0], False, ""
        if same_file:
            ordered = same_file

    # (b) tên đủ điều kiện dài nhất (nhiều thành phần nhất) phân biệt được.
    by_depth: dict[int, list] = {}
    for n in ordered:
        depth = len((n.qualified_name or n.name or "").split("."))
        by_depth.setdefault(depth, []).append(n)
    deepest = by_depth[max(by_depth)]
    quals = {(n.qualified_name or n.name) for n in ordered}
    if len(deepest) == 1 and len(quals) == len(ordered):
        chosen = deepest[0]
        logger.info(
            "Hotspot '%s' khớp %d node -> phân biệt được bằng qualified_name "
            "'%s'.", name, len(nodes), chosen.qualified_name,
        )
        return chosen, False, ""

    # (c) không phân biệt được -> giữ hành vi cũ nhưng GẮN CỜ.
    detail = (
        f"tên '{name}' khớp {len(nodes)} node không phân biệt được: "
        + "; ".join(f"{n.qualified_name or n.name}@{n.file}:{n.lineno_start}"
                    for n in ordered[:4])
        + ("; ..." if len(ordered) > 4 else "")
        + f". Đã dùng node đầu tiên ({ordered[0].file}) -- đối số ghi được và "
          "bản Rust có thể KHÔNG thuộc cùng một hàm."
    )
    logger.warning("Hotspot '%s': AMBIGUOUS_NAME -- %s", name, detail)
    return ordered[0], True, detail
