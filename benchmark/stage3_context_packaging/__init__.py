"""Stage 3 -- Đóng gói context cho từng hotspot.

Bước TRUNG GIAN giữa stage0_graph/context_export.py (xuất TOÀN BỘ graph ra
JSON) và stage4_llm_transpile/generator_agent.py (chỉ cần context của ĐÚNG 1
hotspot): lấy neighbor node/edge (Nnode(u)/Nedge(u) theo POLO Section 3.3)
của 1 hàm cụ thể từ PCG + PSG, kèm dữ liệu profiling Stage 1.

Xem packager.py.
"""
