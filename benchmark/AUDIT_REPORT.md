# AUDIT — Output của pipeline có đủ để so sánh CORRECTNESS và EFFICIENCY chưa?

**Phạm vi:** chỉ đọc code + chạy thử với mock/dataset giả. **Không sửa file mã nguồn nào.**
**Ngày audit:** 2026-09-28
**Cặp đánh giá:** Python → Rust, dataset RepoTransBench, 3 phiên bản `python_pure` / `rust_pure` / `hybrid_pyo3`.
**Máy audit:** Windows dev, KHÔNG có cargo/maturin/GPU/LLM thật (xem mục (d)).

**Lượt chạy thử đã thực hiện (2 lượt, trên `data/fake_dataset/` gồm `repo_alpha`, `repo_beta`):**

| Lượt | Cấu hình | Mục đích |
|---|---|---|
| A | `llm.enabled=true`, `num_agents=2`, backend giả, `rebuild_from_draft` giả trả `REBUILT_OK`, `measure_in_subprocess` giả trả số KHÁC nhau mỗi vòng, `max_rounds=3`, gate BẬT | xem output khi mọi thứ "chạy được" |
| B | `llm.enabled=false`, **không mock `PIPELINE_REGISTRY`** | xem output khi repo dataset có hàm không nằm trong registry — tức đúng tình huống RepoTransBench thật |

File kết quả đã mở và đối chiếu: `results/pipeline_raw_*_repo_alpha.json`, `results/pipeline_summary_*_repo_alpha.json`, `results/pipeline_summary_*_repo_beta.json`, `results/dataset_summary_*.json`.

> **Ghi chú về `summary.json`:** file này KHÔNG tồn tại. Pipeline chỉ sinh `pipeline_raw_<ts>[_<repo>].json`, `pipeline_summary_<ts>[_<repo>].json`, `dataset_summary_<ts>.json`, `graph_context_<ts>.json`. `report_*.md` và `whole_scope_*.json` **chỉ do `stage6_benchmark/bench.py` sinh**, `run_pipeline.py` không sinh (xem lỗ hổng #8).

---

## (a) Bảng checklist

### PHẦN 1 — CORRECTNESS

| Mục | Kết luận | file:key trong output | Ghi chú |
|---|---|---|---|
| **1.1** `correctness_match` per-hotspot | **MỘT PHẦN** | `pipeline_summary_*.json` → `stages.correctness.<fn>.{status,detail,n_matched,n_samples}` | Có đủ MATCH/MISMATCH/ERROR, có ghi ra file. Code: `stage5_compiler_in_the_loop/correctness.py:run_correctness_checks`, gọi từ `run_pipeline.py:314`. |
| 1.1a — tách riêng `rust_pure` vs `hybrid_pyo3` | **KHÔNG** | — | `run_correctness_checks()` chỉ nhận `python_registry` + `rust_registry`. `hybrid_pyo3` **chưa từng được kiểm tra đúng đắn**, dù vẫn được đo tốc độ và báo speedup. |
| 1.1b — số lượng input so sánh | **CÓ** | `stages.correctness.<fn>.n_samples` (= 3) | `build_sample_inputs()` tạo 3 sample: ảnh thật, crop 32×32, ảnh hằng 16×16. |
| 1.1c — shape của input so sánh | **MỘT PHẦN** | chỉ nằm trong chuỗi `detail` khi MISMATCH | Khi MATCH, `detail` = `"khớp trên toàn bộ 3 sample"` — **không có shape nào**. Không có field shape riêng. |
| 1.1d — sai số rtol/atol | **KHÔNG** | — | Đọc từ `config.correctness.{rtol,atol}` rồi truyền vào hàm, **không ghi lại vào file kết quả**. Đọc file kết quả xong không biết đã so với ngưỡng nào. |
| 1.1e — chi tiết khi MISMATCH | **MỘT PHẦN** | `stages.correctness.<fn>.detail` | Dataclass `CorrectnessResult.mismatches` giữ **tất cả** sample lệch, nhưng khi ghi JSON chỉ lấy `detail` = lệch **đầu tiên**. Mất thông tin các sample còn lại. |
| **1.2** Trạng thái biên dịch per-hotspot | **CÓ** | `stages.stage5.outcomes[]` = `{function_name, compiled, passed_first_try, attempts, fix_rounds, error_classes[], skipped, skip_reason, final_error}`; `stages.stage5.metrics` = `{n_total, n_evaluated, n_skipped, n_passed_first_try, n_compiled_eventually, pass_at_1, dsr_at_1}` | Đầy đủ nhất trong toàn bộ audit: Pass@1, DSR@1, số vòng sửa, loại lỗi (`_ERROR_CODE_GROUPS` trong `compiler_loop.py:39`). Cũng lên bảng chính qua cột `pass@1`. Trên máy audit không có cargo nên `skipped=true`. |
| **1.3** Correctness mức REPO (chạy `run_tests.sh`/pytest) | **KHÔNG — LỖ HỔNG CHÍNH** | — | Grep toàn repo: `pytest`, `run_tests`, `coverage`, `test_summary` **không xuất hiện trong bất kỳ file .py nào** (chỉ khớp `.pytest_cache` trong danh sách bỏ qua của `stage0_graph/builder.py:40`). Không có bước nào chạy bộ test Python gốc cho baseline (a) hay cho bản hybrid (b). |
| **1.4** APR / SR / timeout / BASELINE_FAILED | **KHÔNG** (hệ quả của 1.3) | — | Không có parse số test pass/fail, không có APR, không có SR, không có timeout per-repo, không có nhãn `BASELINE_FAILED`. Tầng dataset (`run_pipeline.py:main`) chỉ bắt `Exception` cho mỗi repo, không có giới hạn thời gian. |
| **1.5** Có vô tình dùng `target_projects/Python/Rust` làm oracle? | **KHÔNG — ĐÚNG** | — | Grep toàn bộ `.py/.yaml/.yml/.md/.env*`: chuỗi `target_projects` **không xuất hiện ở đâu cả**. Chỉ có `source_projects` trong `REPOTRANSBENCH_ROOT` (config.yaml:131, docker-compose.yml:54, .env.example:15, intake.py:230). Quy tắc "Stage 0-4 không đọc target_projects" đang được giữ đúng. |
| **1.6** 4 repo không có test Rust (`macbre_sql-metadata`, `piskvorky_sqlitedict`, `quora_qcore`, `spulec_freezegun`) | **KHÔNG ÁP DỤNG — không crash, không bỏ qua âm thầm** | — | Vì không có bước nào đọc `target_projects`, việc thiếu bộ test Rust **không ảnh hưởng gì** tới pipeline hiện tại. `resolve_dataset_repos()` chỉ liệt kê thư mục con của `source_projects/Python`, không cần đối chiếu target. Cũng vì vậy **không có** khái niệm `NO_GROUND_TRUTH` — hiện chưa cần, nhưng sẽ cần ngay khi bổ sung 1.3. |

### PHẦN 2 — EFFICIENCY

| Mục | Kết luận | file:key trong output | Ghi chú |
|---|---|---|---|
| **2.1a** mean / median / std / n | **MỘT PHẦN — "chỉ có trong log"** | `pipeline_raw_*.json` chứa **danh sách durations thô** `{version: {fn: [float,...]}}`; mean/median/std **chỉ được in ra stdout** | `report.summarize()` tính đủ 4 số và `build_report()` in bảng đẹp, nhưng `run_pipeline.py:320` chỉ `print(table)` — **không ghi ra file**. Muốn có số phải tự tính lại từ raw. `n` suy ra được từ `len(durations)`. |
| **2.1b** số lần warmup / iterations | **KHÔNG** | — | `config.benchmark.{warmup,iterations}` không được ghi vào bất kỳ file kết quả nào. |
| **2.1c** cùng một input cho cả 3 phiên bản | **CÓ** | — | `run_pipeline.py:312-316` truyền **cùng một object `image`** cho `_bench_python_pure`, `_bench_hybrid`, `_bench_rust_pure`, cùng `warmup`/`iterations`. Đúng thiết kế. |
| **2.1d** đo in-process bằng `perf_counter` (hướng A) | **CÓ** | — | `bench._time_callable()`: warmup N lần rồi `time.perf_counter()` quanh `fn(image)`, cả 3 phiên bản đi qua đúng hàm đó. |
| **2.1e** speedup so với `python_pure` | **MỘT PHẦN** | `stages.stage6.hotspots[].speedup`, `stages.stage6.decisions[].speedup` | Chỉ có speedup của **`rust_pure`**. `hybrid_pyo3` có speedup trong bảng stdout nhưng **không có trong file kết quả nào** và không có trong bảng tổng kết hotspot. |
| **2.2** Kích thước input (shape/dtype/số phần tử) | **KHÔNG** | — | `run_pipeline.py:main` chỉ `logger.info("Ảnh input: shape=%s dtype=%s")`. Không field nào trong `pipeline_summary_*.json` hay `pipeline_raw_*.json` ghi shape/dtype. Không diễn giải được speedup (chi phí gọi cố định của PyO3 trên dữ liệu nhỏ). |
| **2.3a** speedup riêng từng vòng | **CÓ** | `stages.stage6.decisions[]` — mỗi phần tử có `round` + `speedup` | Lượt A cho `histogram`: vòng 1 = `1.02x`, vòng 2 = `0.03x`, vòng 3 = `0.04x`. |
| **2.3b** `build_status` từng vòng | **CÓ** | `stages.stage6.hotspots[].build_status_by_round[] = [{round, build_status}]`, `.last_build_status`, và `decisions[].build_status_next_round` | 4 trạng thái đúng như yêu cầu: `INITIAL` / `REBUILT_OK` / `BUILD_FAILED` / `SKIPPED_NO_CARGO` (`rebuild.py:36-38`). Có bảng riêng `build_round_table()` in ra stdout. |
| **2.3c** số đo giữa các vòng khác nhau khi code khác nhau | **CÓ** | như trên | Xác nhận trong lượt A: 3 vòng ra 3 con số khác nhau, không lặp lại số cũ. Cơ chế đúng: `measure_in_subprocess()` chạy tiến trình MỚI sau `maturin develop`, vì extension native không reload được in-process. |
| **2.4** Đo tốc độ có chồng với LLM inference / repo khác? | **CÓ — đảm bảo tuần tự** | — | Grep toàn repo: **không có** `Thread`, `multiprocessing`, `concurrent.futures`, `asyncio`, `Pool(`. Toàn bộ là 1 tiến trình tuần tự. Vòng dataset (`run_pipeline.py:main`) xử lý repo lần lượt. Trong mỗi vòng tối ưu, thứ tự là: `decide_after_benchmark` (gọi LLM, **đồng bộ**) → `optimize_further` (gọi LLM, đồng bộ) → `rebuild_from_draft` → `measure_in_subprocess` (cha `subprocess.run` chờ xong). **Không có lúc nào phép đo chạy song song với inference.** Lưu ý còn lại: xem lỗ hổng #15 (không pin CPU, không ghi nhận tải máy). |
| **2.5** Số đo mức repo (tổng thời gian baseline vs hybrid) | **KHÔNG** | — | `report.build_whole_scope_report()` + `whole_scope_*.json` có tồn tại, nhưng **chỉ `bench.py` gọi**; `run_pipeline.py` không gọi. Và bản thân nó chỉ là "gọi chuỗi hàm top-K **1 lần**", không phải workload repo. Không có đo thời gian chạy test repo. |
| **2.6** Metadata tái lập | **KHÔNG — hoàn toàn không có** | — | Grep toàn repo: **không có** `platform`, `cpu_count`, `processor`, `uname`, `socket`, `git rev-parse`, `sys.version`, `__version__`. Không có CPU model, số core, phiên bản Python/Rust/maturin, git commit, hostname. Tên 2 model LLM và `num_ctx` **chỉ có trong log** (`run_pipeline.py:192`, `:356`), không vào file. Chỉ có `timestamp` (`pipeline_summary_*.json:timestamp`). |

---

## (b) Danh sách lỗ hổng theo mức ảnh hưởng tới tính hợp lệ của kết quả

### MỨC CAO — kết quả hiện tại chưa dùng được cho luận văn

**#1. `PIPELINE_REGISTRY` hard-code 4 hàm ảnh của viraj7 → không đo được bất kỳ repo RepoTransBench nào. Pipeline vẫn báo thành công.**

Đây là lỗ hổng nghiêm trọng nhất và tôi đã chứng minh bằng lượt chạy B (không mock registry):

```
Correctness [normalize]: ERROR -- không có implementation Python để làm chuẩn so sánh
python_pure: chưa có implementation cho hàm 'normalize' -- bỏ qua riêng hàm này
hybrid_pyo3: chưa có implementation cho hàm 'normalize' -- bỏ qua riêng hàm này

hotspot                 correctness   pass@1   speedup   rounds
normalize                     ERROR      n/a       n/a        1
scale_pixels                  ERROR      n/a       n/a        1
...
=== TỔNG KẾT DATASET ===
Số repo chạy được: 2/2        <-- BÁO OK
### EXIT CODE = 0
```

`versions/python_pure/pipeline.py:98` và `versions/hybrid_pyo3/pipeline.py:92` chỉ có 4 khoá cố định (`edge_det`, `harris`, `hess_corner_det`, `im_threshold`). Stage 0 phát hiện hotspot theo tên hàm thật của repo (`normalize`, `scale_pixels`, …) nên `registry.get(name)` luôn trả `None` → `_bench_version()` bỏ qua từng hàm, correctness trả ERROR, speedup `n/a`. Nhưng `summary["ok"] = True` vẫn được đặt và exit code = 0, `dataset_summary` báo `n_ok = 2/2`. Chạy cả dataset RepoTransBench trên máy thuê sẽ ra một bảng toàn `ERROR` / `n/a` mà pipeline vẫn tự nhận là thành công.

Thêm một tầng nữa: workload luôn là **một ảnh** và mọi hàm được gọi bằng `fn(image)`. Các repo RepoTransBench (`sqlitedict`, `freezegun`, `qcore`, `sql-metadata`) không có hàm nào nhận ảnh — kể cả khi registry được nạp động thì chữ ký tham số cũng không khớp.

> *Đề xuất:* nạp registry **động** từ chính repo đang xử lý (import module theo `graph.functions[].file`) và sinh input theo chữ ký/type-hint của từng hàm thay vì luôn truyền `image`; đồng thời đặt `summary["ok"] = False` khi không đo được hotspot nào để exit code phản ánh đúng sự thật.

**#2. Không có correctness mức repo — không có APR/SR (mục 1.3, 1.4).**

Oracle duy nhất hiện tại là so output hàm-với-hàm trên 3 sample ảnh. Với hybrid (chỉ vài hàm là Rust), oracle đúng phải là **bộ test Python gốc của repo** như bối cảnh đã nêu. Không có bước nào chạy `run_tests.sh`/`pytest`, nên không có bằng chứng "thay hotspot bằng Rust mà repo vẫn chạy đúng" — vốn là luận điểm trung tâm của hướng hybrid.

> *Đề xuất:* thêm một stage chạy `pytest` trong repo (a) trước khi thay và (b) sau khi thay, parse `passed/failed` từ output, tính APR = trung bình tỉ lệ pass và SR = tỉ lệ repo pass toàn bộ, kèm timeout mỗi repo và nhãn `BASELINE_FAILED` để loại repo mà baseline Python tự fail.

**#3. Baseline `python_pure` không được đo lại ở vòng ≥ 2 → speedup của vòng 2, 3 là so sánh hai cách đo khác nhau.**

`py_results` được gán **một lần** ở `run_pipeline.py:325` và không bao giờ cập nhật, trong khi từ vòng 2 `rs_results[name]` được thay bằng kết quả `measure_in_subprocess()`. Nên:

- vòng 1: `mean(python in-process) / mean(rust in-process)` — hợp lệ;
- vòng ≥ 2: `mean(python **in-process**) / mean(rust **subprocess**)` — khác tiến trình, khác trạng thái interpreter/cache.

Speedup các vòng sau không so sánh được với vòng 1, cũng không so sánh được với nhau nếu chỉ một bên đổi cách đo.

> *Đề xuất:* đo lại **cả `python_pure`** trong cùng subprocess ở mỗi vòng (hoặc ghi rõ `measure_method` per-round vào output để người đọc không so lẫn).

**#4. `speedup` trong bảng tổng kết chính là MAX qua các vòng — có thể không thuộc code cuối cùng.**

`run_pipeline.py:436` lấy `best_speedup = max(...)`. Trong lượt A, `histogram` báo `1.02x` — đó là **vòng 1, `build_status=INITIAL`**, tức extension viết tay có sẵn, **không phải** code do LLM sinh; hai vòng sau (`REBUILT_OK`, code thật của LLM) chỉ đạt `0.03x`/`0.04x`. Bảng chính không có cột nào cho biết con số đó thuộc vòng nào hay build nào.

> *Đề xuất:* thêm `best_round` + `best_build_status` vào `hotspots[]`, và báo cả `final_speedup` (vòng cuối) cạnh `best_speedup`.

**#5. `hybrid_pyo3` không bao giờ được kiểm tra correctness.**

`run_correctness_checks()` chỉ nhận `python_registry` và `rust_registry` (`correctness.py:run_correctness_checks`). Phiên bản `hybrid_pyo3` vẫn được đo tốc độ và vẫn có speedup trong bảng stdout, nhưng chưa từng được chứng minh cho ra kết quả đúng. Với đề tài mà hybrid là hướng chính, đây là thiếu sót về tính hợp lệ, không chỉ về báo cáo.

> *Đề xuất:* gọi `check_function()` thêm một lượt với `hybrid_registry`, ghi `correctness_match` riêng cho từng phiên bản (`rust_pure`, `hybrid_pyo3`), và chặn đo tốc độ độc lập theo từng phiên bản.

### MỨC TRUNG — kết quả có thể đúng nhưng không kiểm chứng/diễn giải/tái lập được

**#6. Không có metadata tái lập nào trong output (mục 2.6).** Không có CPU model, số core, phiên bản Python/Rust/maturin, git commit của code, hostname, tên 2 model LLM, `num_ctx`. Số đo trên máy thuê sẽ không gắn được với môi trường sinh ra nó — phản biện "đo trên máy nào, code commit nào" không trả lời được.
> *Đề xuất:* ghi một block `environment` vào đầu `pipeline_summary_*.json`: `platform.processor()`, `os.cpu_count()`, `sys.version`, `rustc --version`, `maturin --version`, `git rev-parse HEAD`, `socket.gethostname()`, `generator_model`/`decision_model`/`num_ctx`.

**#7. Không có shape/dtype input trong output (mục 2.2).** Không diễn giải được speedup: dữ liệu nhỏ thì Rust qua PyO3 có thể chậm hơn Python vì chi phí gọi cố định — và các số trong lượt A (~15 µs/lần) đúng là vùng đó.
> *Đề xuất:* thêm `input_spec: {shape, dtype, n_elements, source_file}` vào summary.

**#8. mean/median/std chỉ có trong stdout; `run_pipeline.py` không ghi `report_*.md`.** `bench.py` ghi `report_*.md` + `whole_scope_*.json`, `run_pipeline.py` thì không — chỉ `print()`. Mất log terminal là mất bảng. Cũng không ghi `warmup`/`iterations`.
> *Đề xuất:* trong `run_pipeline.py`, ghi `report_<ts>[_<repo>].md` như `bench.py` đã làm, và thêm `bench_params: {warmup, iterations}` vào summary.

**#9. Stage 6 đo và gọi Decision Agent cho CẢ hotspot mà Decision Gate đã gạt.** Vòng lặp là `for name in functions:` (`run_pipeline.py:394`), không phải `for name in candidates:`. Lượt A: `normalize` bị gate gán `suggest_numpy_vectorization` nên Stage 3/4 không tạo Generator Agent cho nó, **nhưng Stage 6 vẫn đo, vẫn gọi Decision Agent, vẫn báo `1.07x` và `ACCEPT`** rồi mới dừng vì "không có Generator Agent". Tốn lời gọi LLM và làm bảng chính lẫn lộn hàm đã dịch với hàm chưa dịch.
> *Đề xuất:* thêm cột `gate_label` vào `hotspots[]` và bỏ qua vòng Decision Agent cho hotspot không phải `candidate`.

**#10. `correctness = ERROR` không chặn vòng tối ưu, chỉ `MISMATCH` chặn.** Điều kiện ở `run_pipeline.py:401` là `if corr_status == CORRECTNESS_MISMATCH`. Hotspot ERROR vẫn vào vòng, vẫn gọi Decision Agent với `before=None, after=None`. Trong lượt B, cả 4 hotspot đều ERROR mà vẫn có `rounds=1` và một quyết định REJECT.
> *Đề xuất:* chặn cả `ERROR` (không có gì để so thì cũng không có gì để tối ưu), và ghi `skipped_reason` vào `hotspots[]`.

**#11. `rtol`/`atol` và shape các sample không vào file; chi tiết MISMATCH bị cắt còn 1 dòng.** Xem 1.1c/1.1d/1.1e.
> *Đề xuất:* ghi `tolerance: {rtol, atol}` và `sample_shapes: [...]` vào `stages.correctness`, và ghi cả mảng `mismatches` thay vì chỉ `detail`.

**#12. Không có số đo mức repo (mục 2.5).** Cần ghi rõ giới hạn này trong luận văn: bộ test RepoTransBench đo **tính đúng**, không phải workload hiệu năng — nên thời gian chạy test chỉ là chỉ số tham khảo, **không thay thế** benchmark hàm.
> *Đề xuất:* nếu muốn có số mức repo, gọi `_bench_whole_scope()` từ `run_pipeline.py` (đã có sẵn trong `bench.py`) và trình bày nó như chỉ số phụ, ghi rõ là 1 lần chạy không warmup.

**#13. Không có timeout per-repo ở tầng dataset.** Có timeout cho `cargo` (`cargo_timeout_sec`), cho `maturin` (`build_timeout_sec`) và cho subprocess đo (`DEFAULT_TIMEOUT_SEC=300`), nhưng **không có** giới hạn cho cả một repo. Một repo treo ở Stage 0 (`build_graph` trên repo lớn) hoặc treo ở lời gọi LLM sẽ giữ cả dataset.
> *Đề xuất:* bọc `run_once()` bằng một timeout mức repo và đánh dấu `TIMEOUT` trong `dataset_summary`.

### MỨC THẤP

**#14. `report.regenerate_latest()` không bao giờ tìm thấy file của `run_pipeline.py`.** Nó glob `raw_*.json` (`report.py:regenerate_latest`) trong khi `run_pipeline.py` ghi `pipeline_raw_*.json` — tên không khớp pattern. Công cụ in lại báo cáo chỉ dùng được cho output của `bench.py`.
> *Đề xuất:* đổi pattern thành `*raw_*.json`.

**#15. Không pin CPU, không ghi nhận tải máy lúc đo.** Mục 2.4 đạt ở mức "không có song song trong code", nhưng trên máy thuê vẫn còn: container Ollama giữ 2 model resident (`OLLAMA_KEEP_ALIVE=30m`, `OLLAMA_MAX_LOADED_MODELS=2`) — không tốn CPU khi idle, nhưng cũng không có bước nào xác nhận server đã idle trước khi đo, và không có `taskset`/affinity.
> *Đề xuất:* ghi `os.getloadavg()` (Linux) trước/sau mỗi lượt đo vào output, để hậu kiểm được lượt nào đo trong lúc máy bận.

**#16. `hotspots[].pass_at_1 = null` không phân biệt được "Stage 5 bị tắt" với "Stage 5 bỏ qua vì thiếu cargo".** Cả hai đều ra `n/a` trên bảng. Lý do có trong `stages.stage5.reason`/`skip_reason` nhưng bảng chính không phản ánh.

---

## (c) Đề xuất schema bảng kết quả cuối cho luận văn

Một dòng một repo. Các cột in **đậm** là cột hiện tại **chưa có dữ liệu** trong output, cần bổ sung theo mục (b).

| Cột | Nguồn dữ liệu (hiện có / cần thêm) |
|---|---|
| `repo` | `pipeline_summary_*.json:label` ✔ |
| `n_hotspot` | `len(stages.stage0_1.functions)` ✔ — nên tách thêm `n_candidate` từ `stages.stage2.candidates` |
| `compile_ok` | `stages.stage5.metrics.n_compiled_eventually` / `n_evaluated` ✔ (kèm `pass_at_1`, `dsr_at_1` ✔) |
| `correctness_match_rate` | đếm `status=="MATCH"` trong `stages.correctness` ✔ — **nhưng phải tách riêng `rust_pure` và `hybrid_pyo3`** (lỗ hổng #5) |
| **`baseline_tests` (pass/total)** | **chưa có — lỗ hổng #2** |
| **`hybrid_tests` (pass/total)** | **chưa có — lỗ hổng #2** |
| `speedup_rust_pure` | `stages.stage6.hotspots[].speedup` ✔ — nên đổi sang trung bình/median qua hotspot, và ghi rõ là `best` hay `final` (lỗ hổng #4) |
| **`speedup_hybrid`** | **chưa có trong file — chỉ có trong bảng stdout (mục 2.1e)** |
| `n_rounds` | `stages.stage6.hotspots[].rounds` ✔ (kèm `stopped_by_cap` ✔) |
| `status` | cần định nghĩa mới: `OK` / `BASELINE_FAILED` / `NO_IMPL` / `NO_GROUND_TRUTH` / `TIMEOUT` / `ERROR` — hiện chỉ có `ok: true/false` ✔ và trường `error` |

Bảng đề xuất:

```
repo | n_hotspot | n_candidate | compile_ok | pass@1 | corr_rate(rust) | corr_rate(hybrid) |
baseline_tests | hybrid_tests | APR | speedup_rust | speedup_hybrid | rounds | status
```

Kèm một khối `environment` dùng chung cho cả bảng (CPU, core, Python/Rust/maturin, git commit, 2 model LLM + num_ctx, hostname, thời điểm chạy) — mục 2.6.

---

## (d) Những gì KHÔNG kiểm chứng được trên máy dev này

Máy audit là Windows, không có Rust toolchain, không có GPU, không có LLM thật. Các mục sau **chỉ xác nhận được trên máy Linux thuê**:

1. **`cargo check` thật (Stage 5).** Trên máy này `shutil.which("cargo")` trả `None` → mọi hotspot ra `skipped=true`, nên `pass_at_1`/`dsr_at_1`/`error_classes` **chưa từng chạy với compiler thật**. Việc phân loại lỗi (`_ERROR_CODE_GROUPS`) chưa được kiểm chứng trên output `cargo` thật.
2. **`maturin develop --release` thật (rebuild giữa các vòng).** Trong cả 2 lượt chạy, `rebuild_from_draft` bị mock trả `REBUILT_OK`. Đường đi `BUILD_FAILED` và `SKIPPED_NO_CARGO` chỉ được kiểm bằng mock, chưa bằng build thật.
3. **`measure_in_subprocess` với extension PyO3 thật.** Đã kiểm bằng module Python giả (và trong phiên trước, bằng module đổi nhanh/chậm khi import), **chưa** với `.so` thật vừa build lại. Giả định cốt lõi "tiến trình mới thấy code mới" chưa được xác nhận trên native extension thật.
4. **Lời gọi LLM thật tới Ollama.** Cả 2 model (`devstral-small-2:latest`, `qwen3:14b`), `options.num_ctx`, `think: false`, việc lọc `<think>`, và hành vi khi prompt vượt `num_ctx` — tất cả chỉ được kiểm bằng `MockBackend`. Chưa xác nhận: server có nạp được cả 2 model cùng lúc trong ~28GB VRAM hay không, và Ollama có thật sự tôn trọng `num_ctx` per-request hay không.
5. **Dataset RepoTransBench thật.** Cả 2 lượt chạy trên `data/fake_dataset/` (2 repo, 1 file .py mỗi repo). Chưa kiểm: `build_graph` trên repo thật hàng trăm file (thời gian, bộ nhớ), `resolve_dataset_repos` trên toàn bộ dataset thật, và Stage 0 có xử lý được các file Python 2 / cú pháp lạ trong dataset không.
6. **Kết luận #1 đã được chứng minh trên dataset giả nhưng nên xác nhận lại trên dataset thật** — logic là như nhau (registry hard-code 4 tên hàm ảnh), nhưng nên chạy 1 repo RepoTransBench thật trên máy thuê để có bằng chứng trực tiếp trước khi sửa.
7. **Profiling động (Scalene).** `graph.build_mode` để `static` trong cả 2 lượt. Đường `dynamic` (POLO Eq.1-2) không nằm trong phạm vi audit này.

---

## Tổng kết một dòng

Về **EFFICIENCY**, hạ tầng đo đã khá đầy đủ và đúng thiết kế (in-process `perf_counter`, cùng input, per-round `build_status`, rebuild giữa các vòng, tuần tự không chồng với inference) — thiếu chủ yếu là **ghi lại**: mean/median/std, shape input, metadata tái lập, và speedup của `hybrid_pyo3`.

Về **CORRECTNESS**, mức hàm có đủ MATCH/MISMATCH/ERROR và mức biên dịch có đủ Pass@1/DSR@1, nhưng **hai lỗ hổng chặn**: (1) không có oracle mức repo nên không có APR/SR, và (2) registry hard-code 4 hàm ảnh khiến mọi hotspot của repo RepoTransBench đều ra `ERROR`/`n/a` **trong khi pipeline vẫn báo thành công** — đây là việc phải sửa trước khi chạy trên máy thuê, nếu không toàn bộ lượt chạy sẽ ra bảng rỗng.
