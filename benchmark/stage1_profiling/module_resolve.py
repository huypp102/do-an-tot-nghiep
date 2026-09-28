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
    NHIỀU node (2 file cùng định nghĩa `normalize`); chọn node ĐẦU TIÊN và ghi
    log, vì registry/báo cáo phía sau đều khoá theo tên trần nên không phân
    biệt được nhiều hơn. Đây là giới hạn đã biết của việc khớp theo tên, ghi
    lại ở đây để không ai tưởng là bug mới.
    """
    specs: list[HotspotSpec] = []
    skipped: dict[str, str] = {}

    by_name: dict[str, list] = {}
    for node in (graph.functions or {}).values():
        by_name.setdefault(node.name, []).append(node)

    for name in function_names:
        nodes = by_name.get(name) or []
        if not nodes:
            skipped[name] = f"Stage 0 không có node nào tên '{name}'"
            continue
        if len(nodes) > 1:
            logger.warning(
                "Hotspot '%s' khớp %d node (%s) -- dùng node đầu tiên; khớp "
                "theo tên trần không phân biệt được nhiều hơn.",
                name, len(nodes), ", ".join(n.file for n in nodes[:3]),
            )
        node = nodes[0]
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
        ))

    return specs, skipped
