"""Kiem chung fix _force_rmtree: clone -> force_refresh (xoa ban cu co file
read-only) -> clone lai. Truoc khi fix, buoc force_refresh se that bai am
tham roi git clone bao 'directory not empty'."""
import logging
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()
from input.intake import IntakeError, resolve_target  # noqa: E402

URL = "https://github.com/viraj7/Computer-Vision-Image-processing"
CLONED = BENCH / "data" / "cloned_repos"

print("--- B1: clone lan dau ---")
p1 = resolve_target(URL)
n1 = sum(1 for _ in p1.rglob("*") if _.is_file())
print(f"OK: {p1.name}, {n1} file")

print("\n--- B2: force_refresh=True (xoa ban cu CO file read-only roi clone lai) ---")
p2 = resolve_target(URL, force_refresh=True)
n2 = sum(1 for _ in p2.rglob("*") if _.is_file())
print(f"OK: {p2.name}, {n2} file  -> fix _force_rmtree HOAT DONG")

print("\n--- B3: URL sai (phai bao loi sach, khong de lai rac) ---")
try:
    resolve_target("https://github.com/khong-ton-tai-abcxyz/repo-khong-co")
    print("!!! LE RA PHAI LOI")
except IntakeError as exc:
    print(f"OK, bat duoc IntakeError: {str(exc)[:90]}...")
    leftover = list(CLONED.glob("repo-khong-co"))
    print(f"Thu muc clone do con sot lai? {leftover if leftover else 'KHONG (da don sach)'}")

print("\n--- B4: don sach de ban giao ---")
from input.intake import _force_rmtree  # noqa: E402

_force_rmtree(CLONED)
print(f"cloned_repos con ton tai? {CLONED.exists()}")
