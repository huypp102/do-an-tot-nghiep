"""Quét TĨNH dataset RepoTransBench (171 repo) để chuẩn bị chọn repo cho
LẦN CHẠY 4 -- KHÔNG chạy test, KHÔNG cài phụ thuộc, KHÔNG import module của
repo (chỉ `ast.parse` đọc source, an toàn với repo lạ/không tin cậy).

Cho mỗi repo, đo:
  - n_py_files: số file .py phát hiện được (dùng CHUNG bộ lọc thư mục rác
    với `stage0_graph.builder.discover_python_files`, để con số này khớp
    với những gì Stage 0 sẽ thấy sau này).
  - n_unparseable: số file .py không `ast.parse` được (cú pháp lạ, có thể là
    Python 2, hoặc lỗi encoding) -- ghi ra để biết rủi ro, KHÔNG loại repo.
  - has_test_dir: có thư mục con tên nằm trong
    `stage0_graph.test_filter.TEST_DIR_NAMES` không (cùng quy tắc test_filter
    dùng để lọc hàm test khỏi candidate_pool).
  - domain_label: xem QUY TẮC GẮN NHÃN bên dưới.

QUY TẮC GẮN NHÃN (đọc để kiểm, không phải "hộp đen"):
  1. ai_preprocessing: có ÍT NHẤT 1 file .py trong repo `import` (dạng
     `import X` hoặc `from X import ...`, X là module gốc, bỏ qua import
     tương đối) MỘT TRONG 11 thư viện ở AI_PREPROCESSING_LIBS (CV/NLP/
     tabular: numpy, PIL, cv2, pandas, sklearn, nltk, spacy, torch,
     transformers, scipy, skimage). Quét TOÀN BỘ file .py của repo (kể cả
     trong thư mục test) -- 1 file test import cv2 vẫn là tín hiệu đúng repo
     thuộc miền CV.
  2. general: n_py_files > 0 nhưng KHÔNG khớp mục 1.
  3. unknown: n_py_files == 0 (không có file .py nào để phân tích -- không
     đủ căn cứ gắn miền).

Không dùng danh sách "AI" rộng hơn (vd flask, requests) vì đề bài chỉ định
rõ 11 thư viện CV/NLP/tabular ở trên; các thư viện khác không đổi domain
label, chỉ xuất hiện trong cột top_imports để tham khảo.

Cách chạy:
    python selection/scan_domains.py [--source <path tới source_projects/Python>]
    (mặc định: đọc biến môi trường REPOTRANSBENCH_ROOT, sau đó tới file
    benchmark/.dataset_root do scripts/setup_linux.sh ghi ra, giống các
    script khác trong repo -- KHÔNG hard-code đường dẫn máy.)

Output:
    selection/domain_tags.csv     -- 1 dòng / repo
    selection/domain_counts.json  -- số đếm theo nhãn + tổng số repo
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import logging
import sys
from collections import Counter
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCHMARK_ROOT))

from stage0_graph.builder import discover_python_files  # noqa: E402
from stage0_graph.test_filter import TEST_DIR_NAMES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark.selection.scan_domains")

# 11 thư viện CV/NLP/tabular theo đúng đề bài (khớp theo TÊN MODULE GỐC khi
# import, không phải tên gói pip -- vd gói "scikit-learn" import là "sklearn").
AI_PREPROCESSING_LIBS = {
    "numpy", "PIL", "cv2", "pandas", "sklearn", "nltk", "spacy",
    "torch", "transformers", "scipy", "skimage",
}

# Thư viện chuẩn hay gặp -- loại khỏi cột top_imports cho đỡ nhiễu (vẫn tính
# đủ trong lượt quét, chỉ không hiện trong báo cáo rút gọn).
_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


def _extract_top_level_imports(source: str) -> set[str]:
    """Trả về tập tên module GỐC (`os.path` -> `os`) mà 1 file import qua
    `import X` hoặc `from X import ...`. Bỏ qua import tương đối
    (`from . import x`, `level > 0`) vì đó là module NỘI BỘ của repo, không
    phải thư viện ngoài."""
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _has_test_dir(files: list[Path], repo_root: Path) -> bool:
    for f in files:
        rel = f.relative_to(repo_root)
        if any(part in TEST_DIR_NAMES for part in rel.parts):
            return True
    return False


def scan_repo(repo_root: Path) -> dict:
    files = discover_python_files(repo_root)
    n_unparseable = 0
    all_imports: Counter[str] = Counter()

    for f in files:
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
            imports = _extract_top_level_imports(source)
        except (SyntaxError, ValueError, UnicodeError) as exc:
            n_unparseable += 1
            logger.debug("Không parse được %s: %s", f, exc)
            continue
        all_imports.update(imports)

    matched_ai = sorted(set(all_imports) & AI_PREPROCESSING_LIBS)
    if not files:
        domain_label = "unknown"
    elif matched_ai:
        domain_label = "ai_preprocessing"
    else:
        domain_label = "general"

    non_stdlib = Counter({k: v for k, v in all_imports.items() if k not in _STDLIB})
    top_imports = [name for name, _count in non_stdlib.most_common(8)]

    return {
        "repo": repo_root.name,
        "n_py_files": len(files),
        "n_unparseable": n_unparseable,
        "has_test_dir": _has_test_dir(files, repo_root),
        "domain_label": domain_label,
        "matched_ai_preprocessing_libs": ";".join(matched_ai),
        "top_imports": ";".join(top_imports),
    }


def _resolve_default_source() -> Path | None:
    import os

    env = os.environ.get("REPOTRANSBENCH_ROOT")
    if env:
        return Path(env)
    marker = BENCHMARK_ROOT / ".dataset_root"
    if marker.exists():
        return Path(marker.read_text(encoding="utf-8").strip())
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=str, default=None,
        help="Đường dẫn tới source_projects/Python. Mặc định đọc "
             "REPOTRANSBENCH_ROOT rồi tới benchmark/.dataset_root.",
    )
    parser.add_argument(
        "--out-dir", type=str, default=str(BENCHMARK_ROOT / "selection"),
        help="Thư mục ghi domain_tags.csv + domain_counts.json.",
    )
    args = parser.parse_args()

    source = Path(args.source) if args.source else _resolve_default_source()
    if source is None:
        logger.error(
            "Không xác định được đường dẫn dataset. Set biến môi trường "
            "REPOTRANSBENCH_ROOT, hoặc truyền --source, hoặc chạy "
            "scripts/setup_linux.sh trước (ghi ra .dataset_root)."
        )
        return 1
    if not source.is_dir():
        logger.error("Đường dẫn dataset không tồn tại hoặc không phải thư mục: %s", source)
        return 1

    repos = sorted(
        p for p in source.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    )
    if not repos:
        logger.error("Không có repo con nào trong %s", source)
        return 1

    logger.info("Quét %d repo trong %s ...", len(repos), source)
    rows = [scan_repo(r) for r in repos]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "domain_tags.csv"
    fieldnames = [
        "repo", "n_py_files", "n_unparseable", "has_test_dir",
        "domain_label", "matched_ai_preprocessing_libs", "top_imports",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(r["domain_label"] for r in rows)
    counts_path = out_dir / "domain_counts.json"
    counts_path.write_text(
        json.dumps(
            {
                "total_repos": len(rows),
                "by_domain": dict(counts),
                "source_root": str(source),
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    logger.info("Đã ghi %s và %s", csv_path, counts_path)
    logger.info("Số đếm theo nhãn: %s", dict(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
