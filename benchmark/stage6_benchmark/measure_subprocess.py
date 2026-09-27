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
                cmd, capture_output=True, text=True, timeout=timeout_sec
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
