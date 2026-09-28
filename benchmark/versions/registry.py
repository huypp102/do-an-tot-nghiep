"""PHA C -- Registry ĐỘNG: thay cho 3 dict 4 hàm cứng.

VẤN ĐỀ ĐƯỢC SỬA (lỗ hổng #1 trong AUDIT_REPORT.md): trước Pha C,
`versions/python_pure/pipeline.py` và `versions/hybrid_pyo3/pipeline.py` có
`PIPELINE_REGISTRY` chỉ gồm 4 khoá cố định của repo viraj7
(`edge_det`, `harris`, `hess_corner_det`, `im_threshold`). Stage 0 phát hiện
hotspot theo TÊN THẬT của repo đang xử lý, nên `registry.get(name)` luôn trả
`None` -> mọi hàm bị bỏ qua, correctness ra ERROR, speedup `n/a`, mà pipeline
vẫn báo thành công.

HAI CHẾ ĐỘ, cố ý tách hẳn:

  LEGACY (`target.mode = "function"`) -- KHÔNG ĐỔI GÌ.
      Vẫn dùng `PIPELINE_REGISTRY` cứng của 4 hàm viraj7, workload là 1 ảnh,
      đo in-process. Đây là yêu cầu "4 hàm viraj7 vẫn chạy được như chế độ
      legacy", nên đường đi đó không được chạm tới.

  ĐỘNG (`target.mode = "file" | "repo"`) -- đường đi MỚI.
      Hàm Python lấy từ CHÍNH REPO qua `module:qualname` (Pha B), workload là
      ĐỐI SỐ THẬT mà bộ test của repo đã tạo ra. Bản Rust lấy từ extension
      build riêng cho từng hotspot (Pha D).

VÌ SAO KHÔNG CÓ HÀM `get(name)` TRẢ CALLABLE Ở CHẾ ĐỘ ĐỘNG: hàm Python của
repo chỉ import được TRONG venv riêng của repo (phụ thuộc của nó không có ở
venv benchmark), và extension Rust cũng chỉ nạp được ở đó. Nên ở chế độ động,
module này chỉ dựng ĐẶC TẢ (module, qualname, ext_module, ...) rồi giao cho
tiến trình con chạy trong venv repo tự resolve. Trả về callable ở đây sẽ là
một lời hứa không giữ được.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("benchmark.versions.registry")

PYTHON_PURE = "python_pure"
RUST_PURE = "rust_pure"
HYBRID_PYO3 = "hybrid_pyo3"
ALL_VERSIONS = (PYTHON_PURE, RUST_PURE, HYBRID_PYO3)


@dataclass
class VersionTarget:
    """Cách gọi được MỘT hotspot trong MỘT phiên bản, ở chế độ ĐỘNG."""

    version: str
    module: str                 # module Python, hoặc tên extension Rust
    qualname: str               # tên hàm trong module đó
    import_root: str | None = None
    is_rust: bool = False

    def as_dict(self) -> dict:
        # Khoá `ext_module`/`ext_func`/`ext_root` là giao diện mà
        # `_replay_runner.py` và `_pair_runner.py` đọc.
        if self.is_rust:
            return {"ext_module": self.module, "ext_func": self.qualname,
                    "ext_root": self.import_root}
        return {"module": self.module, "qualname": self.qualname,
                "import_root": self.import_root}


@dataclass
class DynamicRegistry:
    """Bảng tra cho chế độ ĐỘNG: {tên hotspot: {phiên bản: VersionTarget}}."""

    targets: dict[str, dict[str, VersionTarget]] = field(default_factory=dict)
    # Lý do không tra được, theo tên hotspot -> chuỗi giải thích. Đây là thứ
    # thay cho việc "bỏ qua im lặng" trước đây (yêu cầu Pha 0/C).
    missing: dict[str, dict[str, str]] = field(default_factory=dict)

    def add(self, function_name: str, target: VersionTarget) -> None:
        self.targets.setdefault(function_name, {})[target.version] = target

    def mark_missing(self, function_name: str, version: str, reason: str) -> None:
        self.missing.setdefault(function_name, {})[version] = reason
        logger.warning(
            "Registry động: hotspot '%s' không có bản '%s' -- %s",
            function_name, version, reason,
        )

    def has(self, function_name: str, version: str) -> bool:
        return version in (self.targets.get(function_name) or {})

    def versions_for(self, function_name: str) -> list[str]:
        return sorted((self.targets.get(function_name) or {}).keys())

    def rust_targets_payload(self) -> dict:
        """Định dạng mà `_replay_runner.py`/`_pair_runner.py` nhận: chỉ các
        phiên bản Rust, theo {hotspot: {version: {ext_module, ext_func, ...}}}."""
        out: dict[str, dict] = {}
        for name, per_version in self.targets.items():
            rust = {
                version: t.as_dict()
                for version, t in per_version.items() if t.is_rust
            }
            if rust:
                out[name] = rust
        return out

    def python_targets_payload(self) -> dict:
        """{hotspot: {module, qualname, import_root}} của bản Python gốc."""
        out: dict[str, dict] = {}
        for name, per_version in self.targets.items():
            t = per_version.get(PYTHON_PURE)
            if t is not None:
                out[name] = t.as_dict()
        return out

    def as_dict(self) -> dict:
        return {
            "targets": {
                name: {v: t.as_dict() for v, t in per.items()}
                for name, per in self.targets.items()
            },
            "missing": self.missing,
        }


def build_dynamic_registry(
    hotspot_specs: list,
    built_extensions: dict[str, dict] | None = None,
) -> DynamicRegistry:
    """Dựng registry động.

    `hotspot_specs`: list `HotspotSpec` (stage1_profiling/module_resolve.py) --
        nguồn duy nhất cho bản `python_pure`.
    `built_extensions`: {tên hotspot: {"rust_pure": {...}, "hybrid_pyo3": {...}}}
        do Pha D điền sau khi build xong. Thiếu phiên bản nào thì ghi lý do cụ
        thể vào `missing` chứ KHÔNG im lặng bỏ qua.
    """
    registry = DynamicRegistry()
    built_extensions = built_extensions or {}

    for spec in hotspot_specs:
        registry.add(spec.function_name, VersionTarget(
            version=PYTHON_PURE,
            module=spec.module,
            qualname=spec.qualname,
            import_root=spec.import_root,
            is_rust=False,
        ))

        per_hotspot = built_extensions.get(spec.function_name) or {}
        for version in (RUST_PURE, HYBRID_PYO3):
            info = per_hotspot.get(version)
            if not info:
                registry.mark_missing(
                    spec.function_name, version,
                    "chưa có extension đã build (Stage 4/5 chưa sinh được code "
                    "Rust, hoặc maturin chưa build thành công)",
                )
                continue
            registry.add(spec.function_name, VersionTarget(
                version=version,
                module=info["ext_module"],
                qualname=info.get("ext_func") or spec.function_name,
                import_root=info.get("ext_root"),
                is_rust=True,
            ))

    return registry


# ---------------------------------------------------------------------------
# Chế độ LEGACY -- giữ nguyên hành vi cũ 100%
# ---------------------------------------------------------------------------
def legacy_registry(version: str) -> dict:
    """`PIPELINE_REGISTRY` cứng của 4 hàm viraj7, cho `target.mode=function`.

    Import "lazy" để chế độ động không phải nạp 3 module `versions/*/pipeline.py`
    (và numpy theo chúng) khi không dùng tới.
    """
    if version == PYTHON_PURE:
        from versions.python_pure import pipeline as mod
    elif version == HYBRID_PYO3:
        from versions.hybrid_pyo3 import pipeline as mod
    elif version == RUST_PURE:
        from versions.rust_pure import pipeline as mod
    else:
        raise ValueError(f"phiên bản không hợp lệ: {version!r} (một trong {ALL_VERSIONS})")
    return getattr(mod, "PIPELINE_REGISTRY", {})


def legacy_lookup(version: str, function_name: str) -> tuple[object | None, str]:
    """Tra hàm trong registry legacy. Trả về (callable, lý do nếu không có).

    Khác `PIPELINE_REGISTRY.get(name)` ở chỗ: KHÔNG trả `None` trơ trọi mà kèm
    lý do cụ thể, để Pha 0 ghi được `NO_IMPLEMENTATION` với giải thích thay vì
    một dòng log rồi biến mất khỏi bảng.
    """
    try:
        table = legacy_registry(version)
    except Exception as exc:  # noqa: BLE001
        return None, f"không nạp được registry legacy của '{version}': {exc}"

    fn = table.get(function_name)
    if fn is None:
        return None, (
            f"'{function_name}' không có trong PIPELINE_REGISTRY của '{version}' "
            f"(hiện có: {sorted(table)}). Ở chế độ legacy (target.mode=function) "
            f"danh sách hàm là CỨNG -- đổi sang target.mode=repo để pipeline tự "
            f"nạp hàm thật của repo."
        )
    return fn, ""


def describe_workload_source(mode: str) -> str:
    """Một dòng ghi vào báo cáo: workload đến từ đâu. Người đọc luận văn cần
    biết con số speedup được đo trên input nào."""
    if mode == "function":
        return (
            "legacy: 1 ảnh mẫu từ data/sample_input/, cùng ảnh cho cả 3 phiên bản"
        )
    return (
        "động: đối số THẬT do bộ test của repo tạo ra (ghi bằng "
        "stage1_profiling/capture_plugin.py), cùng bộ đối số cho cả 3 phiên bản"
    )
