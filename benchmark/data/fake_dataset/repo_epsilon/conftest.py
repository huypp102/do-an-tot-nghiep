"""Layout `src/`: phải tự thêm `src/` vào sys.path (đó chính là gốc import mà
`module_resolve.resolve_module` phải tìm ra)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
