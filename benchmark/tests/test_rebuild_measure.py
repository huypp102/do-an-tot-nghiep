"""Yeu cau 4: chung minh moi vong do DUNG code vua sinh, khong lap lai so
lieu ban cu.

May nay khong co Rust toolchain nen khong build duoc extension that. Thay
vao do dung 1 module Python gia lap extension: no doc `version.txt` luc
IMPORT de quyet dinh dung ban CHAM hay ban NHANH -- dung co che ma 1
extension vua rebuild se the hien. Do trong subprocess moi => bat duoc doi
code, giong het tinh huong that.
"""
import json
import logging
import statistics
import sys
import tempfile
import textwrap
from pathlib import Path

import numpy as np

BENCH = Path(__file__).resolve().parent.parent
# Fixture TỰ DỰNG trong thư mục tạm. Trước đây test này trỏ vào một thư mục
# NGOÀI repo (.scratch/fake_ext) nên nó hỏng ngay khi thư mục đó bị dọn -- test
# phải mang theo fixture của chính nó, không mượn của ai.
FAKE = Path(tempfile.mkdtemp(prefix="rtb_fake_ext_"))
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

from stage6_benchmark.measure_subprocess import measure_in_subprocess  # noqa: E402

_FAKE_PIPELINE = textwrap.dedent(
    """
    from pathlib import Path

    # Đọc phiên bản LÚC IMPORT: đó là điểm mấu chốt. Extension native thật cũng
    # chỉ đổi khi được nạp trong một tiến trình MỚI, nên module này mô phỏng
    # đúng cơ chế đó.
    _V = (Path(__file__).resolve().parent / "version.txt").read_text(
        encoding="utf-8"
    ).strip()


    def _slow(image):
        total = 0
        for _ in range(40):
            total = int(image.sum())
        return total


    def _fast(image):
        return int(image.sum())


    edge_det = _slow if _V == "slow" else _fast


    class _AnyName(dict):
        \"\"\"Trả về CÙNG một hàm cho bất kỳ tên hotspot nào.

        Tên hotspot do FuncRank chọn nên không biết trước (ở đây là `normalize`
        của repo giả, không phải `edge_det`). Khoá cứng một tên sẽ làm phép đo
        thất bại vì lý do chẳng liên quan gì tới điều đang kiểm chứng.
        \"\"\"

        def get(self, key, default=None):
            return edge_det

        def __contains__(self, key):
            return True

        def __getitem__(self, key):
            return edge_det


    PIPELINE_REGISTRY = _AnyName()
    """
)


def set_version(v):
    """Đổi phiên bản 'extension'. Tiến trình đo sau đó phải thấy bản mới."""
    FAKE.mkdir(parents=True, exist_ok=True)
    (FAKE / "fake_pipeline.py").write_text(_FAKE_PIPELINE, encoding="utf-8")
    (FAKE / "version.txt").write_text(v, encoding="utf-8")


image = np.arange(256 * 256, dtype=np.uint8).reshape(256, 256)

print("=" * 72)
print("PHAN 1 -- Do truc tiep trong subprocess: 2 ban code khac nhau")
print("=" * 72)

set_version("slow")
d_slow, err1 = measure_in_subprocess(
    "edge_det", image, benchmark_root=FAKE, warmup=1, iterations=7,
    pipeline_module="fake_pipeline",
)
assert d_slow, f"do ban cham that bai: {err1}"

set_version("fast")
d_fast, err2 = measure_in_subprocess(
    "edge_det", image, benchmark_root=FAKE, warmup=1, iterations=7,
    pipeline_module="fake_pipeline",
)
assert d_fast, f"do ban nhanh that bai: {err2}"

m_slow = statistics.mean(d_slow) * 1000
m_fast = statistics.mean(d_fast) * 1000
print(f"  ban CHAM : {m_slow:.4f} ms")
print(f"  ban NHANH: {m_fast:.4f} ms")
print(f"  ty le    : {m_slow / m_fast:.1f}x")
assert m_slow > m_fast * 3, (
    f"2 ban phai khac nhau ro ret; cham={m_slow:.4f}ms nhanh={m_fast:.4f}ms"
)
print("  OK: subprocess bat duoc code MOI, so lieu KHAC nhau ro ret")

print()
print("=" * 72)
print("PHAN 2 -- Vong lap Stage 6: speedup PHAI khac nhau giua cac vong")
print("=" * 72)

import stage4_llm_transpile.model_backend as mb  # noqa: E402
import stage5_compiler_in_the_loop.rebuild as rb  # noqa: E402
import stage6_benchmark.measure_subprocess as ms  # noqa: E402

RUST_SRC = "use pyo3::prelude::*;\n#[pyfunction]\nfn f() {}\n"


class MockBackend:
    name = "mock"

    def __init__(self):
        self.decision_calls = 0

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        if system and "ĐÁNH GIÁ" in system:
            self.decision_calls += 1
            verdict = "CONTINUE" if self.decision_calls < 2 else "STOP"
            return (f"## Decision\nACCEPT\n\n## Continue\n{verdict}\n\n"
                    f"## Next strategy\n{'Bo vong lap thua' if verdict == 'CONTINUE' else 'none'}")
        return f"## Rust code\n```rust\n{RUST_SRC}```\n\n## Optimization strategy\nx."

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}])


backend = MockBackend()
mb.get_model_backend = lambda cfg: backend

# Gia lap rebuild: doi ban CHAM -> NHANH roi bao REBUILT_OK.
rebuild_calls = []


def fake_rebuild(draft_path, crate_dir, timeout_sec=300):
    rebuild_calls.append(str(draft_path))
    set_version("fast")  # dung nhu maturin vua build code moi
    return rb.RebuildResult(rb.REBUILT_OK, "fake build ok")


rb.rebuild_from_draft = fake_rebuild
rb.restore_original_lib = lambda crate_dir: False  # khong dung lib.rs that

# Do lai giua cac vong -> tro vao module gia.
real_measure = ms.measure_in_subprocess
ms.measure_in_subprocess = lambda function_name, image, benchmark_root, warmup=5, \
    iterations=20, pipeline_module="versions.rust_pure.pipeline", timeout_sec=300: \
    real_measure(function_name, image, FAKE, warmup, iterations, "fake_pipeline", timeout_sec)

set_version("slow")  # vong 1 chay tren ban CHAM

import run_pipeline  # noqa: E402
from stage6_benchmark import bench  # noqa: E402


class AnyFnRegistry(dict):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def get(self, k, default=None):
        return self._fn

    def __contains__(self, k):
        return True

    def __getitem__(self, k):
        return self._fn


py_fn = lambda img: np.zeros_like(np.asarray(img), dtype=np.uint8)  # noqa: E731
# Ban "rust" vong 1 = ban CHAM, tra cung ket qua voi python de correctness MATCH.
bench.python_pure_pipeline.PIPELINE_REGISTRY = AnyFnRegistry(py_fn)
bench.rust_pure_pipeline.PIPELINE_REGISTRY = AnyFnRegistry(py_fn)
bench.rust_pure_pipeline.HAS_EXT = True

original_load = run_pipeline.load_config


def patched(*a, **k):
    cfg = original_load(*a, **k)
    cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
    cfg["target"]["mode"] = "repo"
    # Cac test nay kiem thu duong CHAY CU (run_once). Tu khi co PHA A..F,
    # target.mode=repo mac dinh di duong DONG (repo_pipeline.py), nen phai
    # tat repo_oracle de giu nguyen pham vi kiem thu cua chung.
    cfg.setdefault("repo_oracle", {})["enabled"] = False
    cfg["target"]["source"] = str(BENCH / "data" / "fake_dataset" / "repo_alpha")
    cfg["graph"]["top_k_hotspots"] = 1
    cfg["benchmark"].update({"iterations": 5, "warmup": 1})
    cfg["optimization_loop"]["max_rounds"] = 3
    cfg["compiler_loop"]["enabled"] = False
    cfg["decision_gate"]["enabled"] = False
    return cfg


run_pipeline.load_config = patched
code = run_pipeline.main()
assert code == 0

newest = max((BENCH / "results").glob("pipeline_summary_*.json"),
             key=lambda p: p.stat().st_mtime)
data = json.loads(newest.read_text(encoding="utf-8"))
st6 = data["stages"]["stage6"]
hs = st6["hotspots"][0]
rounds = [d for d in st6["decisions"] if d.get("round")]

print()
print(f"  so vong da chay      : {hs['rounds']}")
print(f"  build_status tung vong: {hs['build_status_by_round']}")
print(f"  so lan goi rebuild    : {len(rebuild_calls)}")
for d in rounds:
    sp = "n/a" if d["speedup"] is None else f"{d['speedup']:.3f}x"
    print(f"    vong {d['round']}: speedup={sp}")

speedups = [d["speedup"] for d in rounds if d["speedup"] is not None]
assert len(speedups) >= 2, f"can it nhat 2 vong co speedup, co {len(speedups)}"
assert len(rebuild_calls) >= 1, "chua he goi rebuild giua cac vong!"
assert any(e["build_status"] == "REBUILT_OK" for e in hs["build_status_by_round"]), \
    "khong vong nao co build_status=REBUILT_OK"
assert abs(speedups[0] - speedups[1]) > 1e-6, (
    f"speedup 2 vong GIONG HET NHAU ({speedups[0]} vs {speedups[1]}) -- "
    "van dang do lai ban build cu!"
)
print(f"\n  chenh lech speedup giua vong 1 va 2: "
      f"{abs(speedups[0] - speedups[1]):.3f} (khac nhau -> da do code moi)")
print("  OK: moi vong do DUNG code vua sinh")

print()
print("=" * 72)
print("TEST REBUILD + DO LAI: PASS")
print("=" * 72)
