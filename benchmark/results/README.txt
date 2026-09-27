Thư mục này chứa kết quả benchmark do stage6_benchmark/bench.py tự sinh ra:
  raw_<timestamp>.json     - số liệu thô (giây), từng iteration, từng hàm, từng version
  report_<timestamp>.md    - bảng mean/median/std/speedup dạng text

Xem lại report mới nhất mà không chạy benchmark lần nữa:
  python stage6_benchmark/report.py

Không cần commit các file *.json/*.md sinh ra ở đây (đã bị .gitignore bỏ
qua), trừ khi muốn lưu lại mốc số liệu cụ thể để đưa vào báo cáo đồ án.
