"""PHẦN 3.3 -- PREFLIGHT: kiểm tra điều kiện TRƯỚC khi đốt giờ GPU.

Máy thuê tính tiền theo giờ. Một lượt chạy 12 repo mất nhiều giờ, và phát hiện
"thiếu maturin" ở repo thứ 7 là mất trắng phần đã chạy. Nên mọi điều kiện phải
được kiểm ở đây, TRƯỚC khi gọi LLM lần đầu.

Nguyên tắc: FAIL là DỪNG NGAY kèm hướng dẫn cụ thể. Không có "cảnh báo rồi
chạy tiếp" cho những thứ mà thiếu nó thì kết quả vô nghĩa.

Chạy độc lập:  python preflight.py [--profile pilot_linux] [--run-id <id>]
Trả 0 nếu mọi mục BẮT BUỘC đều đạt, khác 0 nếu không.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

ensure_utf8_stdio()
logger = logging.getLogger("benchmark.preflight")

MIN_PYTHON = (3, 10)
MIN_FREE_GB = 20.0
HTTP_TIMEOUT = 30
WARMUP_TIMEOUT = 300


@dataclass
class Check:
    name: str
    ok: bool
    required: bool = True
    detail: str = ""
    fix: str = ""
    data: dict = field(default_factory=dict)


def _run(cmd: list[str], timeout: int = 60, cwd: str | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return 124, f"quá {timeout}s"
    except OSError as exc:
        return 127, str(exc)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


# ---------------------------------------------------------------- các phép kiểm
def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    return Check(
        "python_version", ok,
        detail=f"Python {v.major}.{v.minor}.{v.micro} tại {sys.executable}",
        fix=f"cần Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        data={"version": f"{v.major}.{v.minor}.{v.micro}", "executable": sys.executable},
    )


def check_venv_creation() -> Check:
    """Tạo thử một venv RỒI XOÁ. Pipeline dựng venv cho từng repo, nên nếu
    bước này không chạy được thì mọi repo sẽ ra INSTALL_FAILED."""
    tmp = Path(tempfile.mkdtemp(prefix="preflight_venv_"))
    try:
        if shutil.which("uv"):
            code, out = _run(["uv", "venv", str(tmp / "v"), "--python", sys.executable], 180)
            tool = "uv"
        else:
            code, out = _run([sys.executable, "-m", "venv", str(tmp / "v")], 180)
            tool = "python -m venv"
        py = (tmp / "v" / ("Scripts" if os.name == "nt" else "bin")
              / ("python.exe" if os.name == "nt" else "python"))
        ok = code == 0 and py.exists()
        return Check(
            "venv_creation", ok,
            detail=f"tạo được venv thử bằng {tool}" if ok else out[-300:],
            fix="cài `python3-venv` (apt install python3-venv) hoặc `uv`",
            data={"tool": tool},
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_rust_toolchain() -> list[Check]:
    checks = []
    for exe, fix in (
        ("cargo", "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"),
        ("rustc", "cùng rustup như trên"),
        ("maturin", "pip install maturin"),
    ):
        path = shutil.which(exe)
        if path is None:
            checks.append(Check(exe, False, detail="không có trong PATH", fix=fix))
            continue
        code, out = _run([exe, "--version"], 30)
        ver = out.splitlines()[0] if out else "?"
        checks.append(Check(
            exe, code == 0, detail=ver, fix=fix, data={"path": path, "version": ver}
        ))
    return checks


def _ollama_get(base_url: str, path: str) -> tuple[bool, object]:
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
            return True, json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            json.JSONDecodeError) as exc:
        return False, str(exc)


def check_ollama(cfg: dict) -> list[Check]:
    """Ollama truy cập được, và CÓ ĐỦ 2 model mà config yêu cầu."""
    from stage4_llm_transpile.model_backend import (
        DECISION_ROLE,
        GENERATOR_ROLE,
        resolve_model_for_role,
    )

    local = ((cfg.get("llm") or {}).get("local") or {})
    base_url = str(local.get("base_url") or "").strip()
    checks: list[Check] = []

    if not base_url:
        return [Check(
            "ollama_reachable", False,
            detail="llm.local.base_url rỗng",
            fix="export OLLAMA_BASE_URL=http://127.0.0.1:11434",
        )]

    ok, payload = _ollama_get(base_url, "/api/tags")
    checks.append(Check(
        "ollama_reachable", ok,
        detail=f"{base_url} trả về {len(payload.get('models', []))} model"
        if ok and isinstance(payload, dict) else f"{base_url}: {payload}",
        fix=(
            "khởi động `ollama serve` và kiểm tra OLLAMA_BASE_URL. Nếu cổng "
            "11434 đã bị tiến trình khác chiếm, mở bản riêng ở 11435 "
            "(xem scripts/setup_linux.sh) rồi export OLLAMA_BASE_URL=http://127.0.0.1:11435"
        ),
        data={"base_url": base_url},
    ))
    if not ok:
        return checks

    installed = {
        m.get("name", "") for m in (payload.get("models") or [])
        if isinstance(m, dict)
    }
    # Ollama hay trả kèm tag `:latest`; so cả hai dạng để không báo thiếu oan.
    installed_bare = {n.split(":")[0] for n in installed}
    wanted = {
        "generator": resolve_model_for_role(cfg, GENERATOR_ROLE),
        "decision": resolve_model_for_role(cfg, DECISION_ROLE),
    }
    missing = [
        f"{role}={name}" for role, name in wanted.items()
        if name not in installed and name.split(":")[0] not in installed_bare
    ]
    checks.append(Check(
        "ollama_models", not missing,
        detail=f"cần {wanted}; server có {sorted(installed)}",
        fix="docker/ollama: `ollama pull " + " && ollama pull ".join(wanted.values()) + "`",
        data={"wanted": wanted, "installed": sorted(installed)},
    ))
    return checks


def warmup_models_and_check_loaded(cfg: dict) -> list[Check]:
    """Gọi thử MỖI model một lượt, rồi `ollama ps` phải thấy CẢ HAI loaded.

    VÌ SAO KHÔNG CHỈ `ollama pull`: pull chỉ chứng minh model có trên đĩa.
    Điều cần biết là VRAM có đủ cho cả hai model cùng lúc hay không. Nếu Ollama
    phải nạp-gỡ luân phiên thì mỗi lần đổi vai trò sẽ mất vài chục giây, và
    lượt chạy 12 repo sẽ chậm gấp nhiều lần dự tính.
    """
    from stage4_llm_transpile.model_backend import (
        DECISION_ROLE,
        GENERATOR_ROLE,
        LocalModelBackend,
        ModelBackendError,
        resolve_model_for_role,
        resolve_num_ctx_for_role,
    )

    local = ((cfg.get("llm") or {}).get("local") or {})
    base_url = str(local.get("base_url") or "")
    checks: list[Check] = []

    backend = LocalModelBackend(
        model=resolve_model_for_role(cfg, GENERATOR_ROLE),
        base_url=base_url,
        api_style=str(local.get("api_style") or "ollama"),
    )
    for role, role_const in (("generator", GENERATOR_ROLE), ("decision", DECISION_ROLE)):
        name = resolve_model_for_role(cfg, role_const)
        t0 = time.perf_counter()
        try:
            reply = backend.chat(
                [{"role": "user", "content": "Trả lời đúng một từ: OK"}],
                model=name, num_ctx=resolve_num_ctx_for_role(cfg, role_const),
                temperature=0.0,
            )
            checks.append(Check(
                f"warmup_{role}", True,
                detail=f"{name} trả lời sau {time.perf_counter() - t0:.1f}s "
                       f"({(reply or '').strip()[:40]!r})",
                data={"model": name, "seconds": round(time.perf_counter() - t0, 1)},
            ))
        except ModelBackendError as exc:
            checks.append(Check(
                f"warmup_{role}", False, detail=str(exc)[:300],
                fix=f"kiểm tra model '{name}' đã pull chưa và server còn VRAM không",
            ))

    ok, payload = _ollama_get(base_url, "/api/ps")
    loaded = []
    if ok and isinstance(payload, dict):
        loaded = [m.get("name", "") for m in (payload.get("models") or [])]
    wanted = {
        resolve_model_for_role(cfg, GENERATOR_ROLE),
        resolve_model_for_role(cfg, DECISION_ROLE),
    }
    both = all(
        any(w == l or w.split(":")[0] == l.split(":")[0] for l in loaded) for w in wanted
    )
    checks.append(Check(
        "ollama_ps_both_loaded", both, required=False,
        detail=f"`ollama ps` thấy {loaded}; cần cả {sorted(wanted)}",
        fix=(
            "đặt OLLAMA_MAX_LOADED_MODELS=2 và OLLAMA_KEEP_ALIVE=30m rồi khởi "
            "động lại Ollama. Nếu vẫn chỉ thấy 1 model thì VRAM không đủ cho cả "
            "hai -> giảm generator_num_ctx/decision_num_ctx hoặc chọn model nhỏ hơn. "
            "KHÔNG phải lỗi chặn, nhưng lượt chạy sẽ chậm vì nạp-gỡ luân phiên."
        ),
        data={"loaded": loaded},
    ))

    code, out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                      "--format=csv,noheader"], 30)
    checks.append(Check(
        "nvidia_smi", code == 0, required=False,
        detail=out[:300] if code == 0 else "không chạy được nvidia-smi",
        fix="không bắt buộc -- chỉ để ghi lại tình trạng VRAM vào metadata",
        data={"raw": out[:500]},
    ))
    return checks


def check_dataset(cfg: dict) -> list[Check]:
    """Dataset đúng cấu trúc, và đường dẫn KHÔNG trỏ vào `target_projects`."""
    root_str = str(((cfg.get("dataset") or {}).get("source_root")) or "").strip()
    checks: list[Check] = []

    if not root_str:
        return [Check(
            "dataset_root", False,
            detail="dataset.source_root rỗng",
            fix="export REPOTRANSBENCH_ROOT=/đường/dẫn/source_projects/Python",
        )]

    root = Path(root_str)
    # QUY TẮC CỐ ĐỊNH của đồ án: Stage 0-4 KHÔNG được đọc target_projects (đó
    # là bản dịch Rust tham chiếu, dùng nó làm đầu vào là rò rỉ đáp án).
    leaks = "target_projects" in root.as_posix()
    checks.append(Check(
        "dataset_not_target_projects", not leaks,
        detail=f"source_root = {root}",
        fix=(
            "REPOTRANSBENCH_ROOT phải trỏ vào source_projects/Python, KHÔNG "
            "phải target_projects -- dùng target_projects làm đầu vào là rò rỉ "
            "đáp án và làm toàn bộ kết quả mất giá trị."
        ),
    ))

    if not root.is_dir():
        checks.append(Check(
            "dataset_root", False, detail=f"không phải thư mục: {root}",
            fix="kiểm tra đã giải nén dataset chưa, và REPOTRANSBENCH_ROOT trỏ đúng chỗ",
        ))
        return checks

    repos = [
        p for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ]
    n = len(repos)
    checks.append(Check(
        "dataset_root", n > 0,
        detail=f"{n} repo con trong {root}",
        fix="thư mục tồn tại nhưng rỗng -- giải nén lại dataset",
        data={"n_repos": n, "root": str(root)},
    ))
    # 171 là số repo Python của bản dataset đang dùng. Thiếu thì vẫn chạy được
    # (không chặn), nhưng phải ghi lại vì nó ảnh hưởng tới việc chọn mẫu.
    checks.append(Check(
        "dataset_expected_count", n == 171, required=False,
        detail=f"thấy {n} repo (bản đang dùng có 171)",
        fix="không chặn -- nhưng ghi rõ trong luận văn là chạy trên bản dataset khác",
        data={"n_repos": n},
    ))
    return checks


def check_disk(cfg: dict) -> Check:
    ro = cfg.get("repo_oracle") or {}
    from stage5_compiler_in_the_loop.repo_runner import default_work_root

    target = Path(str(ro.get("work_root") or "").strip() or default_work_root())
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return Check("disk_space", False, detail=str(exc),
                     fix=f"không đọc được dung lượng ổ chứa {target}")
    free_gb = usage.free / (1024 ** 3)
    return Check(
        "disk_space", free_gb >= MIN_FREE_GB,
        detail=f"{free_gb:.1f} GB trống tại {probe}",
        fix=(
            f"cần >= {MIN_FREE_GB} GB. Mỗi repo dựng venv riêng; đặt "
            f"RTB_WORK_DIR sang ổ còn nhiều chỗ, và giữ repo_oracle.keep_venv=false."
        ),
        data={"free_gb": round(free_gb, 1), "path": str(probe)},
    )


def check_git_state() -> Check:
    """Commit hash + cây làm việc có sạch không.

    Cây bẩn KHÔNG chặn (đang phát triển là chuyện thường), nhưng PHẢI ghi lại:
    commit hash khi đó không mô tả đủ code đã chạy, nên kết quả không tái lập
    được nếu không biết điều này.
    """
    import env_metadata

    git = env_metadata._git_commit(BENCHMARK_ROOT)  # noqa: SLF001
    commit = git.get("commit")
    return Check(
        "git_state", commit is not None, required=False,
        detail=(
            f"commit {str(commit)[:12]}"
            + ("  [CÂY LÀM VIỆC BẨN -- hash không mô tả đủ code đã chạy]"
               if git.get("dirty") else "  (cây sạch)")
            if commit else str(git.get("note", "không lấy được commit"))
        ),
        fix="commit hoặc stash thay đổi trước khi chạy để kết quả tái lập được",
        data=git,
    )


# ---------------------------------------------------------------------------
def run_all(cfg: dict, skip_llm: bool = False) -> list[Check]:
    checks: list[Check] = [check_python(), check_venv_creation()]
    checks += check_rust_toolchain()
    checks += check_dataset(cfg)
    checks.append(check_disk(cfg))
    checks.append(check_git_state())

    llm_on = bool((cfg.get("llm") or {}).get("enabled", False))
    if skip_llm or not llm_on:
        checks.append(Check(
            "ollama_reachable", True, required=False,
            detail="bỏ qua (llm.enabled=false hoặc --skip-llm)",
        ))
        return checks

    ollama = check_ollama(cfg)
    checks += ollama
    # Chỉ warmup khi server đã truy cập được VÀ có đủ model -- gọi model chưa
    # pull chỉ tạo thêm một lỗi khó đọc.
    if all(c.ok for c in ollama):
        checks += warmup_models_and_check_loaded(cfg)
    return checks


def format_report(checks: list[Check]) -> str:
    lines = ["PREFLIGHT", "=" * 92]
    header = f"{'mục':<28}{'bắt buộc':>10}{'kết quả':>10}  chi tiết"
    lines.append(header)
    lines.append("-" * len(header))
    for c in checks:
        lines.append(
            f"{c.name:<28}{('có' if c.required else 'không'):>10}"
            f"{('ĐẠT' if c.ok else 'HỎNG'):>10}  {c.detail[:150]}"
        )
    failed_required = [c for c in checks if c.required and not c.ok]
    failed_optional = [c for c in checks if not c.required and not c.ok]
    lines.append("")
    if failed_required:
        lines.append("PHẢI SỬA TRƯỚC KHI CHẠY:")
        for c in failed_required:
            lines.append(f"  [{c.name}] {c.detail[:200]}")
            lines.append(f"     cách sửa: {c.fix}")
    if failed_optional:
        lines.append("Không chặn, nhưng nên biết:")
        for c in failed_optional:
            lines.append(f"  [{c.name}] {c.detail[:200]}")
            lines.append(f"     {c.fix}")
    if not failed_required:
        lines.append("Mọi mục BẮT BUỘC đều đạt -- có thể chạy thực nghiệm.")
    return "\n".join(lines)


def write_metadata(
    checks: list[Check], cfg: dict, results_dir: Path, run_id: str
) -> Path:
    """Ghi `results/<run_id>/metadata.json`."""
    import env_metadata

    out_dir = Path(results_dir) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "metadata.json"
    payload = {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": cfg.get("_profile"),
        "environment": env_metadata.collect(cfg, BENCHMARK_ROOT),
        "preflight": [
            {"name": c.name, "ok": c.ok, "required": c.required,
             "detail": c.detail, "data": c.data}
            for c in checks
        ],
        "preflight_passed": all(c.ok for c in checks if c.required),
        "config_effective": {
            k: v for k, v in cfg.items() if not k.startswith("_")
        },
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight cho thực nghiệm")
    parser.add_argument("--profile", default=os.environ.get("RUN_PROFILE") or None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--skip-llm", action="store_true",
                        help="bỏ qua kiểm tra Ollama (dùng khi thử trên máy dev)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
    cfg = load_config(profile=args.profile)
    checks = run_all(cfg, skip_llm=args.skip_llm)
    report = format_report(checks)
    print(report)

    run_id = args.run_id or time.strftime("run_%Y%m%d_%H%M%S")
    results_dir = BENCHMARK_ROOT / (cfg.get("paths") or {}).get("results_dir", "results")
    meta_path = write_metadata(checks, cfg, results_dir, run_id)
    print(f"\nĐã ghi: {meta_path}")

    failed = [c for c in checks if c.required and not c.ok]
    if failed:
        print(f"\nPREFLIGHT HỎNG ({len(failed)} mục bắt buộc) -- DỪNG, không chạy "
              "thực nghiệm để khỏi đốt giờ GPU.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
