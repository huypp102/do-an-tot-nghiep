"""Tải và chuẩn hoá config.yaml cho toàn bộ benchmark.

Đặt ở benchmark/config_loader.py (root của benchmark/, không nằm trong
stage6_benchmark/ hay data/) để mọi package con (data, versions, stage0_graph,
stage6_benchmark, ...) đều
import được như nhau qua `from config_loader import load_config`, sau khi
BENCHMARK_ROOT đã được thêm vào sys.path (xem stage6_benchmark/bench.py).

Yêu cầu PyYAML (xem requirements.txt: `pip install -r requirements.txt`).
Không tự viết YAML parser rút gọn ở đây để tránh bug âm thầm khi config.yaml
có cấu trúc phức tạp hơn -- PyYAML là dependency chuẩn, rất nhẹ.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("benchmark.config_loader")

try:
    import yaml  # PyYAML
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Thiếu thư viện PyYAML để đọc config.yaml.\n"
        "Cài đặt bằng lệnh:  pip install -r requirements.txt\n"
        "(hoặc riêng lẻ:      pip install pyyaml)"
    ) from exc

BENCHMARK_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BENCHMARK_ROOT / "config.yaml"


def ensure_utf8_stdio() -> None:
    """Ép stdout/stderr dùng UTF-8 (thay ký tự lỗi thay vì crash) thay vì
    codepage mặc định của Windows console (vd cp1252) -- codepage đó KHÔNG
    encode được tiếng Việt có dấu và sẽ làm print()/logging crash với
    UnicodeEncodeError khi in log/báo cáo tiếng Việt. Gọi hàm này SỚM nhất có
    thể ở đầu mọi entry point script (bench.py, report.py, ...), trước khi in
    hoặc log bất kỳ text tiếng Việt nào."""
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# ${VAR} | ${VAR:-mặc định} | $VAR
# Dùng regex riêng thay vì os.path.expandvars vì expandvars KHÔNG hiểu cú
# pháp `:-default` của shell (nó sẽ trả về nguyên chuỗi), mà đây chính là cú
# pháp ta cần để config.yaml có giá trị dự phòng khi máy chưa set biến.
_ENV_VAR_RE = re.compile(
    r"""
    \$(?:
        \{ (?P<braced>[A-Za-z_][A-Za-z0-9_]*) (?: :- (?P<default>[^}]*) )? \}
      | (?P<bare>[A-Za-z_][A-Za-z0-9_]*)
    )
    """,
    re.VERBOSE,
)


def expand_env_vars(value: Any) -> Any:
    """Mở rộng biến môi trường trong CHUỖI, đệ quy vào dict/list.

    Hỗ trợ 3 dạng:
        $VAR                -> giá trị biến, hoặc "" nếu chưa set
        ${VAR}              -> như trên
        ${VAR:-mặc định}    -> giá trị biến, hoặc "mặc định" nếu chưa set/rỗng

    Nhờ dạng thứ 3, config.yaml KHÔNG cần hardcode đường dẫn của bất kỳ máy
    nào: máy thuê GPU chỉ cần set biến môi trường, không sửa file config.
    """
    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            name = m.group("braced") or m.group("bare")
            default = m.group("default")
            env_value = os.environ.get(name)
            if env_value:
                return env_value
            if default is not None:
                return default
            if env_value is None:
                logger.warning(
                    "config.yaml tham chiếu biến môi trường $%s nhưng biến này "
                    "CHƯA được set và cũng không có giá trị mặc định "
                    "(${%s:-...}) -> thay bằng chuỗi rỗng.", name, name,
                )
            return ""

        return _ENV_VAR_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    return value


def load_config(
    path: str | Path | None = None,
    expand_env: bool = True,
    profile: str | None = None,
) -> dict[str, Any]:
    """Đọc config.yaml và trả về dict.

    expand_env=True (mặc định): mở rộng mọi biến môi trường dạng
    `${VAR:-default}` trong giá trị config -- xem `expand_env_vars`. Nhờ vậy
    không có đường dẫn nào của máy cụ thể bị hardcode trong file config.

    `profile`: tên file trong `profiles/` để GỘP ĐÈ lên config gốc (PHẦN 3.2).
    Không truyền thì đọc biến môi trường `RUN_PROFILE`. Nhờ vậy `config.yaml`
    chỉ chứa mặc định TRUNG TÍNH, còn phần phụ thuộc máy nằm trong profile +
    biến môi trường -- máy thuê không phải sửa code.

    TODO: nếu cần strict schema validation (báo lỗi sớm khi thiếu key), cân
    nhắc thêm pydantic model ở đây thay vì truy cập dict trực tiếp như hiện tại.
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file config: {cfg_path}. "
            "Chạy từ đúng thư mục benchmark/ hoặc truyền path rõ ràng."
        )
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"{cfg_path} rỗng hoặc sai định dạng (không parse ra được mapping).")

    # --- PHẦN 3.2: gộp PROFILE lên trên config gốc ------------------------
    name = profile if profile is not None else os.environ.get("RUN_PROFILE", "")
    if name:
        cfg = merge_profile(cfg, name, cfg_path.parent)
    return expand_env_vars(cfg) if expand_env else cfg


def deep_merge(base: dict, override: dict) -> dict:
    """Gộp `override` LÊN TRÊN `base`, đệ quy theo dict.

    Chỉ những khoá có trong `override` bị ghi đè -- nhờ vậy profile chỉ cần ghi
    phần khác biệt, còn `config.yaml` giữ vai trò nguồn mặc định duy nhất. List
    bị THAY THẾ chứ không nối: `ablation.arms: [graph]` phải nghĩa là "chỉ một
    nhánh", không phải "thêm một nhánh".
    """
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def available_profiles(benchmark_root: Path | None = None) -> list[str]:
    root = Path(benchmark_root) if benchmark_root else DEFAULT_CONFIG_PATH.parent
    d = root / "profiles"
    return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []


def merge_profile(cfg: dict, name: str, benchmark_root: Path) -> dict:
    """Đọc `profiles/<name>.yaml` và gộp đè lên `cfg`.

    Raise `FileNotFoundError` với danh sách profile có sẵn: sai tên profile mà
    chạy tiếp bằng mặc định là kiểu lỗi tệ nhất trên máy thuê -- lượt chạy tốn
    tiền xong mới phát hiện nó dùng cấu hình khác với ý định.
    """
    prof_path = Path(benchmark_root) / "profiles" / f"{name}.yaml"
    if not prof_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy profile '{name}' ({prof_path}). "
            f"Có sẵn: {available_profiles(benchmark_root) or '(không có profile nào)'}"
        )
    with prof_path.open("r", encoding="utf-8") as f:
        prof = yaml.safe_load(f) or {}
    if not isinstance(prof, dict):
        raise ValueError(f"{prof_path} sai định dạng (không parse ra được mapping).")
    logger.info("Đã gộp profile '%s' từ %s lên config gốc.", name, prof_path)
    merged = deep_merge(cfg, prof)
    merged["_profile"] = name
    return merged


if __name__ == "__main__":
    import json

    print(json.dumps(load_config(), indent=2, ensure_ascii=False))
