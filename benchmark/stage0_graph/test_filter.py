"""Loại HÀM TEST khỏi danh sách ứng viên hotspot.

VẤN ĐỀ THẬT (đo trên lượt chạy đầu tiên, 9 repo RepoTransBench): **41/219 =
18%** chỗ trong `candidate_pool` bị hàm test chiếm, riêng `BBuf_onnx_learn` là
**14/23 = 60%**. Hàm test chiếm chỗ ngay từ đầu nên `candidate_pool=30` thực
tế chỉ còn hơn 10 chỗ cho code sản phẩm ở những repo đó.

Dịch hàm test sang Rust là vô nghĩa: nó không nằm trong đường chạy của người
dùng, tăng tốc nó không tăng tốc gì cả, và nếu thay nó bằng Rust thì chính bộ
test -- tức ORACLE của chúng ta -- bị đổi. Nên phải lọc TRƯỚC khi xếp hạng,
không phải lọc ở Decision Gate (lúc đó nó đã chiếm chỗ rồi).

BA TIÊU CHÍ (khớp một cái là đủ), theo đúng quy ước pytest/unittest:
  (a) ĐƯỜNG DẪN nằm trong `tests/` hoặc `public_tests/` (RepoTransBench dùng
      cả hai tên);
  (b) TÊN HÀM khớp `test_*`, `*_test`, `setUp`, `tearDown` (và các biến thể
      `setUpClass`/`tearDownClass`/`setup_method`/`teardown_method`);
  (c) FILE có import `pytest`/`unittest` VÀ hàm KHÔNG được gọi từ file khác.
      Điều kiện thứ hai là quan trọng: một module sản phẩm có thể import
      pytest (vd cung cấp fixture cho người dùng), và hàm của nó vẫn là code
      sản phẩm thật nếu nơi khác gọi tới. Chỉ loại khi nó cô lập trong file
      test đó.

CỐ Ý KHÔNG xoá node khỏi graph: Stage 3 vẫn cần biết "hàm test nào gọi
hotspot" làm context, và ví dụ vào/ra cũng lấy từ lời gọi của test. Module này
chỉ trả về TẬP ID BỊ LOẠI để bước xếp hạng bỏ qua.
"""
from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath

logger = logging.getLogger("benchmark.stage0_graph.test_filter")

# Thư mục test. RepoTransBench có cả `tests/` lẫn `public_tests/`.
TEST_DIR_NAMES = {"tests", "test", "public_tests", "testing", "spec", "specs"}

# Tên hàm theo quy ước pytest/unittest.
_TEST_NAME_RE = re.compile(
    r"""^(
        test_.*            # pytest: test_foo
      | .*_test            # một số repo dùng hậu tố: foo_test
      | setUp(Class|Module)?      # unittest
      | tearDown(Class|Module)?
      | setup_(method|function|module|class)   # pytest xUnit-style
      | teardown_(method|function|module|class)
    )$""",
    re.VERBOSE,
)

# File test thường tên `test_*.py` hoặc `*_test.py`, hoặc `conftest.py`.
_TEST_FILE_RE = re.compile(r"^(test_.*|.*_test|conftest)\.py$")

_TEST_IMPORT_RE = re.compile(r"\b(import\s+(pytest|unittest)|from\s+(pytest|unittest)\b)")

# Lý do loại -- ghi vào báo cáo để người đọc kiểm được, không phải tin suông.
BY_PATH = "trong thư mục test"
BY_FILENAME = "file test (test_*.py / *_test.py / conftest.py)"
BY_NAME = "tên hàm theo quy ước pytest/unittest"
BY_IMPORT = "file import pytest/unittest và hàm không được file khác gọi"


def _in_test_dir(file_path: str) -> bool:
    parts = PurePosixPath(str(file_path).replace("\\", "/")).parts
    return any(p in TEST_DIR_NAMES for p in parts)


def _is_test_filename(file_path: str) -> bool:
    name = PurePosixPath(str(file_path).replace("\\", "/")).name
    return bool(_TEST_FILE_RE.match(name))


def _bare_name(node) -> str:
    """Tên trần của hàm. `qualified_name` có thể là `TestFoo.test_bar`, và tiêu
    chí tên phải áp lên phần CUỐI, không phải cả chuỗi."""
    return (getattr(node, "name", "") or "").split(".")[-1]


def _callers_outside_file(graph, node) -> set[str]:
    """Tập file KHÁC có hàm gọi tới `node`."""
    callers: set[str] = set()
    for edge in graph.call_edges or []:
        if edge.callee != node.id:
            continue
        caller = (graph.functions or {}).get(edge.caller)
        if caller is not None and caller.file != node.file:
            callers.add(caller.file)
    return callers


def classify(graph) -> dict[str, str]:
    """Trả về {FunctionNode.id: lý do loại} cho mọi hàm bị coi là code-test.

    KHÔNG raise và KHÔNG sửa graph.
    """
    dropped: dict[str, str] = {}
    files = graph.files or {}

    for fid, node in (graph.functions or {}).items():
        path = getattr(node, "file", "") or ""

        if _in_test_dir(path):
            dropped[fid] = BY_PATH
            continue
        if _is_test_filename(path):
            dropped[fid] = BY_FILENAME
            continue
        if _TEST_NAME_RE.match(_bare_name(node)):
            dropped[fid] = BY_NAME
            continue

        # (c) file import pytest/unittest VÀ hàm cô lập trong file đó.
        file_node = files.get(path)
        imports = " ".join((getattr(file_node, "imports_raw", None) or [])) if file_node else ""
        if imports and _TEST_IMPORT_RE.search(imports):
            if not _callers_outside_file(graph, node):
                dropped[fid] = BY_IMPORT

    return dropped


def summarize(graph, dropped: dict[str, str]) -> dict:
    """Số liệu để ghi vào kết quả: bao nhiêu hàm bị loại, vì lý do gì."""
    by_reason: dict[str, int] = {}
    for reason in dropped.values():
        by_reason[reason] = by_reason.get(reason, 0) + 1
    n_total = len(graph.functions or {})
    return {
        "n_functions_total": n_total,
        "n_test_functions_excluded": len(dropped),
        "pct_excluded": round(100.0 * len(dropped) / n_total, 1) if n_total else 0.0,
        "by_reason": by_reason,
        "excluded_names": sorted(
            {(graph.functions or {})[fid].name for fid in dropped if fid in (graph.functions or {})}
        )[:60],
    }


def exclude_test_functions(graph) -> tuple[set[str], dict]:
    """Tiện ích cho Stage 0: trả về (tập id bị loại, số liệu tóm tắt).

    Truyền tập id đó vào `rank.top_k_functions(..., exclude_ids=...)` để hàm
    test không chiếm chỗ trong `candidate_pool`.
    """
    dropped = classify(graph)
    stats = summarize(graph, dropped)
    if dropped:
        logger.info(
            "Lọc code-test: loại %d/%d hàm (%.1f%%) khỏi ứng viên hotspot -- %s",
            stats["n_test_functions_excluded"], stats["n_functions_total"],
            stats["pct_excluded"],
            ", ".join(f"{k}: {v}" for k, v in sorted(stats["by_reason"].items())),
        )
    return set(dropped), stats
