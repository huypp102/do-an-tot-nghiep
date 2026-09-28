"""Kiem chung: ghi de lib.rs de rebuild KHONG lam mat code Rust viet tay."""
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()
from stage5_compiler_in_the_loop.rebuild import (  # noqa: E402
    install_draft_into_crate,
    restore_original_lib,
)

CRATE = BENCH / "versions" / "rust_pure" / "pyo3_ext"
LIB = CRATE / "src" / "lib.rs"

original = LIB.read_text(encoding="utf-8")
print(f"lib.rs goc: {len(original)} ky tu, co '#[pymodule]': {'#[pymodule]' in original}")

draft = Path(r"D:\đồ án\.scratch\draft_test.rs")
draft.write_text("// DRAFT do LLM sinh\nfn main() {}\n", encoding="utf-8")

print("\n--- Ghi de draft vao lib.rs (mo phong rebuild) ---")
install_draft_into_crate(draft, CRATE)
after_overwrite = LIB.read_text(encoding="utf-8")
print(f"lib.rs sau ghi de: {len(after_overwrite)} ky tu")
assert "DRAFT do LLM sinh" in after_overwrite, "draft chua duoc nap"
assert (CRATE / "src" / "lib.rs.orig_backup").exists(), "KHONG co ban sao luu!"
print("  OK: da ghi de VA da tao ban sao luu")

print("\n--- Ghi de lan 2 (khong duoc de ban sao luu bi hong) ---")
draft.write_text("// DRAFT vong 2\nfn main() {}\n", encoding="utf-8")
install_draft_into_crate(draft, CRATE)
backup_text = (CRATE / "src" / "lib.rs.orig_backup").read_text(encoding="utf-8")
assert backup_text == original, "ban sao luu bi ghi de boi draft vong 1!"
print("  OK: ban sao luu van la ban GOC, khong bi draft vong 1 de len")

print("\n--- Khoi phuc ---")
restored = restore_original_lib(CRATE)
final = LIB.read_text(encoding="utf-8")
assert restored, "restore tra ve False"
assert final == original, "lib.rs KHONG khop ban goc sau khi khoi phuc!"
assert not (CRATE / "src" / "lib.rs.orig_backup").exists(), "ban sao luu chua duoc don"
print(f"  OK: lib.rs khoi phuc chinh xac ({len(final)} ky tu, khop 100% ban goc)")

draft.unlink(missing_ok=True)
print("\n" + "=" * 60)
print("TEST AN TOAN lib.rs: PASS")
print("=" * 60)
