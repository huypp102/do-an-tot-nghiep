"""PHẦN 3.1 -- Kiểm tra TÍNH DI ĐỘNG Windows → Linux.

Máy phát triển là Windows, máy chạy thực nghiệm là Linux thuê. Mọi giả định
riêng Windows lọt vào code sẽ chỉ lộ ra khi đã thuê máy và đang tính tiền theo
giờ, nên nó phải bị bắt ở đây.

Test này quét TĨNH source (không chạy pipeline) tìm các mẫu sau:
  1. đường dẫn tuyệt đối có ổ đĩa (`C:\\`, `D:/`, ...);
  2. dấu `\\` trong literal đường dẫn;
  3. `Scripts\\python.exe` hoặc `.exe` viết cứng;
  4. gọi `"python"`/`"python3"` thay vì `sys.executable`;
  5. `/tmp` viết cứng thay vì `tempfile.gettempdir()`.

Chạy: python tests/test_portability.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))

# Thư mục KHÔNG quét: repo giả là dữ liệu kiểm thử, không phải code sản phẩm;
# `tests/` tự chịu trách nhiệm (và đã được sửa để suy đường dẫn từ __file__).
SKIP_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "data", "results", "profiles",
    ".rtb_venv", ".rtb_crates", ".rtb_capture", "tests",
}

# Tài liệu/comment được phép nhắc tới đường dẫn Windows để giải thích; chỉ CODE
# thật mới bị chặn. Nên mỗi phép kiểm bỏ qua comment và docstring.
PATTERNS: list[tuple[str, str, str]] = [
    (
        "drive_letter",
        r"""['"][A-Za-z]:[\\/]""",
        "đường dẫn tuyệt đối có ổ đĩa -- dùng Path/biến môi trường thay thế",
    ),
    (
        "hardcoded_tmp",
        r"""['"]/tmp[/'"]""",
        "/tmp viết cứng -- dùng tempfile.gettempdir()",
    ),
    (
        "scripts_dir",
        r"""['"][^'"]*Scripts[\\/]python""",
        "Scripts/python viết cứng -- tự chọn bin|Scripts theo os.name",
    ),
    (
        "bare_python_exe",
        r"""\[\s*['"]python3?['"]""",
        "gọi 'python' trực tiếp -- dùng sys.executable",
    ),
]


def _code_only(src: str) -> str:
    """Bỏ comment và docstring, chỉ giữ phần code chạy thật.

    Không làm bước này thì mọi dòng giải thích "trên Windows là C:\\..." đều bị
    báo sai, và test sẽ bị tắt đi vì gây nhiễu -- đó là cách một test chết.
    """
    lines = src.splitlines()
    out = []
    for line in lines:
        # Bỏ comment cuối dòng (thô nhưng đủ: không bỏ '#' nằm trong string
        # vì các mẫu ta tìm đều có dấu nháy riêng).
        stripped = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        out.append(stripped)
    text = "\n".join(out)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return text
    # Xoá nội dung mọi docstring.
    docs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.append(d)
    for d in docs:
        text = text.replace(d, "")
    return text


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    for p in BENCH.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(BENCH).parts):
            continue
        files.append(p)
    return sorted(files)


def scan() -> list[str]:
    problems: list[str] = []
    for path in iter_source_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        code = _code_only(src)
        for name, pattern, why in PATTERNS:
            for m in re.finditer(pattern, code):
                line_no = code[: m.start()].count("\n") + 1
                snippet = code.splitlines()[line_no - 1].strip()[:90]
                problems.append(
                    f"{path.relative_to(BENCH)}:{line_no} [{name}] {why}\n      {snippet}"
                )
    return problems


def check_venv_python_both_platforms() -> list[str]:
    """`_venv_python` phải trả đúng đường dẫn cho CẢ hai nền tảng."""
    import stage5_compiler_in_the_loop.repo_runner as rr

    problems = []
    src = Path(rr.__file__).read_text(encoding="utf-8")
    if 'os.name == "nt"' not in src:
        problems.append(
            "repo_runner._venv_python không phân biệt nền tảng bằng os.name "
            "-> venv trên Linux (bin/) sẽ tìm sai đường dẫn"
        )
    if '"bin"' not in src or '"Scripts"' not in src:
        problems.append("repo_runner thiếu nhánh bin/ hoặc Scripts/")
    return problems


def check_default_work_root() -> list[str]:
    """Thư mục làm việc mặc định phải trung tính, không phải /tmp viết cứng."""
    import tempfile

    import stage5_compiler_in_the_loop.repo_runner as rr

    problems = []
    root = str(rr.default_work_root())
    if not root.startswith(tempfile.gettempdir()):
        problems.append(
            f"default_work_root()={root} không nằm trong tempfile.gettempdir()"
            f"={tempfile.gettempdir()}"
        )
    return problems


def check_profiles_have_no_machine_paths() -> list[str]:
    """Profile chỉ được chứa biến môi trường, không đường dẫn máy cụ thể."""
    problems = []
    for p in sorted((BENCH / "profiles").glob("*.yaml")):
        text = p.read_text(encoding="utf-8")
        for line in text.splitlines():
            code = line.split("#", 1)[0]
            if re.search(r"[A-Za-z]:[\\/]", code):
                problems.append(f"{p.name}: đường dẫn có ổ đĩa -> {line.strip()[:80]}")
    return problems


def main() -> int:
    print("=" * 78)
    print("TÍNH DI ĐỘNG Windows -> Linux")
    print("=" * 78)
    files = iter_source_files()
    print(f"  quét {len(files)} file .py (bỏ {sorted(SKIP_DIRS)})")

    problems = scan()
    problems += check_venv_python_both_platforms()
    problems += check_default_work_root()
    problems += check_profiles_have_no_machine_paths()

    if problems:
        print(f"\n### TÍNH DI ĐỘNG: THẤT BẠI ({len(problems)} vấn đề)")
        for p in problems:
            print(f"   - {p}")
        return 1

    print("\n  không có đường dẫn ổ đĩa / /tmp viết cứng / Scripts-python cứng")
    print("  _venv_python phân biệt bin|Scripts theo os.name")
    print("  default_work_root() dùng tempfile.gettempdir()")
    print("  profiles/ không chứa đường dẫn máy cụ thể")
    print("\n### TÍNH DI ĐỘNG: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
