"""Input intake -- chuẩn hoá input thành 1 đường dẫn LOCAL cho Stage 0.

Đây là NƠI DUY NHẤT trong toàn hệ thống phân biệt "input là URL GitHub" hay
"input là đường dẫn local". Mọi stage phía sau (stage0_graph/builder.py trở
đi) chỉ làm việc với `Path` local đã được resolve ở đây, không cần biết nó
đến từ đâu.

Quy tắc nhận diện (cố ý đơn giản, dễ đoán):
  - Bắt đầu bằng "http://" hoặc "https://" VÀ chứa "github.com"  -> URL GitHub
  - Còn lại                                                       -> đường dẫn local

Với URL GitHub: `git clone --depth 1 <url>` vào
`data/cloned_repos/<tên_repo>/`. Nếu thư mục đó đã tồn tại từ lần trước,
DÙNG LẠI (không clone lại, không tốn mạng) trừ khi truyền
`force_refresh=True`.

Mọi lỗi (repo private, không có mạng, URL sai, chưa cài git) đều được gom vào
`IntakeError` kèm thông điệp rõ ràng. Hàm này KHÔNG tự nuốt lỗi và cũng không
`sys.exit()` -- caller (run_pipeline.py, stage6_benchmark/bench.py) bắt
`IntakeError`, log rõ rồi bỏ qua phần cần target đó, để phần còn lại của
pipeline vẫn chạy tiếp được.
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("benchmark.input.intake")

BENCHMARK_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLONE_DIR = BENCHMARK_ROOT / "data" / "cloned_repos"

CLONE_TIMEOUT_SEC = 300

# Thư mục con KHÔNG được coi là "1 repo" khi quét dataset.
_IGNORE_REPO_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules", "__MACOSX",
}


class IntakeError(RuntimeError):
    """Không resolve được target (clone lỗi, path không tồn tại, ...)."""


def _force_rmtree(path: Path) -> None:
    """Xoá thư mục clone, xử lý được file CHỈ-ĐỌC trên Windows.

    `git clone` tạo các file trong `.git/objects/pack/` ở chế độ read-only.
    `shutil.rmtree` thường sẽ thất bại với PermissionError khi gặp chúng, và
    nếu dùng `ignore_errors=True` thì thất bại đó bị NUỐT ÂM THẦM -- để lại
    thư mục xoá dở, khiến lần `git clone` sau báo lỗi "destination path
    already exists and is not an empty directory". Vì vậy ở đây bỏ cờ
    read-only trước rồi mới xoá, và KHÔNG nuốt lỗi.
    (Đã gặp thật trên Windows khi dọn data/cloned_repos/.)
    """
    if not path.exists():
        return
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chmod(Path(root) / name, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass  # không chmod được thì để rmtree báo lỗi thật bên dưới
    shutil.rmtree(path)


def _cleanup_partial_clone(dest: Path) -> None:
    """Dọn thư mục clone dở sau khi `git clone` thất bại.

    Ở đây KHÔNG raise: caller sắp raise IntakeError với nguyên nhân GỐC
    (timeout/exit code), lỗi dọn dẹp chỉ là hệ quả, không được phép che mất
    lỗi gốc. Nhưng vẫn log warning để người dùng biết còn rác cần xoá tay.
    """
    try:
        _force_rmtree(dest)
    except OSError as exc:
        logger.warning(
            "Không dọn được thư mục clone dở tại %s: %s. Xoá tay thư mục này "
            "trước khi clone lại.", dest, exc,
        )


def is_github_url(source: str) -> bool:
    """True nếu `source` là URL GitHub (http/https + host chứa github.com)."""
    s = (source or "").strip()
    if not s.lower().startswith(("http://", "https://")):
        return False
    try:
        host = (urlparse(s).netloc or "").lower()
    except ValueError:
        return False
    return "github.com" in host


def repo_name_from_url(url: str) -> str:
    """Lấy tên repo từ URL: .../<owner>/<repo>[.git][/] -> "<repo>"."""
    path = urlparse(url.strip()).path.strip("/")
    if not path:
        raise IntakeError(f"URL GitHub không có phần owner/repo: {url!r}")
    name = path.split("/")[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not name:
        raise IntakeError(f"Không trích được tên repo từ URL: {url!r}")
    return name


def clone_github_repo(
    url: str, clone_root: Path | None = None, force_refresh: bool = False
) -> Path:
    """`git clone --depth 1 <url>` vào <clone_root>/<tên_repo>/ và trả về
    đường dẫn đó. Dùng lại bản đã clone nếu có (trừ khi force_refresh)."""
    clone_root = Path(clone_root) if clone_root is not None else DEFAULT_CLONE_DIR
    name = repo_name_from_url(url)
    dest = clone_root / name

    if dest.exists():
        if not force_refresh:
            logger.info(
                "Repo '%s' đã có sẵn tại %s -- dùng lại bản đã clone (truyền "
                "force_refresh=True nếu muốn clone lại từ đầu).", name, dest,
            )
            return dest
        logger.info("force_refresh=True -> xoá bản clone cũ tại %s.", dest)
        try:
            _force_rmtree(dest)
        except OSError as exc:
            raise IntakeError(
                f"Không xoá được bản clone cũ tại {dest}: {exc}. Xoá tay thư "
                "mục đó rồi chạy lại."
            ) from exc

    if shutil.which("git") is None:
        raise IntakeError(
            "Không tìm thấy lệnh `git` trong PATH -- không thể clone "
            f"{url!r}. Cài Git rồi thử lại, hoặc tự clone tay và trỏ "
            "target.source vào đường dẫn local."
        )

    clone_root.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", url, str(dest)]
    logger.info("Đang clone %s -> %s ...", url, dest)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=CLONE_TIMEOUT_SEC
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup_partial_clone(dest)
        raise IntakeError(
            f"Clone {url!r} quá {CLONE_TIMEOUT_SEC}s -> huỷ. Kiểm tra mạng "
            "hoặc kích thước repo."
        ) from exc
    except OSError as exc:
        raise IntakeError(f"Không chạy được `git clone` cho {url!r}: {exc}") from exc

    if proc.returncode != 0:
        _cleanup_partial_clone(dest)  # dọn thư mục clone dở
        stderr = (proc.stderr or "").strip()
        raise IntakeError(
            f"`git clone` thất bại cho {url!r} (exit={proc.returncode}). "
            "Thường do: repo private (cần credential), URL sai, hoặc không có "
            f"mạng. stderr: {stderr[-500:]}"
        )

    logger.info("Clone xong: %s", dest)
    return dest


def resolve_target(
    source: str, clone_root: Path | None = None, force_refresh: bool = False
) -> Path:
    """HÀM CHÍNH. Nhận `source` là đường dẫn local HOẶC URL GitHub, trả về
    `Path` local đã sẵn sàng cho stage0_graph/builder.py.

    Raise `IntakeError` (không crash chương trình) nếu không resolve được.
    """
    if not source or not str(source).strip():
        raise IntakeError(
            "target.source rỗng -- cần điền đường dẫn local hoặc URL GitHub "
            "trong config.yaml khi target.mode = file | repo."
        )

    source = str(source).strip()

    if is_github_url(source):
        logger.info("target.source được nhận diện là URL GitHub: %s", source)
        return clone_github_repo(source, clone_root=clone_root, force_refresh=force_refresh)

    logger.info("target.source được nhận diện là đường dẫn local: %s", source)
    path = Path(source)
    if not path.is_absolute():
        path = BENCHMARK_ROOT / path
    if not path.exists():
        raise IntakeError(
            f"Đường dẫn local không tồn tại: {path} (từ target.source={source!r}). "
            "Kiểm tra lại config.yaml -- đường dẫn tương đối được tính từ "
            f"thư mục benchmark/ ({BENCHMARK_ROOT})."
        )
    return path


def resolve_dataset_repos(
    source_root: str | Path, max_repos: int = 0
) -> list[Path]:
    """Liệt kê các repo con trong dataset (vd RepoTransBench) để chạy per-repo.

    `source_root` thường đến từ `config.dataset.source_root`, vốn đã được
    `config_loader.expand_env_vars` mở rộng từ
    `${REPOTRANSBENCH_ROOT:-...}` -- nghĩa là KHÔNG có đường dẫn máy nào bị
    hardcode; máy thuê GPU chỉ cần set biến môi trường.

    Quy ước: mỗi THƯ MỤC CON trực tiếp của source_root là 1 repo. Bỏ qua thư
    mục ẩn và các thư mục rác thường gặp.

    Raise `IntakeError` với hướng dẫn CỤ THỂ (không phải traceback khó hiểu)
    khi:
      - source_root rỗng (biến môi trường chưa set và không có mặc định),
      - thư mục chưa tồn tại (dataset chưa tải về / chưa mount volume),
      - thư mục tồn tại nhưng không có repo con nào.
    """
    if not source_root or not str(source_root).strip():
        raise IntakeError(
            "dataset.source_root rỗng. Set biến môi trường REPOTRANSBENCH_ROOT "
            "trỏ tới thư mục chứa dataset, ví dụ:\n"
            "    export REPOTRANSBENCH_ROOT=/app/data/RepoTransBench/source_projects/Python\n"
            "Hoặc đặt dataset.enabled: false trong config.yaml nếu chưa cần dataset."
        )

    root = Path(str(source_root).strip())
    if not root.is_absolute():
        root = BENCHMARK_ROOT / root

    if not root.exists():
        raise IntakeError(
            f"Thư mục dataset không tồn tại: {root}\n"
            "Nguyên nhân thường gặp:\n"
            "  1. Dataset chưa được tải về máy này.\n"
            "  2. Chạy trong Docker nhưng chưa mount volume: set "
            "REPOTRANSBENCH_HOST_PATH trong file .env trỏ tới thư mục dataset "
            "trên máy host (xem docker-compose.yml).\n"
            "  3. Biến REPOTRANSBENCH_ROOT trỏ sai đường dẫn bên trong container.\n"
            "Hoặc đặt dataset.enabled: false nếu chưa cần chạy trên dataset."
        )

    if not root.is_dir():
        raise IntakeError(f"dataset.source_root phải là thư mục, không phải file: {root}")

    repos = sorted(
        p for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _IGNORE_REPO_DIRS
    )

    if not repos:
        raise IntakeError(
            f"Thư mục dataset tồn tại nhưng KHÔNG có repo con nào: {root}\n"
            "Mỗi repo phải là 1 thư mục con trực tiếp của đường dẫn này. "
            "Kiểm tra lại: dataset đã giải nén xong chưa, hay volume mount "
            "đang trỏ vào thư mục rỗng?"
        )

    if max_repos and max_repos > 0:
        if len(repos) > max_repos:
            logger.info(
                "Dataset có %d repo, giới hạn còn %d theo dataset.max_repos.",
                len(repos), max_repos,
            )
        repos = repos[:max_repos]

    logger.info("Dataset: tìm thấy %d repo tại %s", len(repos), root)
    return repos


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(BENCHMARK_ROOT))
    from config_loader import ensure_utf8_stdio

    ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

    # `python input/intake.py --dataset <root>` để thử nhánh dataset.
    if len(sys.argv) > 1 and sys.argv[1] == "--dataset":
        root_arg = sys.argv[2] if len(sys.argv) > 2 else ""
        try:
            found = resolve_dataset_repos(root_arg)
            print(f"OK: {len(found)} repo")
            for r in found[:10]:
                n = len(list(r.rglob("*.py")))
                print(f"   - {r.name}  ({n} file .py)")
        except IntakeError as exc:
            print(f"LỖI DATASET:\n{exc}")
            sys.exit(1)
        sys.exit(0)

    arg = sys.argv[1] if len(sys.argv) > 1 else "data/reference_repo"
    try:
        resolved = resolve_target(arg)
        print(f"OK: {arg!r} -> {resolved}")
        if resolved.is_dir():
            py_files = sorted(p.name for p in resolved.rglob("*.py"))
            print(f"   ({len(py_files)} file .py) {py_files[:10]}")
    except IntakeError as exc:
        print(f"LỖI INTAKE: {exc}")
        sys.exit(1)
