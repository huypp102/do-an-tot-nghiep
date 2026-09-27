"""Stage 5 -- Compiler-in-the-loop.

Biên dịch code Rust do Stage 4 sinh ra, phân loại lỗi biên dịch, rồi gửi lại
GENERATOR AGENT để sửa (tối đa `compiler_loop.max_retries` vòng). Tính
Pass@1 và DSR@1.

PHÂN VAI RÕ RÀNG: vòng lặp sửa lỗi ở đây CHỈ dùng Generator Agent (vai trò
sinh/sửa code). Decision Agent KHÔNG tham gia bước này -- nó chỉ vào cuộc ở
Stage 6, sau khi code đã biên dịch được VÀ đã đo xong tốc độ, để quyết định
accept/reject kết quả.

Xem compiler_loop.py (biên dịch + phân loại lỗi) và loop_runner.py (vòng
lặp retry + số liệu).
"""
