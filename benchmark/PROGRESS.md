# PROGRESS -- nhật ký ngắn, theo thời gian

Chỉ ghi các mốc CẦN TRUY NGƯỢC được (dataset dùng bản nào, quyết định nào chốt
lúc nào) -- không phải nhật ký công việc đầy đủ. Chi tiết từng phần xem
`selection/SELECTION_REPORT.md`, `AUDIT_REPORT.md`, và lịch sử commit.

## 2026-09-30 -- LẦN CHẠY 4: thiết kế lại cách chọn repo

- **Dataset**: xác nhận dùng ĐÚNG bản zip đã dùng ở pilot 1-3 (KHÔNG phải bản
  tải mới) -- `D:\data_repotransbench (1).zip`.
  - SHA256: `9bd76d90ef319c563a67dfb500ab7ec097ec011e4a0a0bd2401515666df994a1`
  - Kích thước: 58,898,394 byte (~56.2 MiB)
  - Giải nén vào `benchmark/data_repotransbench/` (gitignored). Xác nhận
    `source_projects/Python` có ĐÚNG 171 repo con -- khớp số đã dùng ở 3
    pilot trước, KHÔNG lệch.
- Quét tĩnh 171 repo, gắn nhãn miền (`ai_preprocessing`/`general`/`unknown`):
  21 ai_preprocessing / 150 general / 0 unknown. Xem
  `selection/domain_tags.csv`, `selection/domain_counts.json`.
- Đổi cơ chế chọn repo: hoán vị ngẫu nhiên có seed cố định (seed=42) thay cho
  sắp theo tên; cân bằng miền (>=5 ai_preprocessing hợp lệ thì lấy đúng 5 + 5
  general, ít hơn thì lấy hết rồi bù general); giới hạn CỨNG 10 repo + 2 dự
  phòng; screening_limit=50 (từ 25).
- Nâng `repo_time_budget_sec` 1800 -> 3000s, căn cứ 1 điểm đo thật
  (LanceGin_haishoku: 998s cho 3 nhánh ablation).
- Toàn bộ thay đổi đã test bằng `--dry-run` + `tests/test_pilot_scale.py`
  trên dataset thật (171 repo) tại máy dev Windows -- PASS. Phần "sàng thật"
  (chạy Pha A-B thật trên 50 repo) CHƯA chạy ở đây -- để dành cho máy Linux
  thuê (xem `selection/SELECTION_REPORT.md` mục rủi ro/kế hoạch).
- **Sửa lần 2 (cùng ngày)**: đổi cơ chế sàng từ "phẳng rồi cân bằng miền SAU"
  sang "sàng 2 giai đoạn theo miền" -- giai đoạn 1 sàng TOÀN BỘ 20 repo
  `ai_preprocessing` (đã loại `mgedmin_check-manifest`) trước, dừng khi đủ
  quota 5; giai đoạn 2 mới sàng `general`. Thêm
  `selection/domain_exclusions.txt` (danh sách loại tay, dễ sửa không cần
  đụng code) và `tests/test_ai_first_screening.py` (unit test cho
  `_screen_stream()`, dữ liệu giả, không cần dataset thật). Test PASS.
