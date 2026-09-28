"""Repo GIẢ dùng layout `src/` -- kiểm chứng `module_resolve.resolve_module`
tìm đúng gốc import (`<repo>/src`, không phải `<repo>`).

`src/` cố ý KHÔNG có `__init__.py`, nên vòng lặp đi ngược lên trong
`resolve_module` dừng đúng chỗ và module ra `epsipkg.textops`, không phải
`src.epsipkg.textops` (tên sau không import được).
"""
