import sys
import logging
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()
from stage0_graph.builder import build_graph, discover_python_files  # noqa: E402
from stage3_context_packaging.packager import package_context_for  # noqa: E402
from stage4_llm_transpile.generator_agent import generate_rust_for, parse_response  # noqa: E402

FAKE_RESPONSE = """Toi da phan tich ham.

## Rust code
```rust
use pyo3::prelude::*;

#[pyfunction]
fn edge_det(image: Vec<u8>, width: usize, height: usize, _sig: f64) -> PyResult<Vec<u8>> {
    let mut out = vec![0u8; width * height];
    for i in 0..out.len() { out[i] = image[i]; }
    Ok(out)
}
```

## Optimization strategy
Gop 2 vong lap Gaussian X/Y thanh 1 luot duyet, bo cap phat trung gian.
"""


class FakeBackend:
    """Gia lap model tra loi dung template -> test parse + ghi file offline."""

    name = "fake"

    def generate(self, prompt, model=None):
        assert "edge_det" in prompt, "prompt phai chua ten hotspot"
        assert "gen_gauss1d_k" in prompt, "prompt phai chua context callee tu Stage 3"
        return FAKE_RESPONSE

    # GeneratorAgent goi qua AgentSession -> `chat()`, khong phai `generate()`.
    # Dung **kwargs de chu ky mo rong ve sau (model/num_ctx/think/...) khong
    # lam hong mock nay nua.
    def chat(self, messages, system=None, **kwargs):
        return self.generate(messages[-1]["content"] if messages else "")


parsed = parse_response(FAKE_RESPONSE)
print("parse_response -> rust_code chars:", len(parsed["rust_code"]))
print("parse_response -> strategy:", parsed["strategy"])

target = BENCH / "data" / "reference_repo"
graph = build_graph(discover_python_files(target), target)
ctx = package_context_for("edge_det", graph)
res = generate_rust_for("edge_det", ctx, FakeBackend())
print("\nKET QUA:", {k: v for k, v in res.items() if k != "raw_response"})
print("\n--- FILE SINH RA ---")
print(Path(res["output_path"]).read_text(encoding="utf-8"))

lib_rs = BENCH / "versions" / "rust_pure" / "pyo3_ext" / "src" / "lib.rs"
print("--- KIEM TRA lib.rs KHONG BI GHI DE ---")
print("lib.rs van con dong '#[pymodule]':",
      "#[pymodule]" in lib_rs.read_text(encoding="utf-8"))
