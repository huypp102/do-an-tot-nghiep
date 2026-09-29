"""Đo tốc độ 1 hàm trong 1 TIẾN TRÌNH MỚI.

VÌ SAO CẦN: extension PyO3 là module native (.pyd/.so). Khi đã nạp vào tiến
trình, nó KHÔNG thể reload an toàn -- `importlib.reload()` trên module native
không thay được code máy đã nạp. Nên sau khi `maturin develop --release` build
lại extension, muốn đo ĐÚNG code mới thì bắt buộc phải chạy phép đo trong 1
tiến trình mới.

PHẠM VI ÁP DỤNG (quan trọng, đừng gộp nhầm):
  - CHỈ dùng cho việc đo lại GIỮA CÁC VÒNG tối ưu (vài lần cho mỗi hotspot).
  - KHÔNG thay thế thiết kế in-process trong stage6_benchmark/bench.py. Vòng
    lặp training thật gọi hàm preprocessing hàng nghìn lần trong CÙNG tiến
    trình, nên phép đo chuẩn cho bối cảnh đó vẫn phải là in-process; thêm chi
    phí spawn tiến trình vào đó sẽ làm sai lệch con số. Hai bối cảnh khác
    nhau, hai cách đo khác nhau, cố ý tách riêng.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

logger = logging.getLogger("benchmark.stage6_benchmark.measure_subprocess")

RUNNER = Path(__file__).resolve().parent / "_subprocess_runner.py"
DEFAULT_TIMEOUT_SEC = 300


def measure_in_subprocess(
    function_name: str,
    image: np.ndarray,
    benchmark_root: Path,
    warmup: int = 5,
    iterations: int = 20,
    pipeline_module: str = "versions.rust_pure.pipeline",
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> tuple[list[float] | None, str]:
    """Trả về (durations_giây, thông_báo_lỗi). durations=None nghĩa là không
    đo được -- KHÔNG raise, caller tự quyết định xử lý."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="bench_measure_"))
    img_path = tmp_dir / "image.npy"
    try:
        np.save(img_path, np.asarray(image))
        cmd = [
            sys.executable, str(RUNNER),
            "--benchmark-root", str(Path(benchmark_root).resolve()),
            "--function", function_name,
            "--image", str(img_path),
            "--warmup", str(warmup),
            "--iterations", str(iterations),
            "--pipeline-module", pipeline_module,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout_sec
            )
        except subprocess.TimeoutExpired:
            return None, f"đo trong subprocess quá {timeout_sec}s -> huỷ"
        except OSError as exc:
            return None, f"không spawn được subprocess đo: {exc}"

        stdout = (proc.stdout or "").strip()
        # Lấy dòng JSON cuối (phòng khi module in thêm log ra stdout).
        payload = None
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if payload is None:
            return None, (
                f"subprocess không trả JSON hợp lệ (exit={proc.returncode}). "
                f"stderr: {(proc.stderr or '')[-300:]}"
            )
        if not payload.get("ok"):
            return None, str(payload.get("error", "lỗi không rõ"))

        durations = payload.get("durations") or []
        if not durations:
            return None, "subprocess trả về danh sách thời gian rỗng"
        return [float(d) for d in durations], ""
    finally:
        try:
            img_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass


# ===========================================================================
# PHA F -- đo MỌI phiên bản trong CÙNG 1 tiến trình (chế độ repo động).
#
# Khác `measure_in_subprocess` ở trên: hàm đó chỉ đo MỘT phiên bản (rust_pure)
# và dùng ảnh làm workload, nên khi so với số Python đo in-process thì hai bên
# không cùng điều kiện (lỗ hổng #3). Hàm dưới đây đo python_pure VÀ các bản
# Rust trong cùng một tiến trình, cùng bộ đối số thật, cùng vòng lặp
# warmup + N -- tỉ số lấy ra mới là tỉ số hợp lệ.
# ===========================================================================
PAIR_RUNNER = Path(__file__).resolve().parent / "_pair_runner.py"


def measure_pair_in_repo_venv(
    venv_python: Path,
    work_dir: Path,
    capture_dir: Path,
    benchmark_root: Path,
    targets: dict,
    warmup: int = 5,
    iterations: int = 20,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> tuple[dict, str]:
    """Spawn `_pair_runner.py` trong venv CỦA REPO.

    `targets`: {tên hotspot: {phiên bản: đặc tả}} -- lấy từ
        `versions/registry.py::DynamicRegistry`. Đặc tả của `python_pure` có
        khoá `module`/`qualname`; của bản Rust có `ext_module`/`ext_func`.

    Trả về (kết quả, lỗi mức tiến trình). KHÔNG raise.
    """
    import os

    from stage5_compiler_in_the_loop.repo_runner import build_child_env

    capture_dir = Path(capture_dir)
    capture_dir.mkdir(parents=True, exist_ok=True)
    targets_path = capture_dir / "pair_targets.json"
    out_path = capture_dir / "pair_measurements.json"
    targets_path.write_text(json.dumps(targets, ensure_ascii=False), encoding="utf-8")

    cmd = [
        str(venv_python), str(PAIR_RUNNER),
        "--capture-dir", str(capture_dir),
        "--targets", str(targets_path),
        "--out", str(out_path),
        "--warmup", str(warmup),
        "--iterations", str(iterations),
    ]
    env = build_child_env({
        "PYTHONPATH": os.pathsep.join(
            [str(Path(benchmark_root).resolve()), str(Path(work_dir).resolve())]
        ),
    })
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL,
            timeout=timeout_sec, cwd=str(work_dir), env=env,
        )
    except subprocess.TimeoutExpired:
        return {}, f"đo ghép cặp quá {timeout_sec}s -> huỷ"
    except OSError as exc:
        return {}, f"không spawn được tiến trình đo: {exc}"

    if not out_path.exists():
        return {}, (
            f"tiến trình đo không ghi được kết quả (exit={proc.returncode}). "
            f"stderr: {(proc.stderr or '')[-500:]}"
        )
    try:
        return json.loads(out_path.read_text(encoding="utf-8")), ""
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"không đọc được kết quả đo: {exc}"
