"""Stage 2 -- Decision Gate (pre-filter trước khi dịch).

Phân loại từng hàm hotspot (từ FuncRank của Stage 0/1) thành "skip",
"suggest_numpy_vectorization" hoặc "candidate", để chỉ những hàm thật sự
đáng dịch sang Rust mới đi tiếp qua Stage 3/4.

LƯU Ý: khác hoàn toàn với Decision Agent ở Stage 6 (accept/reject SAU khi đã
dịch và đo được tốc độ) -- xem gate.py.
"""
