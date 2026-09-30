# Chọn repo cho LẦN CHẠY 4 -- báo cáo ngắn

Ghi lại quy tắc, seed, và rủi ro thiên lệch của cách chọn 10 repo (+2 dự
phòng) cho lần chạy 4, để người đọc tự đánh giá được thay vì phải tin lời
kể. Mã nguồn: `run_experiment1.py` (hàm `select_repos`, `shuffled_candidates`,
`balance_by_domain`), `selection/scan_domains.py`.

## 1. Dataset (provenance)

- Bản zip GỐC đã dùng cho cả 3 pilot trước (KHÔNG phải bản tải mới):
  `D:\data_repotransbench (1).zip`.
- SHA256: `9bd76d90ef319c563a67dfb500ab7ec097ec011e4a0a0bd2401515666df994a1`
- Kích thước: 58,898,394 byte (~56.2 MiB)
- Giải nén vào `benchmark/data_repotransbench/` (nằm trong `.gitignore`, không
  commit dữ liệu dataset). Đã xác nhận `source_projects/Python` có **đúng 171
  repo con** -- khớp số đã dùng ở pilot 1-3.

## 2. Quét tĩnh + gắn nhãn miền

`selection/scan_domains.py` quét TOÀN BỘ 171 repo (không cài phụ thuộc, không
chạy test, chỉ `ast.parse`). Quy tắc gắn nhãn (chi tiết trong docstring của
file):

- `ai_preprocessing`: có ≥1 file `.py` import 1 trong 11 thư viện CV/NLP/
  tabular: `numpy, PIL, cv2, pandas, sklearn, nltk, spacy, torch,
  transformers, scipy, skimage`.
- `general`: có file `.py` phân tích được nhưng không khớp danh sách trên.
- `unknown`: không có file `.py` nào.

Kết quả trên dataset thật (`selection/domain_tags.csv`,
`selection/domain_counts.json`): **21 ai_preprocessing / 150 general / 0
unknown**.

Hai điểm đáng ngờ phát hiện khi kiểm bằng mắt kết quả gắn nhãn (chưa xử lý,
ghi lại để người đọc tự cân nhắc):

- `mgedmin_check-manifest` có 4047 file `.py` (bất thường so với trung vị 19
  file/repo) và được gắn `ai_preprocessing` (khớp `PIL;numpy`) -- nghi là do
  bộ test của nó tự sinh hàng loạt cây thư mục dự án giả, không phải repo
  CV/NLP thật. **Đã loại khỏi candidate pool** qua
  `selection/domain_exclusions.txt` (xem mục 3) -- không chỉ hạ xuống
  `general`, mà loại hẳn khỏi cả 2 dòng sàng.
- `BBuf_onnx_learn` KHÔNG được gắn `ai_preprocessing` dù tên gợi ý ONNX/deep
  learning -- file của nó chỉ import module nội bộ (`tools`, `onnxapi`,
  `convert2onnx`), không khớp danh sách 11 thư viện (không có `onnx`/
  `onnxruntime` trong danh sách theo đúng yêu cầu đề bài).

## 3. Cơ chế chọn mẫu

Thay đổi so với pilot 1-3 (sắp theo TÊN, thiên lệch alphabet -- 3 pilot trước
chỉ từng thấy các repo đầu bảng chữ cái). **Sửa lần 2 (cùng ngày 2026-09-30)**:
đổi từ "sàng phẳng rồi cân bằng miền SAU" sang "sàng 2 GIAI ĐOẠN theo miền",
vì cách cũ để quota 5 ai_preprocessing phụ thuộc may rủi (chỉ ~12% dataset
thuộc miền này, có thể không đủ vị trí trong 50 ứng viên đầu của hoán vị
phẳng).

1. **Hoán vị ngẫu nhiên có seed cố định**: `random.Random(42).shuffle()` trên
   TOÀN BỘ 171 repo, ghi `seed=42` vào `selection.json` để tái lập đúng thứ tự.
2. **Loại tay**: bỏ khỏi TOÀN BỘ candidate pool các repo trong
   `selection/domain_exclusions.txt` (hiện có `mgedmin_check-manifest` --
   xem mục 2). Thêm dòng mới vào file này khi rà tay phát hiện repo khác cần
   loại, không cần sửa code.
3. **Tách 2 dòng theo miền** (giữ nguyên thứ tự hoán vị bên trong mỗi dòng):
   dòng `ai_preprocessing` (20 repo, sau khi loại tay) và dòng `general`.
4. **Giai đoạn 1 -- AI-first**: sàng dòng `ai_preprocessing` theo thứ tự,
   DỪNG khi đủ 5 repo hợp lệ (quota tối đa) HOẶC hết dòng HOẶC chạm
   `screening_limit=50`. Không đủ 5 thì lấy hết số tìm được (đã sàng HẾT cả
   dòng, không phải do vị trí xui rủi).
5. **Giai đoạn 2 -- general**: sàng tiếp dòng `general` theo thứ tự, DỪNG khi
   TỔNG (2 giai đoạn) đủ 12 (10 + 2 dự phòng) HOẶC hết dòng HOẶC chạm
   `screening_limit`.
6. **Điều kiện nhận 1 repo** (giữ nguyên như 3 pilot trước, áp cho cả 2 giai
   đoạn): bộ test Python gốc chạy được (pass ≥1 test) VÀ ≥2 hotspot ghi +
   phát lại được đối số thật.
7. **10 repo chính** = 5 (hoặc ít hơn, nếu không đủ) đầu tiên tìm được ở giai
   đoạn 1 + phần còn lại lấy từ giai đoạn 2, theo đúng thứ tự tìm được. Phần
   hợp lệ còn dư làm 2 dự phòng, không phân biệt miền.

`selection.json` ghi `phases.ai_first` / `phases.general` (số ứng viên, số đã
sàng, số hợp lệ, lý do dừng) và trường `phase` trên từng mục trong `screened`
-- đọc lại được CHÍNH XÁC vì sao dừng ở đâu, không chỉ con số cuối cùng.

**Cỡ mẫu**: cần 10 + 2 = 12 repo hợp lệ. Với tỉ lệ đạt 24-30% đo được ở pilot
1 (dataset 171 repo, coi là đặc điểm dataset chứ không phải vị trí trong danh
sách, nên áp lại được cho mẫu ngẫu nhiên), ước lượng cần sàng ~41-50 ứng viên
→ `screening_limit=50`. Ước lượng gần nhất đo được (mốc 40 ứng viên, pilot 1):
12/40 → SÁT NGƯỠNG (cần 12 == ước lượng đạt 12), không có biên an toàn dư. Chi
tiết cảnh báo 3 mức: `run_experiment1.warn_if_screening_too_small()`.

## 4. Ngân sách thời gian mỗi repo

`repo_time_budget_sec`: 1800 → **3000s** (50 phút). Căn cứ DUY NHẤT đo được:
`LanceGin_haishoku` chạy đủ 3 nhánh ablation (`graph`/`none`/`graph2`) hết
998s (~55% ngân sách cũ). Repo đó nhỏ (25 file `.py`, ~65 percentile của
dataset: trung vị 19 file, p90=61, p95=100, max ngoài 1 outlier = 436). Chọn
hệ số an toàn ~3x trên điểm đo được làm TRẦN, không phải trung bình kỳ vọng --
vì bước tốn thời gian nhất (build Rust + gọi LLM) bị chặn trần bởi
`top_k_hotspots` cố định, không tăng thẳng theo kích thước repo, nên KHÔNG
suy tuyến tính theo p95/max. **Chỉ có 1 điểm đo thật** -- cần đối chiếu thêm
khi có số liệu từ lượt sàng 50 repo của lần chạy 4.

## 5. Rủi ro thiên lệch (BẮT BUỘC nêu khi dùng kết quả)

- **Thiên lệch "test tốt"**: điều kiện nhận đòi bộ test gốc chạy được VÀ ≥2
  hotspot phát lại được -- cả hai đều thiên về repo được bảo trì tốt, có bộ
  test đầy đủ, đối số đơn giản/tất định. Repo có logic phức tạp nhưng test
  yếu hoặc đối số khó ghi lại sẽ bị loại một cách có hệ thống, KHÔNG phải
  ngẫu nhiên -- mẫu cuối không đại diện cho "độ khó dịch" trung bình của
  dataset.
- **Hoán vị ngẫu nhiên chỉ phủ 1 phần dataset**: dòng `ai_preprocessing`
  (20 repo) được sàng TOÀN BỘ (sửa lần 2 -- xem mục 3), nên miền này không
  còn phụ thuộc vị trí trong hoán vị. Dòng `general` (150 repo) thì vẫn chỉ
  sàng tới khi đủ quota hoặc chạm phần `screening_limit` còn lại (~30 repo
  general, sau khi trừ phần đã dùng cho giai đoạn AI) -- phần lớn dataset
  `general` vẫn không có cơ hội được sàng ở lần chạy này.
- **Quota 5 ai_preprocessing có thể vẫn không đạt được, dù đã sàng HẾT dòng
  AI**: chỉ 20 repo (sau khi loại `mgedmin_check-manifest`) thuộc miền này.
  Với tỉ lệ đạt 24-30% đo được ở dataset nói chung, kỳ vọng chỉ **~5-6 repo
  ai_preprocessing hợp lệ trong toàn bộ 20** -- SÁT quota 5, không dư. Nếu tỉ
  lệ đạt thực tế của riêng miền `ai_preprocessing` thấp hơn mức trung bình
  dataset (chưa có số đo riêng), lần chạy 4 vẫn có thể rơi vào nhánh "lấy hết
  rồi bù general" -- nhưng khác với thiết kế cũ, đây sẽ là kết luận CÓ CĂN CỨ
  (đã thử hết 20 repo) chứ không phải do vị trí xui rủi trong 1 hoán vị
  phẳng.
- **7/171 repo** có ≥1 file `.py` không `ast.parse` được (cú pháp lạ, có thể
  Python 2) -- không chặn gì, chỉ làm giảm độ chính xác gắn nhãn miền cho
  đúng 7 repo đó (`selection/domain_tags.csv`, cột `n_unparseable`).
- **`mgedmin_check-manifest`**: đã loại khỏi candidate pool (xem mục 2, 3) --
  không còn rủi ro lọt vào mẫu, nhưng cần ghi trong luận văn LÝ DO loại (nhãn
  `ai_preprocessing` là dương tính giả) để người đọc hiểu vì sao 1 repo bị
  loại thủ công thay vì qua quy trình sàng tự động.

## 6. Trạng thái hiện tại

Đã chạy trên máy dev Windows với dataset thật (171 repo): `--dry-run` +
`tests/test_pilot_scale.py` -- PASS, xác nhận hoán vị đúng seed, cấu hình
đọc đúng, `selection.json` ghi đủ trường. **Phần sàng THẬT** (chạy Pha A-B
thật trên tối đa 50 ứng viên, cần cài đặt + chạy test của từng repo) CHƯA
chạy ở đây -- để dành cho máy Linux thuê (`python run_experiment1.py
--profile pilot_linux`), theo đúng phạm vi công việc đã giao.
