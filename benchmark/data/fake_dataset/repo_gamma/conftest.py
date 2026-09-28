"""Cho phép `import mypkg` khi chạy pytest từ gốc repo.

Sự có mặt của file này cũng làm pytest chèn rootdir vào `sys.path` (import
mode mặc định `prepend`), nên repo chạy được mà không cần `pip install -e .`
-- giữ lượt kiểm thử trên máy dev nhẹ nhất có thể.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
