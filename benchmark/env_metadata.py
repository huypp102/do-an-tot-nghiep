"""PHA F -- Metadata TÁI LẬP cho mỗi lượt chạy (lỗ hổng #6 trong AUDIT_REPORT.md).

Trước Pha F, file kết quả chỉ có `timestamp`. Số đo trên máy thuê không gắn
được với môi trường sinh ra nó, nên câu hỏi hiển nhiên của hội đồng -- "đo trên
CPU nào, commit nào, model nào" -- không trả lời được từ dữ liệu.

Mọi thứ ở đây là best-effort: thiếu `git`, thiếu `rustc` thì ghi `null` kèm lý
do, KHÔNG raise. Một lượt benchmark không được chết vì không đọc được số
phiên bản.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("benchmark.env_metadata")

_VERSION_TIMEOUT_SEC = 15


def _tool_version(exe: str, args: list[str] | None = None) -> str | None:
    """`<exe> --version`, hoặc None nếu không có/không chạy được."""
    if shutil.which(exe) is None:
        return None
    try:
        proc = subprocess.run(
            [exe, *(args or ["--version"])],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_VERSION_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = (proc.stdout or proc.stderr or "").strip()
    return out.splitlines()[0] if out else None


def _git_commit(repo_root: Path) -> dict:
    """Commit hiện tại của CODE BENCHMARK (không phải của repo đang đo).

    Đây là thứ cho biết số liệu được sinh bởi phiên bản pipeline nào -- cần
    thiết khi bảng kết quả trong luận văn phải tái lập lại được.
    """
    if shutil.which("git") is None:
        return {"commit": None, "note": "không có git trong PATH"}
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_VERSION_TIMEOUT_SEC,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(repo_root),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_VERSION_TIMEOUT_SEC,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"commit": None, "note": f"không chạy được git: {exc}"}
    if rev.returncode != 0:
        return {"commit": None, "note": (rev.stderr or "").strip()[:200]}
    return {
        "commit": (rev.stdout or "").strip(),
        # Cây làm việc bẩn nghĩa là commit hash KHÔNG mô tả đủ code đã chạy --
        # phải ghi lại, nếu không kết quả không tái lập được.
        "dirty": bool((dirty.stdout or "").strip()),
    }


def _load_average() -> list[float] | None:
    """Tải máy lúc đo (chỉ có trên POSIX). Dùng để hậu kiểm xem lượt đo nào
    diễn ra trong lúc máy đang bận -- xem lỗ hổng #15."""
    try:
        return [round(x, 3) for x in os.getloadavg()]
    except (AttributeError, OSError):
        return None


def collect(cfg: dict | None = None, benchmark_root: Path | None = None) -> dict:
    """Thu thập toàn bộ metadata tái lập. An toàn để gọi ở bất kỳ đâu."""
    root = Path(benchmark_root) if benchmark_root else Path(__file__).resolve().parent
    cfg = cfg or {}
    llm_cfg = (cfg.get("llm") or {})
    backend_name = llm_cfg.get("backend", "api")
    backend_cfg = llm_cfg.get(backend_name) or {}

    meta: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": socket.gethostname(),
        "cpu": {
            "processor": platform.processor() or None,
            "machine": platform.machine(),
            "n_cores_logical": os.cpu_count(),
        },
        "os": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
        },
        "python": {
            "version": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "toolchain": {
            "rustc": _tool_version("rustc"),
            "cargo": _tool_version("cargo"),
            "maturin": _tool_version("maturin"),
            "uv": _tool_version("uv"),
        },
        "git": _git_commit(root),
        "load_average": _load_average(),
        "llm": {
            "enabled": bool(llm_cfg.get("enabled", False)),
            "backend": backend_name,
            "num_agents": llm_cfg.get("num_agents"),
            # Tên 2 model và num_ctx: trước Pha F chỉ có trong log, không vào
            # file kết quả -- nên không biết bảng nào sinh bởi model nào.
            "generator_model": backend_cfg.get("generator_model") or backend_cfg.get("model"),
            "decision_model": backend_cfg.get("decision_model") or backend_cfg.get("model"),
            "generator_num_ctx": backend_cfg.get("generator_num_ctx"),
            "decision_num_ctx": backend_cfg.get("decision_num_ctx"),
        },
    }

    missing = [k for k, v in meta["toolchain"].items() if v is None]
    if missing:
        meta["toolchain"]["missing_note"] = (
            "không tìm thấy trên máy này: " + ", ".join(missing)
            + " -- các mục phụ thuộc chúng sẽ bị bỏ qua, không phải lỗi"
        )
    return meta


def format_for_report(meta: dict) -> str:
    """Khối metadata dạng text, để dán vào đầu `report_*.md`."""
    cpu = meta.get("cpu") or {}
    tc = meta.get("toolchain") or {}
    git = meta.get("git") or {}
    llm = meta.get("llm") or {}
    lines = [
        "METADATA TÁI LẬP",
        "=" * 72,
        f"  thời điểm       : {meta.get('collected_at')}",
        f"  hostname        : {meta.get('hostname')}",
        f"  CPU             : {cpu.get('processor') or cpu.get('machine')} "
        f"({cpu.get('n_cores_logical')} core logic)",
        f"  OS              : {(meta.get('os') or {}).get('platform')}",
        f"  Python          : {(meta.get('python') or {}).get('version')} "
        f"({(meta.get('python') or {}).get('implementation')})",
        f"  rustc / cargo   : {tc.get('rustc') or '(không có)'} / {tc.get('cargo') or '(không có)'}",
        f"  maturin         : {tc.get('maturin') or '(không có)'}",
        f"  git commit      : {git.get('commit') or '(không lấy được)'}"
        + ("  [CÂY LÀM VIỆC BẨN -- commit không mô tả đủ code đã chạy]"
           if git.get("dirty") else ""),
        f"  LLM             : {'bật' if llm.get('enabled') else 'tắt'}"
        + (f", generator={llm.get('generator_model')} (num_ctx={llm.get('generator_num_ctx')})"
           f", decision={llm.get('decision_model')} (num_ctx={llm.get('decision_num_ctx')})"
           if llm.get("enabled") else ""),
    ]
    if meta.get("load_average") is not None:
        lines.append(f"  load average    : {meta['load_average']}")
    return "\n".join(lines)
