# Lần chạy 4 -- chẩn đoán: thủ tục chạy (mục F)

Mục tiêu KHÔNG phải số đẹp -- biết pipeline sai/thiếu ở khâu nào và quy tắc
nào quá chặt/quá lỏng. Chạy trên máy Linux thuê (cần Ollama + cargo +
maturin -- không chạy được trên máy dev Windows, xem ghi chú cuối file).

## 1. Nhánh ablation

`profiles/pilot_linux.yaml` đã đúng: `ablation.arms: [graph, none]` +
`ablation.noise_floor: true` -> `ablation.arms(cfg)` tự thêm `graph2`, hiệu
lực = **`[graph, none, graph2]`** (đã xác nhận bằng `python -c` trên máy
dev, xem lịch sử commit "Chan doan lan 4 - E"). KHÔNG cần sửa gì thêm.

**Nhánh oracle: CHƯA làm, hoãn lại.** Không có khái niệm "oracle" nào có sẵn
trong code (đã rà toàn bộ repo) -- `repo_oracle` là một khái niệm KHÁC (chạy
bộ test gốc của repo làm chuẩn, không phải bản dịch Rust tham chiếu). Xây 1
nhánh oracle thật (cần bản Rust THAM CHIẾU cho từng hotspot để so sánh) là
một hạng mục lớn, riêng -- không làm vội trong lượt chuẩn bị này ("nếu kịp"
trong yêu cầu gốc). Cần định nghĩa rõ "oracle" nghĩa là gì trước khi bắt
tay: Rust viết tay tham chiếu? Hay 1 LLM khác làm trọng tài? Việc này để
ngỏ cho phiên sau.

## 2. Thứ tự chạy BẮT BUỘC

1. **Chạy thử 1 repo nhỏ trước** để chắc chắn mọi phép đo (mục A-E) hoạt
   động đúng trước khi tốn giờ GPU cho cả 10 repo:

   ```bash
   python run_experiment1.py --profile pilot_linux --run-id t_trial_bbuf \
       --skip-preflight   # BỎ cờ này nếu preflight đã qua trên máy thuê
   ```

   Repo dùng để thử: **`BBuf_onnx_learn`** (đã dùng làm ví dụ xuyên suốt
   `AUDIT_REPORT.md`, có sẵn trong dataset RepoTransBench, nhiều hàm test bị
   lọc nên là ca kiểm tốt cho mục A2/B6). Để CHỈ chạy đúng repo này, sửa tạm
   `experiment.screening_limit` xuống 1 VÀ đặt `experiment.sample_seed` sao
   cho `BBuf_onnx_learn` rơi vào vị trí đầu -- hoặc đơn giản hơn: gọi thẳng
   `repo_pipeline.run_repo_pipeline()` cho 1 mình repo đó (bỏ qua bước sàng),
   xem ví dụ gọi trong `tests/test_graph_export_regression.py`.

2. Sau khi chạy thử xong, **kiểm tra ngay** (đừng đợi hết cả 10 repo mới
   xem):
   - `results/<run>/graphs/BBuf_onnx_learn/pcg.json` tồn tại,
     `graph_available: true`, `overlay_applied: true` (mục D).
   - Ít nhất 1 hotspot có `compiled_at_round` khác null (mục A1) nếu có hàm
     biên dịch được.
   - Chạy `python audit/funnel_report.py --results-dir results/<run>` ra
     được báo cáo không lỗi (mục E) -- đây là cách rẻ nhất để phát hiện 1
     trường JSON bị thiếu/sai kiểu TRƯỚC khi tốn thời gian cho 10 repo.

3. **Chỉ sau khi bước 2 sạch**, chạy đủ 10 repo (bỏ `--run-id`
   `t_trial_bbuf`, dùng lệnh thường):

   ```bash
   python run_experiment1.py --profile pilot_linux
   ```

4. Cuối lượt chạy thật, chạy lại `audit/funnel_report.py` trên CẢ thư mục
   run (gồm cả 10 repo) để có báo cáo tổng hợp cuối cùng -- đây là file DUY
   NHẤT cần đọc, không đọc `repo_summary_*.json` thô.

## 3. Không chạy được trên máy dev Windows

Máy dev (Windows, không có `cargo`/`maturin`) chỉ kiểm chứng được LOGIC
(dry-run + dữ liệu giả, xem `tests/test_graph_export_regression.py`,
`tests/test_ai_first_screening.py`, và các test đơn vị B1-B5 nhúng trong
lịch sử commit "Chan doan lan 4"). Toàn bộ mục A-E đã test bằng dữ liệu giả
trên Windows theo đúng yêu cầu ("Test bằng dry-run và dữ liệu giả trên
Windows"), nhưng CHƯA chạy được với LLM/cargo thật -- mục 1-4 ở trên là thứ
CẦN làm trên máy Linux thuê trước khi tin số liệu.
