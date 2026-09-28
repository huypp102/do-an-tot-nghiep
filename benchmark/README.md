# Benchmark: Python thuần vs Rust thuần vs Hybrid PyO3

Khung (scaffold) đo & so sánh tốc độ giữa 3 phiên bản của cùng một pipeline
tiền xử lý ảnh, phục vụ đồ án tốt nghiệp về hệ thống tự động transpile
Python -> Rust cho các hàm tiền xử lý dữ liệu AI.

- **python_pure** — bản Python thuần (baseline).
- **rust_pure** — bản Rust thuần. Đo tốc độ bằng extension PyO3 gọi
  **in-process** (`versions/rust_pure/pyo3_ext/`); vẫn có thêm bản CLI độc
  lập (`cargo build --release`) để chạy/test rời khỏi Python nếu muốn, nhưng
  CLI đó không còn được dùng để đo tốc độ (xem [vì sao](#vì-sao-đo-in-process-thay-vì-subprocess)).
- **hybrid_pyo3** — Python + Rust qua PyO3, chỉ hotspot (vòng lặp per-pixel
  nặng CPU) được chuyển sang Rust.

Nguồn tham chiếu (case study): repo GitHub
[`viraj7/Computer-Vision-Image-processing`](https://github.com/viraj7/Computer-Vision-Image-processing)
— các hàm Canny edge detection, Harris corner detection, Hessian corner
detection, tách nền/vật thể bằng entropy, cài tay (không gọi thẳng OpenCV
built-in).

> **Trạng thái hiện tại: SCAFFOLD.** Toàn bộ logic xử lý ảnh thật đều là
> `TODO` + dummy data đúng shape. `python stage6_benchmark/bench.py` chạy được ngay,
> không lỗi, nhưng số liệu đo được chỉ phản ánh chi phí "khung" (gọi hàm,
> I/O), KHÔNG phải tốc độ thuật toán thật. Xem mục
> [Checklist trước khi có số liệu thật](#checklist-trước-khi-có-số-liệu-thật).

## Thư mục nào là stage nào

Cấu trúc thư mục bám trực tiếp kiến trúc 6 giai đoạn của hệ thống chính, để
mỗi thành viên biết mình đang làm ở stage nào:

```
  input/                          Nhận input: local path HOẶC URL GitHub
    └─ intake.py                  (git clone --depth 1 -> data/cloned_repos/)
            │
            ▼
  stage0_graph/                   STAGE 0 -- Global Correlation Detection
    ├─ builder.py                 PCG (call graph) + PSG (import graph)
    ├─ rank.py                    FuncRank: PageRank tĩnh + POLO Eq.1-2 động
    ├─ models.py                  FunctionNode / CallEdge / FileNode
    └─ context_export.py          xuất TOÀN BỘ graph -> results/graph_context_*.json
            │
            ▼
  stage1_profiling/               STAGE 1 -- Runtime Local Hotspot Detection
    └─ dynamic_profiler.py        Scalene (fallback cProfile) -> % time, call count
            │
            ▼
  stage2_decision_gate/           STAGE 2 -- Decision Gate (pre-filter)
    └─ gate.py                    skip | suggest_numpy_vectorization | candidate
            │
            ▼
  stage3_context_packaging/       STAGE 3 -- Đóng gói context 1 hotspot
    └─ packager.py                N_node(u)/N_edge(u) theo POLO Section 3.3
            │
            ▼
  stage4_llm_transpile/           STAGE 4 -- LLM dịch Python -> Rust
    ├─ model_backend.py           Anthropic API | server local OpenAI-style
    ├─ generator_agent.py         sinh Rust (POLO Fig.5 Generator template)
    └─ decision_agent.py          accept/reject sau benchmark (num_agents=2)
            │
            ▼
  stage5_compiler_in_the_loop/    STAGE 5 -- Compiler-in-the-loop
    ├─ compiler_loop.py           cargo check + phân loại lỗi (4 nhóm)
    └─ loop_runner.py             retry tối đa 3 vòng + Pass@1 / DSR@1
            │
            ▼
  stage6_benchmark/               STAGE 6 -- Đo tốc độ 3 phiên bản
    ├─ bench.py                   python_pure / rust_pure / hybrid_pyo3 (in-process)
    └─ report.py                  bảng mean/median/std/speedup

  versions/                       3 bản cài đặt được đem đo (không phải 1 stage)
  data/  results/  config.yaml  config_loader.py  run_pipeline.py
  Dockerfile  docker-compose.yml  .env.example    (chạy trên máy thuê GPU)
```

### Phân vai 2 agent (rất dễ làm sai)

| | Generator Agent | Decision Agent |
|---|---|---|
| Vai trò | SINH và SỬA code Rust | ĐÁNH GIÁ kết quả đo |
| Chạy ở | Stage 4 và **Stage 5** (sửa lỗi biên dịch) | **Chỉ Stage 6**, sau khi đã compile + đo xong |
| System prompt | riêng | riêng, khác hẳn |
| Lịch sử hội thoại | riêng | riêng |
| **Model** | `llm.<backend>.generator_model` | `llm.<backend>.decision_model` |

Hai agent **dùng chung một kết nối** tới server (cùng `base_url`, cùng
instance `ModelBackend`) nhưng **chạy hai model khác nhau** và **không bao
giờ thấy lịch sử của nhau**. Mỗi agent giữ list `messages` riêng và tên model
riêng trong `AgentSession`; dữ liệu cần trao đổi (code, số liệu đo, chiến
lược trước đó) truyền tường minh qua tham số hàm.

Tên model đi theo từng lời gọi (`ModelBackend.chat(..., model=...)`) chứ
không cố định cho cả instance backend, nên cùng một server Ollama phục vụ
được cả hai. Nếu config chỉ khai báo khoá cũ `model:` thì cả hai vai trò dùng
chung model đó, giữ tương thích ngược.
Đây là *role separation to avoid context entanglement* mà cả POLO lẫn
RepoTransAgent đều làm. Chi tiết: `stage4_llm_transpile/agent_session.py`.

Đặc biệt, vòng lặp sửa lỗi biên dịch ở Stage 5 gọi **Generator Agent**, không
phải Decision Agent — sửa code là việc của người viết code, không phải người
đánh giá.

### Hai vòng lặp KHÁC NHAU (rất dễ nhầm)

| | Vòng Stage 5 | Vòng Stage 6 |
|---|---|---|
| Mục tiêu | Làm cho code **chạy đúng** | Làm cho code **chạy nhanh hơn** |
| Kích hoạt bởi | Lỗi biên dịch (`cargo check`) | Decision Agent trả `CONTINUE` |
| Trần | `compiler_loop.max_retries` (3) | `optimization_loop.max_rounds` (3) |
| Ai sửa code | Generator Agent | Generator Agent |
| Ai quyết định | (không có) | Decision Agent |

**Correctness chặn ở giữa hai vòng này.** Sau khi biên dịch được, output bản
Rust được so khớp với bản Python trên các sample input
(`np.allclose` với `rtol=1e-5`, `atol=1e-8`; `math.isclose` cho số vô hướng).
Hàm nào `MISMATCH` thì **không được đem đi đo tốc độ và không vào vòng tối ưu
nào cả** — đo tốc độ của một bản dịch sai là vô nghĩa, vì code sai thường
nhanh hơn do bỏ bớt việc.

Về trần của vòng Stage 6: Decision Agent được quyền dừng **sớm** hơn theo
đúng thiết kế gốc POLO, nhưng dù nó đề xuất `CONTINUE` bao nhiêu lần đi nữa
thì tới vòng `max_rounds` là bị ép dừng, có log rõ lý do.

### Mỗi vòng đo đúng code vừa sinh (rebuild giữa các vòng)

Giữa hai vòng tối ưu, code Rust mới **không chỉ được ghi ra file nháp**. Nếu
dừng ở đó thì extension đang nạp trong tiến trình vẫn là bản cũ, và mọi vòng
sẽ cho speedup giống hệt nhau. Quy trình thật mỗi vòng:

1. Copy draft mới nhất từ `pyo3_ext/generated/<hàm>.rs` đè vào
   `pyo3_ext/src/lib.rs` (vị trí `maturin` build).
2. Chạy `maturin develop --release` với timeout `optimization_loop.build_timeout_sec`.
3. Đo lại trong **một tiến trình mới**, vì extension native không reload an
   toàn trong tiến trình đang chạy.

`build_status` ghi lại mỗi vòng đo trên code nào:

| Giá trị | Nghĩa |
|---|---|
| `INITIAL` | Vòng 1, đo trên extension có sẵn |
| `REBUILT_OK` | Đã build lại, số đo là của code mới |
| `BUILD_FAILED` | Build lỗi, dừng vòng lặp, giữ kết quả vòng trước, không báo speedup cho vòng lỗi |
| `SKIPPED_NO_CARGO` | Máy không có cargo/maturin, dừng thay vì đo lại bản cũ |

**An toàn với code viết tay:** `pyo3_ext/src/lib.rs` có thể là code bạn tự
viết. Trước lần ghi đè đầu tiên, file gốc được sao lưu thành
`lib.rs.orig_backup`, và được khôi phục trong khối `finally` khi vòng lặp
kết thúc, kể cả khi có lỗi. Bản do LLM sinh vẫn nằm trong
`pyo3_ext/generated/` để bạn xem lại.

**Lưu ý về phạm vi:** việc đo trong tiến trình con **chỉ** áp dụng cho các
lần đo lại giữa các vòng tối ưu, vài lần cho mỗi hotspot. Thiết kế đo
**in-process** trong `stage6_benchmark/bench.py` vẫn giữ nguyên cho bối cảnh
vòng lặp training thật, nơi hàm được gọi hàng nghìn lần trong cùng tiến
trình. Hai bối cảnh khác nhau nên cố ý dùng hai cách đo khác nhau.

### Bảng tổng kết cuối

Mỗi hotspot hiện đúng 4 chỉ số:

```
hotspot                 correctness   pass@1   speedup   rounds
---------------------------------------------------------------
normalize                     MATCH      yes     1.08x       3*
```

- `correctness`: `MATCH` / `MISMATCH` / `ERROR`
- `pass@1`: Stage 5 biên dịch được **ngay lần đầu**, không cần sửa lần nào
- `speedup`: `python_pure / rust_pure`, lấy vòng tốt nhất
- `rounds`: số vòng tối ưu đã chạy; dấu `*` nghĩa là dừng vì chạm trần

Các chỉ số chi tiết hơn (DSR@1, phân loại lỗi biên dịch từng vòng, ...) vẫn
được tính và lưu trong `results/pipeline_summary_*.json`, nhưng không hiện ở
bảng này để người đọc tập trung vào 4 con số quan trọng nhất.

## Thực nghiệm 1 trên máy Linux thuê

Đúng 5 bước. Không cần Docker.

### Bước 1 — Clone

```bash
git clone <repo-của-bạn> && cd <repo>/benchmark
```

### Bước 2 — Đặt biến môi trường

Mọi giá trị phụ thuộc máy nằm ở đây, **không có đường dẫn nào hard-code trong code**:

```bash
export DATASET_DRIVE_ID=<id file Google Drive chứa dataset>   # chỉ cần lần đầu
export RTB_WORK_DIR=/mnt/data/rtb_work    # ổ còn nhiều chỗ; để trống thì dùng thư mục tạm hệ thống
export RUN_PROFILE=pilot_linux
# 2 biến dưới đây setup_linux.sh sẽ in ra sau khi chạy xong:
# export REPOTRANSBENCH_ROOT=...
# export OLLAMA_BASE_URL=...
```

`DATASET_DRIVE_ID` cố ý **không** nằm trong repo: id đó thuộc về người chạy, không thuộc về source.

### Bước 3 — Dựng môi trường

```bash
bash scripts/setup_linux.sh
```

Idempotent — chạy lại bao nhiêu lần cũng được, mỗi bước tự kiểm tra trước khi làm. Nó lo: gói apt, rustup, venv, `requirements.txt` + maturin + gdown, tải và giải nén dataset (kiểm đủ 171 repo), khởi động Ollama **chỉ nghe localhost** với `OLLAMA_MAX_LOADED_MODELS=2` và `OLLAMA_KEEP_ALIVE=30m`, pull 2 model.

Nếu image thuê đã có `ollama serve` làm **PID 1**, script **không giết** nó (giết PID 1 có thể làm sập container) mà mở bản riêng ở cổng `11435` rồi in ra `OLLAMA_BASE_URL` cần dùng.

Cuối cùng nó in đúng các lệnh `export` còn lại — copy và chạy.

### Bước 4 — Chạy

```bash
bash scripts/run_pilot.sh
```

Chạy `nohup` nên mất SSH không mất lượt chạy. Script in cách theo dõi:

```bash
tail -f results/<run_id>/pilot.log
ls results/<run_id>/repos/          # repo nào đã xong (ghi NGAY khi xong)
curl -s $OLLAMA_BASE_URL/api/ps     # 2 model có cùng nạp không
nvidia-smi
```

Bị cắt giữa đường thì chạy tiếp, bỏ qua **cặp (repo, nhánh)** đã xong:

```bash
bash scripts/run_pilot.sh --resume --run-id <run_id>
```

Muốn xem kế hoạch trước khi tốn giờ GPU: `python run_experiment1.py --profile pilot_linux --dry-run`.

**Preflight chạy đầu tiên và dừng ngay nếu thiếu thứ gì** — thiếu `cargo`/`maturin`/Ollama/dataset, hoặc `REPOTRANSBENCH_ROOT` trỏ nhầm vào `target_projects`. Nó dừng **trước** lời gọi LLM đầu tiên, nên không đốt giờ GPU vô ích. Chạy riêng được: `python preflight.py --profile pilot_linux`.

### Bước 5 — Thu kết quả

```bash
bash scripts/collect_results.sh <run_id>
# rồi chạy TRÊN MÁY CÁ NHÂN lệnh scp mà script in ra
```

File cần đọc trong `results/<run_id>/`:

| File | Nội dung |
|---|---|
| `metadata.json` | môi trường, kết quả preflight, **quy tắc chọn mẫu**, repo bị loại kèm lý do |
| `selection.json` | ứng viên, repo được chọn, repo dự phòng |
| `report.md` | phễu + bảng theo repo + APR/SR + ablation |
| `funnel.json` | phễu dạng máy đọc được |
| `ablation_report.{md,json}` | so sánh theo cặp nhánh `graph` vs `none` |
| `repos/<repo>.json` | từng repo: lý do từng hotspot, số đo từng vòng |
| `arm_cache/` | kết quả từng (repo, nhánh) — nguồn cho `--resume` |

### Thiên lệch chọn mẫu — phải nêu trong luận văn

12 repo **không phải mẫu ngẫu nhiên** từ 171 repo. Chúng là những repo đầu tiên theo thứ tự tên mà bộ test gốc chạy được **và** có ≥ 2 hotspot ghi/phát lại được đối số. Quy tắc này được chốt **trước** khi xem kết quả và ghi nguyên văn vào `metadata.json` cùng lý do loại từng repo.

Lý do phải sàng: đo thật trên `piskvorky_sqlitedict` cho thấy **5/5 hotspot hạng cao bị loại** — 3 trong đó là method của đối tượng giữ `_contextvars.Context` nên không pickle được. Lấy thẳng top-5 thì thường còn 0 hotspot để dịch, và ablation không có gì để so.

### Hai profile

| | `dev_windows` | `pilot_linux` |
|---|---|---|
| LLM | tắt | `local`, 2 agent |
| `num_ctx` | mặc định | 16384 / 8192 (VRAM cho 2 model) |
| dataset | `data/fake_dataset` | thật, 12 repo |
| ablation | tắt | bật, 2 nhánh |
| `build_mode` | `static` | `dynamic` (POLO Eq.1-2) |
| ngân sách | 1h | 6h |

`config.yaml` chỉ chứa **mặc định trung tính**; profile gộp đè lên nó. Chọn bằng `RUN_PROFILE` hoặc `--profile`. Sai tên profile thì **báo lỗi kèm danh sách có sẵn** chứ không im lặng chạy bằng mặc định.

### Chạy toàn bộ test

```bash
python tests/run_all.py            # bỏ nhóm cần dataset thật
python tests/run_all.py --fast     # chỉ nhóm nhanh (~30s)
python tests/run_all.py --list     # xem phân nhóm
python tests/run_all.py --with-dataset   # gồm cả nhóm cần REPOTRANSBENCH_ROOT
```

## Việc chỉ xác nhận được trên máy thuê

Toàn bộ kết quả hiện có đạt được **không có** Rust toolchain, **không có** GPU, **không có** LLM thật. Những điều sau chưa kiểm chứng được:

1. **`cargo check` và `maturin develop` thật.** Trong test, bước build bị thay bằng module Python cùng tên hàm. Cả đường ống (import, so khớp, hoán đổi, đo, build lại giữa các vòng) đã được kiểm, nhưng **việc biên dịch Rust thì chưa**.
2. **Code Rust do LLM sinh có biên dịch được không, Pass@1 thật bao nhiêu.** Prompt Tầng 1/Tầng 2 và ánh xạ kiểu (`list[float]` → `Vec<f64>`, sửa-tại-chỗ → `&Bound<'_, PyList>`) mới chỉ chạy với backend giả. Đây là ẩn số lớn nhất còn lại.
3. **Tầng 2 (kernel + shim) với PyO3 thật.** Shim trong test do tôi viết tay, chưa biết LLM có trả đúng khối `## Python shim` khớp chữ ký hàm gốc hay không.
4. **`maturin develop` cài vào venv RIÊNG của từng repo**, và việc **gỡ cài giữa hai nhánh ablation** trên extension `.so` thật (trong test chỉ là file `.py`).
5. **Ollama thật:** 2 model, `options.num_ctx`, `options.seed`, `temperature`, `think: false`, và liệu ~28GB VRAM có nạp nổi cả hai model cùng lúc — `preflight` kiểm mục này bằng `ollama ps` nhưng chưa chạy thật lần nào.
6. **`options.seed` có thật sự làm Ollama tất định hay không.** Ablation giả định điều đó; nếu không đúng thì `repeats: 1` là chưa đủ và cần tăng số lần lặp.
7. **Toàn bộ 171 repo.** Đã chạy thật **2 repo** (33.7s). Chưa biết `repo_time_budget_sec=1800` có đủ cho repo lớn, `pip install -e .` fail ở bao nhiêu repo, và tỉ lệ hotspot đi tới bước đo trên toàn dataset.
8. **`load_average`** chỉ có trên POSIX nên trên máy dev luôn `null`.
9. **`build_mode: dynamic` (Scalene)** — profile `pilot_linux` bật nó, nhưng mọi lượt chạy tới nay đều dùng `static`.

## Chạy trên máy thuê GPU (Docker)

Repo này **không chứa dataset và không hardcode đường dẫn máy nào**. Máy thuê
GPU chỉ cần clone repo, trỏ biến môi trường vào dataset họ tự tải về, rồi
`docker compose up`.

### Điều kiện hạ tầng (máy thuê phải có sẵn)

Những thứ sau là việc của **hạ tầng máy thuê**, code không lo được:

- Driver NVIDIA đã cài và `nvidia-smi` chạy được.
- `nvidia-container-toolkit` đã cài, để Docker nhìn thấy GPU.
- Docker + Docker Compose v2.

Kiểm tra nhanh trước khi chạy:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

Nếu lệnh thứ hai lỗi thì container `ollama` sẽ không thấy GPU, cần sửa hạ
tầng trước, không phải sửa code.

### Các bước

```bash
git clone <repo-cua-ban> && cd benchmark

# 1. Khai báo đường dẫn dataset trên máy host
cp .env.example .env
#    rồi sửa REPOTRANSBENCH_HOST_PATH trong .env, ví dụ:
#    REPOTRANSBENCH_HOST_PATH=/mnt/data/RepoTransBench

# 2. Dựng và chạy
docker compose up -d

# 3. Tải CẢ HAI model về ollama (chỉ cần làm 1 lần, model nằm trong volume).
#    Generator Agent và Decision Agent chạy 2 model KHÁC NHAU.
docker compose exec ollama ollama pull devstral-small-2:latest  # Generator: sinh/sửa code Rust
docker compose exec ollama ollama pull qwen3:14b                # Decision: đánh giá kết quả đo
#    Máy yếu thì đổi sang model nhỏ hơn trong .env, ví dụ:
#    OLLAMA_GENERATOR_MODEL=qwen2.5-coder:7b
#    OLLAMA_DECISION_MODEL=llama3.2:3b

# 4. Bật LLM trong config.yaml: llm.enabled: true, backend: local
#    rồi chạy pipeline
docker compose run --rm benchmark python run_pipeline.py
```

### Kiểm tra sau khi chạy vài lượt

Chạy pipeline vài lượt rồi kiểm tra hai thứ:

```bash
# 1. CẢ HAI model phải cùng ở trạng thái loaded
docker compose exec ollama ollama ps

# 2. Tổng VRAM nên dưới ~28GB
nvidia-smi
```

Nếu `ollama ps` chỉ thấy **một** model tại một thời điểm, nghĩa là Ollama đang
nạp rồi gỡ luân phiên hai model. Mỗi lần nạp lại tốn hàng chục giây và làm
nhiễu số đo. Hai biến trong `docker-compose.yml` xử lý việc này:

| Biến | Giá trị | Tác dụng |
|---|---|---|
| `OLLAMA_MAX_LOADED_MODELS` | `2` | Cho phép giữ hai model cùng lúc |
| `OLLAMA_KEEP_ALIVE` | `30m` | Không gỡ model khỏi VRAM sau mỗi lượt |

Nếu VRAM vượt ngưỡng, giảm `num_ctx` trước khi nghĩ tới việc đổi model nhỏ
hơn. `num_ctx` ảnh hưởng gần như tuyến tính tới bộ nhớ KV cache.

### Cửa sổ ngữ cảnh (`num_ctx`) theo vai trò

| Vai trò | Mặc định | Lý do |
|---|---|---|
| Generator | `32768` | Prompt chứa code gốc và context từ Stage 3 |
| Decision | `16384` | Chỉ nhận số liệu đo, hẹp hơn là đủ |

Ollama **không báo lỗi** khi prompt dài quá `num_ctx`, nó lặng lẽ cắt bớt
phần đầu, tức là mất system prompt và mất code gốc, khiến model trả lời lạc
đề mà không ai biết vì sao. Backend tự ước lượng số token theo công thức
`số ký tự / 3.5` và ghi log WARNING trước khi gửi nếu vượt ngưỡng. **Chỉ
tăng `num_ctx` khi thực sự thấy cảnh báo đó**, đừng tăng phòng hờ.

Lưu ý kỹ thuật: `num_ctx` chỉ truyền được qua API native của Ollama
(`POST /api/chat`). Endpoint OpenAI-compatible `/v1/chat/completions` không
chở được `options.num_ctx`. Vì vậy `llm.local.api_style` mặc định là
`ollama`. Nếu dùng vLLM hay LM Studio thì đổi sang `openai`, khi đó `num_ctx`
sẽ không có tác dụng và backend sẽ cảnh báo rõ.

### Model reasoning và khối `<think>`

`qwen3` là model reasoning, nó hay "suy nghĩ ra tiếng" trong thẻ
`<think>...</think>`. Phần suy nghĩ thường cân nhắc cả hai hướng nên rất dễ
chứa chữ `REJECT` dù kết luận cuối là `ACCEPT`. Hệ thống xử lý hai lớp:

1. Gửi `think: false` trong request tới Ollama. Bản Ollama cũ không hiểu
   tham số này sẽ trả HTTP 400, khi đó backend tự thử lại một lần không kèm
   tham số đó.
2. Luôn lọc sạch khối `<think>` trước khi parse, kể cả khi thẻ đóng bị mất do
   response bị cắt giữa chừng. Việc parse `ACCEPT`/`REJECT`/`CONTINUE`/`STOP`
   chỉ dựa trên phần còn lại.

### Vì sao `base_url` là `http://ollama:11434`

Bên trong container `benchmark`, `localhost` trỏ về chính container đó, không
sang được container `ollama`. Phải dùng **tên service** trong mạng nội bộ của
compose. Mặc định trong `config.yaml` đã là `${OLLAMA_BASE_URL:-http://ollama:11434}`.

Chạy **ngoài** Docker (Ollama cài thẳng trên máy) thì set biến môi trường:

```bash
export OLLAMA_BASE_URL=http://localhost:11434
```

### Biến môi trường

| Biến | Dùng để | Mặc định |
|---|---|---|
| `REPOTRANSBENCH_HOST_PATH` | Đường dẫn dataset **trên host**, để mount vào container | `./data/RepoTransBench` |
| `REPOTRANSBENCH_ROOT` | Đường dẫn dataset **trong container** | `/app/data/RepoTransBench/source_projects/Python` |
| `OLLAMA_BASE_URL` | Endpoint model local | `http://ollama:11434` |
| `OLLAMA_GENERATOR_MODEL` | Model cho Generator Agent | `devstral:24b` |
| `OLLAMA_DECISION_MODEL` | Model cho Decision Agent | `qwen3:8b` |
| `ANTHROPIC_API_KEY` | Chỉ cần nếu muốn chạy `llm.backend: api` | (trống) |

`config.yaml` đọc các biến này qua cú pháp `${BIẾN:-mặc định}`, xử lý trong
`config_loader.py::expand_env_vars`. Nếu dataset chưa tải về hoặc mount sai,
pipeline **báo lỗi kèm hướng dẫn cụ thể chứ không traceback**.

## Hai cách chạy

| | `stage6_benchmark/bench.py` | `run_pipeline.py` |
|---|---|---|
| Dùng khi | Chỉ cần **đo tốc độ** code đã điền tay | Muốn test **cả chuỗi**: input GitHub → graph → gate → LLM → đo |
| Input | `target.source` là local path | local path **hoặc URL GitHub** |
| Decision Gate | không chạy | có (`decision_gate.enabled`) |
| LLM | không bao giờ đụng tới | có, nếu `llm.enabled: true` |
| Phụ thuộc thêm | không | `anthropic` (chỉ khi `llm.backend: api`) |

```bash
python stage6_benchmark/bench.py     # đường cũ, vẫn chạy độc lập y như trước
python run_pipeline.py               # đường đầy đủ 6 stage
```

`bench.py` **không** bị buộc phải đi qua `run_pipeline.py`. Hai file dùng
chung đúng một bộ hàm đo (`run_pipeline.py` gọi lại `bench._bench_*`) nên
không có hai bản logic song song dễ lệch nhau.

### Bật/tắt LLM (Stage 4)

Mặc định **TẮT** — không ai bị bắt buộc phải có API key mới dùng được
benchmark. Khi tắt, `run_pipeline.py` bỏ qua hẳn Stage 3/4 và cho kết quả
giống hệt chạy `bench.py`.

```yaml
llm:
  enabled: false     # true để bật Stage 3/4
  backend: api       # api (Anthropic) | local (server OpenAI-compatible)
  num_agents: 1      # 1 = chỉ Generator + rule accept/reject
                     # 2 = thêm Decision Agent bằng LLM (POLO Fig.5)
  api:
    model: claude-sonnet-4-5-20250929   # API key đọc từ $ANTHROPIC_API_KEY
  local:
    base_url: http://localhost:11434    # Ollama / vLLM / LM Studio
    model: codellama:13b
```

- `backend: api` cần `pip install anthropic` và biến môi trường
  `ANTHROPIC_API_KEY` (key **không bao giờ** nằm trong `config.yaml`).
- `backend: local` không cần cài thêm gì (dùng `urllib` của stdlib), chỉ cần
  một server expose `POST {base_url}/v1/chat/completions`.
- **Thiếu key / chưa cài package / server không chạy** → log lỗi rõ ràng rồi
  **bỏ qua Stage 3/4**, pipeline vẫn chạy tiếp tới Stage 6. Không crash.
- `num_agents: 1` → bước accept/reject dùng rule thuần (accept nếu speedup
  `rust_pure`/`python_pure` > 1.0, dừng sau 1 vòng), **không tốn token nào**.

### Input là URL GitHub

```yaml
target:
  mode: repo
  source: https://github.com/viraj7/Computer-Vision-Image-processing
```

`input/intake.py` tự nhận diện URL (http/https + `github.com`), chạy
`git clone --depth 1` vào `data/cloned_repos/<tên_repo>/` rồi trả về đường
dẫn local cho Stage 0. Lần chạy sau dùng lại bản đã clone (không tốn mạng).
Clone lỗi (repo private, mất mạng, URL sai) → log rõ, bỏ qua phần cần target
đó, không crash. `data/cloned_repos/` đã được `.gitignore`.

## Hai đường chạy: LEGACY và REPO ĐỘNG

Từ khi có oracle mức repo, `run_pipeline.py` có **hai** đường chạy tách hẳn nhau.
`_dynamic_mode_selected()` chọn đường, và chọn sai đường là nguồn nhầm lẫn lớn
nhất khi đọc kết quả, nên phần này nói rõ.

| | LEGACY | REPO ĐỘNG |
|---|---|---|
| Bật khi | `target.mode: function` | `target.mode: file\|repo`, hoặc `dataset.enabled: true` |
| | (và/hoặc `repo_oracle.enabled: false`) | **và** `repo_oracle.enabled: true` |
| Hàm được đo | 4 hàm viraj7 trong `PIPELINE_REGISTRY` (cứng) | hàm THẬT của repo, nạp theo `module:qualname` |
| Workload | 1 ảnh mẫu từ `data/sample_input/` | **đối số thật** do bộ test của repo tạo ra |
| Oracle đúng đắn | so output hàm-với-hàm trên 3 sample ảnh | so trên đối số thật **+ chạy lại cả bộ test của repo** |
| Cách đo | in-process, `time.perf_counter()` | 1 tiến trình con trong venv của repo, **cả 3 phiên bản cùng tiến trình** |
| File kết quả | `pipeline_raw_*.json`, `pipeline_summary_*.json` | `repo_summary_<ts>_<repo>.json`, `report_<ts>_repos.md` |
| Cần LLM | không (code Rust điền tay) | **có** — chữ ký mỗi hotspot mỗi khác, phải sinh |

Đường LEGACY **không đổi gì** so với trước: nó vẫn là cách để đo 4 hàm ảnh đã
điền tay. Muốn bắt `mode: repo` chạy lại đường cũ thì đặt
`repo_oracle.enabled: false`.

### Đường REPO ĐỘNG làm gì, theo thứ tự

1. **Copy repo** ra `repo_oracle.work_root` — dataset mount chỉ đọc, mà pytest
   cần ghi cache và bước build cần chèn extension vào.
2. **Venv riêng cho repo** (dùng `uv` nếu có), cài `requirements.txt` hoặc
   `pip install -e .`, cộng `pytest` + `cloudpickle`. Lỗi ⇒ `INSTALL_FAILED`.
3. **Stage 0** dựng PCG/PSG trên bản copy, FuncRank chọn top-K hotspot.
4. **Stage 2** Decision Gate ⇒ hotspot bị gạt nhận `GATE_SKIPPED`.
5. **Ghi đối số thật**: chạy bộ test GỐC kèm plugin
   `stage1_profiling/capture_plugin.py`, lưu tối đa `max_captured_calls` lời gọi
   mỗi hotspot (args, kwargs, giá trị trả về, exception, **và trạng thái đối số
   SAU lời gọi** để bắt hàm sửa tại chỗ).
6. **Phát lại 2 lần** trên chính bản Python. Lệch ⇒ `NONDETERMINISTIC`.
   Đồng thời **phân tầng kiểu** từ đối số thật.
7. **Stage 3/4** sinh Rust theo **chữ ký thật** (không phải theo type-hint).
8. **Stage 5** `cargo check` trên crate riêng từng hotspot + vòng sửa lỗi.
9. **Build** `maturin develop --release` vào venv của repo — **Tầng 1 xong
   trước, rồi mới Tầng 2**; Tầng 2 hỏng không chặn Tầng 1.
10. **So khớp** bản Rust với bản Python trên đối số thật, cho **cả**
    `rust_pure` **và** `hybrid_pyo3`.
11. **Chạy lại bộ test** với hotspot đã thay bằng Rust ⇒ `hybrid_tests`,
    `REGRESSION_FREE`.
12. **Đo tốc độ** cả 3 phiên bản trong cùng tiến trình, rồi vòng tối ưu.

### Hai tầng chữ ký (Tầng do CODE quyết định, không hỏi LLM)

| Tầng | Khi nào | Rust nhận gì | `hybrid_pyo3` trỏ vào |
|---|---|---|---|
| `TIER1_NATIVE` | mọi đối số là kiểu gốc (số, str, bytes, list/dict của kiểu gốc, ndarray) | đúng chữ ký, có kiểu | chính hàm Rust |
| `TIER2_KERNEL` | có đối tượng tuỳ ý, nhưng thuộc tính là kiểu gốc | chỉ các **trường** kiểu gốc | **shim Python** tháo đối tượng rồi gọi kernel |
| ngoài 2 tầng | còn lại | — | `UNSUPPORTED_KIND` |

Để `repo_oracle.signature_support: native_only` nếu chỉ muốn số liệu Tầng 1:
hotspot cần shim sẽ bị loại thành `UNSUPPORTED_KIND` thay vì đo một thứ khác.

### Mỗi hotspot rời pipeline với ĐÚNG MỘT lý do

Không còn hotspot nào lặng lẽ biến mất khỏi bảng. Enum đầy đủ trong
[outcomes.py](outcomes.py):

`MEASURED` · `UNSUPPORTED_KIND` · `UNREPLAYABLE_ARGS` · `NOT_COVERED_BY_TESTS` ·
`NONDETERMINISTIC` · `UNRESOLVABLE_IMPORT` · `COMPILE_FAILED` ·
`CORRECTNESS_FAILED` · `NO_IMPLEMENTATION` · `GATE_SKIPPED` · `LLM_FAILED` ·
`BUILD_FAILED` · `MEASURE_FAILED`

Và mỗi repo có một `repo_status`: `OK` · `PARTIAL` · `NO_MEASURABLE_HOTSPOT` ·
`BASELINE_FAILED` · `INSTALL_FAILED` · `TIMEOUT`.

**`summary["ok"]` chỉ `true` khi có ≥1 hotspot `MEASURED`**, và
`run_pipeline.py` **thoát với exit code 2** nếu cả lượt chạy không đo được gì.
Trước đây pipeline luôn trả 0 kể cả khi bảng kết quả toàn `n/a`.

`BASELINE_FAILED` nghĩa là bộ test Python của repo **đã fail sẵn** khi chưa ai
chạm tới Rust. Repo đó bị **LOẠI khỏi mẫu** APR/SR — lỗi có sẵn của repo không
được tính cho bản hybrid.

### An toàn khi chạy code repo lạ

Test của repo trong dataset là code của người khác, chạy với quyền của tiến
trình này. `repo_runner.build_child_env()` **lọc sạch** mọi biến môi trường
khớp `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `*_CREDENTIALS` cộng
`ANTHROPIC_API_KEY`/`OPENAI_API_KEY` trước khi spawn — lọc theo hậu tố nên biến
mới thêm sau này cũng tự động bị lọc.

Venv của repo bị xoá sau mỗi repo (`keep_venv: false`). Chạy cả dataset mà giữ
lại venv sẽ làm đầy đĩa máy thuê rất nhanh.

## 3 cấp độ benchmark (function / file / repo)

Chọn qua `target.mode` trong `config.yaml`, theo đúng kiến trúc Stage 0 của
hệ thống chính (PSG/PCG/FuncRank -- xem `stage0_graph/`):

| `target.mode` | Cần `target.source`? | Cách chọn hàm để benchmark | Đo whole-scope? |
|---|---|---|---|
| `function` (**mặc định**) | Không | Danh sách cứng `benchmark.functions` (4 hàm hiện có) | Không |
| `file` | Có — 1 file `.py` | Top-K hàm theo FuncRank trong file đó | Có |
| `repo` | Có — 1 thư mục | Top-K hàm theo FuncRank trong toàn bộ thư mục (multi-file) | Có |

**`mode: function`** giữ nguyên 100% hành vi trước khi có `stage0_graph/` — không
import, không phụ thuộc `tree-sitter`/`networkx`, không có gì thay đổi.

**`mode: file` / `mode: repo`** làm thêm các bước sau trước khi benchmark
(`stage6_benchmark/bench.py::_select_target_functions`):

1. `stage0_graph/builder.py` parse toàn bộ file `.py` trong `target.source` (1 file
   nếu mode=file, quét đệ quy `*.py` nếu mode=repo, bỏ qua `__pycache__`,
   `.git`, `.venv`, `target`, ...) thành:
   - **PCG** (Program Call Graph): node = từng hàm (tên, file, dòng, source
     code), edge = quan hệ gọi hàm A→B trong phạm vi scope.
   - **PSG** (Program Structure Graph): node = từng file, edge = quan hệ
     import giữa các file trong scope.
2. `stage0_graph/rank.py` chạy `networkx.pagerank()` trên PCG ra **FuncRank** cho
   từng hàm (bản đơn giản — xem docstring trong file, interface giữ ổn định
   để sau này thay bằng thuật toán ranking phức tạp hơn, vd kiểu Aider/POLO).
3. `stage0_graph/context_export.py` xuất TOÀN BỘ graph (node, edge, FuncRank, source
   code từng hàm) ra `results/graph_context_<timestamp>.json` — đây là
   **context cho model hiểu dependency giữa các hàm/file** khi sinh code
   Rust (dùng qua `stage4_llm_transpile/model_backend.py`, xem mục bên dưới). Bước xuất
   này chạy TRƯỚC benchmark, độc lập với việc benchmark có chạy được hay không.
4. Lấy top `graph.top_k_hotspots` hàm (theo FuncRank) làm danh sách hàm để
   benchmark, **thay cho** `benchmark.functions` cứng.
5. Với mỗi hàm trong top-K: nếu `versions/python_pure`, `versions/rust_pure`,
   `versions/hybrid_pyo3` **chưa có implementation** cho tên hàm đó (vì đây
   là hàm mới phát hiện tự động, chưa ai điền tay) → log warning và **bỏ qua
   riêng hàm đó**, không crash cả benchmark (đúng pattern skip đã có sẵn cho
   `rust_pure`/`hybrid_pyo3` khi thiếu build).
6. Benchmark ở **2 mức**:
   - **Per-function**: y hệt `mode: function` (warmup + N lần, per hàm).
   - **Whole-scope**: gọi **1 lần duy nhất** toàn bộ chuỗi hàm top-K theo
     **thứ tự topological của PCG** (hàm gọi trước hàm được gọi), đo tổng
     thời gian — con số "chạy hết cả file/repo mất bao lâu", lưu riêng vào
     `results/whole_scope_<timestamp>.json` và in thêm 1 bảng
     `WHOLE-SCOPE REPORT` (xem `stage6_benchmark/report.py::build_whole_scope_report`).

### Ví dụ cấu hình `mode: repo`

```yaml
target:
  mode: repo
  source: data/reference_repo
```

`data/reference_repo/` đã có sẵn 4 file gốc thật của repo viraj7
(`edge_detection.py`, `"harris corner detection.py"`,
`"hessian corner detection.py"`, `image_entropy.py`, tải trực tiếp từ
GitHub). **Lưu ý:** 4 file này gần như độc lập (không gọi hàm của nhau) —
dùng để **test cơ chế graph chạy được end-to-end** (parse nhiều file thật,
xuất context, chọn top-K, bỏ qua hàm thiếu implementation), **không phải**
ví dụ có nhiều context xuyên hàm/file thật sự. Trên thực tế PCG vẫn tìm được
1 cạnh gọi hàm thật (`edge_det` gọi `gen_gauss1d_k` trong cùng
`edge_detection.py`), đủ để FuncRank xếp hạng khác biệt (hàm bị gọi có điểm
cao hơn).

### Backend parser: tree-sitter (thật) hoặc `ast` (fallback tạm thời)

`stage0_graph/builder.py` ưu tiên dùng `tree-sitter` + `tree-sitter-python` (đúng
công cụ đã chốt trong thiết kế Stage 0 chính thức). Nếu 2 gói này chưa cài
được, tự động fallback sang module `ast` chuẩn của Python — không cần cài
thêm gì, nhưng **kém bền hơn**: `ast.parse()` dừng hẳn (raise `SyntaxError`)
nếu 1 file lỗi cú pháp, trong khi `tree-sitter` được thiết kế để dung sai lỗi
cú pháp (parse tiếp phần còn lại). Ví dụ thật với `data/reference_repo/`: 2
trong 4 file viraj7 viết bằng cú pháp Python 2 (`print bien`) —
`tree-sitter` parse được **cả 4 file**, còn fallback `ast` chỉ parse được
**2 file** (2 file Python 2 bị bỏ qua với warning, không crash cả graph).
Đây là minh chứng cụ thể vì sao `tree-sitter` được chọn trong thiết kế chính
thức thay vì `ast`. Cài `tree-sitter`:

```bash
pip install tree-sitter tree-sitter-python
```

### PCG động (Scalene) vs tĩnh (tree-sitter/ast)

Mặc định (`graph.build_mode: static`), PCG chỉ dựng từ phân tích TĨNH (parse
source, không chạy chương trình) — FuncRank khi đó là PageRank THUẦN trên
cấu trúc liên kết (ai gọi ai), không biết hàm nào thật sự "nóng" (tốn nhiều
thời gian thực thi).

Đặt `graph.build_mode: dynamic` để dựng PCG kiểu ĐỘNG, theo đúng phương pháp
**Runtime Local Hotspot Detection** của paper **POLO** (Bai et al., *"POLO:
An LLM-Powered Project-Level Code Performance Optimization Framework"*,
IJCAI-25, Section 3.1): chạy target dưới profiling với **ảnh mẫu thật**, đo
% thời gian + số lần gọi THẬT của từng hàm và từng cạnh gọi hàm, rồi xếp
hạng bằng công thức FuncRank có trọng số thời gian:

```
w_ji = Ac_ji[time] / Σ_{u∈caller(j)} Ac_ju[time]        (Eq.1)
I_i  = (1-α)·Ac_i[time] + α·Σ_{j∈callee(i)} w_ji·I_j      (Eq.2)
```

(α = 0.5 mặc định, lặp tới hội tụ — xem `stage0_graph/rank.py::func_rank_dynamic`).
**Vì sao đổi theo POLO thay vì chỉ dùng PageRank cấu trúc:** 2 hàm có cùng
số lượng liên kết trong PCG có thể có mức ảnh hưởng thực tế rất khác nhau
(1 hàm chạy 1000 lần tốn 80% thời gian chương trình, 1 hàm khác chỉ chạy 1
lần tốn 0.01%) — PageRank thuần không phân biệt được, còn Eq.1-2 lan truyền
đúng theo tỉ trọng thời gian thật.

**Vì sao dùng Scalene thay Callgrind:** paper POLO dùng Callgrind (Valgrind)
+ tự viết 1 finite state machine (FSM) để parse output nhị phân của nó —
Callgrind chỉ chạy tốt trên Linux, không hợp với môi trường phát triển
Windows của đồ án này. Scalene đã được chốt dùng cho **Stage 1** của hệ
thống chính (đồng bộ công cụ giữa các Stage), chạy được trên Windows, và
đơn giản hơn nhiều để tích hợp: `stage1_profiling/dynamic_profiler.py` KHÔNG cần dựng
FSM — vì đo trực tiếp ở mức Python (không parse binary instrumentation),
chỉ cần 1 decorator wrap từng hàm target để ghi lại caller/callee/thời gian
mỗi lần gọi (nguồn CHÍNH cho count/time). Scalene chỉ được dùng BỔ SUNG cho
breakdown % Python-only vs native/C-extension mỗi hàm (Scalene là sampling
profiler nên không đáng tin cho count chính xác, và **đã kiểm chứng thực
tế**: với các hàm dummy quá nhanh của scaffold này, Scalene chạy thành công
nhưng không bắt được mẫu nào cho chúng — dữ liệu count/time chính vẫn đầy đủ
nhờ decorator, không phụ thuộc việc Scalene có "thấy" hàm hay không).

Nếu module `scalene` không cài được/lỗi, `stage1_profiling/dynamic_profiler.py` tự
fallback sang `cProfile` (có sẵn trong Python) cho phần bổ sung đó — ghi rõ
log cảnh báo, không crash, không ảnh hưởng tới nguồn count/time chính.

**Khi nào fallback về static:** hàm nào được PHÁT HIỆN qua phân tích tĩnh
(có trong PCG) nhưng KHÔNG được thực thi trong lần profiling động — vì (a)
chưa có implementation callable trong `versions/python_pure/pipeline.py`
(trường hợp phổ biến nhất với scaffold này: các hàm gốc viraj7 dùng
`cv2.imshow`/Python 2 không thể tự động chạy an toàn), hoặc (b) code path
không được chạy tới với workload đang dùng (vd nhánh `if/else` không được
kích hoạt) — sẽ có `dynamic_time_pct = None` và `rank_source =
"static_fallback"` trong context JSON, dùng điểm PageRank tĩnh thay thế.
Đây CHÍNH LÀ vấn đề mà paper POLO mô tả ở Section 3.2 (runtime analysis
không phủ hết mọi code path) — static PSG/PCG dùng để bổ sung đúng lỗ hổng
đó, không phải bị thay thế hoàn toàn bởi runtime analysis.

Ví dụ thật đã kiểm chứng với `data/reference_repo/` (4 file viraj7,
`workload_iterations: 20`): `edge_det`, `harris`, `hess_corner_det`,
`im_threshold` (có trong `PIPELINE_REGISTRY`) → `rank_source="dynamic"`,
`dynamic_call_count=20`; `gen_gauss1d_k` (chỉ là helper nội bộ trong source
gốc, chưa ai điền lại trong `pipeline.py`) → `rank_source="static_fallback"`.

Build bằng `maturin`? Không cần — Scalene cài qua pip bình thường:

```bash
pip install scalene
```

### Context JSON dùng làm gì cho model

`results/graph_context_<timestamp>.json` (schema: xem
`stage0_graph/context_export.py::build_context_dict`) chứa `functions` (id, tên,
file, dòng, **source code đầy đủ**, `func_rank`, `rank_source`
("dynamic"/"static_fallback"), `dynamic_time_pct`, `dynamic_call_count`,
`calls_raw`), `call_edges` (PCG, có thêm `count`/`time_contribution_pct` khi
`build_mode=dynamic`), `files`/`import_edges` (PSG). Đây là dữ liệu **đưa
cho model** (qua `stage4_llm_transpile/model_backend.py::ModelBackend.generate_rust_code`)
để model thấy được 1 hàm nằm trong bối cảnh nào (nó gọi gì, bị gọi bởi gì,
file nào import file nào, và — ở chế độ động — hàm nào THẬT SỰ đáng tối ưu)
thay vì chỉ nhận 1 đoạn code rời rạc — giúp sinh code Rust chính xác và đúng
trọng tâm hơn.

**Hiện tại `stage6_benchmark/bench.py` KHÔNG tự động đưa context này vào lời gọi
model** — `stage0_graph/context_export.py` chỉ xuất file JSON. TODO khi hiện thực
codegen thật: đọc file JSON này trong `stage4_llm_transpile/model_backend.py`, chọn ra
phần liên quan (hàm cần dịch + các hàm nó gọi/bị gọi trong cùng file, dựa
vào `call_edges`) và nhét vào prompt gửi cho `ApiModelBackend`/
`LocalModelBackend`.

## Vì sao đo in-process thay vì subprocess

Cả 3 phiên bản (`python_pure`, `hybrid_pyo3`, `rust_pure`) đều được đo bằng
đúng `time.perf_counter()` **trong cùng 1 tiến trình Python**, lặp
warmup + N lần trên cùng 1 ảnh input (`stage6_benchmark/bench.py::_time_callable`).

`rust_pure` **trước đây** được đo bằng cách `bench.py` spawn 1 tiến trình con
chạy `versions/rust_pure/src/main.rs` (CLI), lặp N lần bên trong tiến trình
con đó. Cách này bị đổi vì **không cùng điều kiện** với 2 phiên bản còn lại:
use-case thật của đồ án là gọi hàm tiền xử lý ảnh **lặp lại nhiều lần trong
cùng 1 tiến trình training** (theo batch/epoch) — không phải chạy `rust_pure`
như 1 chương trình rời mỗi lần gọi. Đo qua subprocess sẽ cộng thêm chi phí
khởi động tiến trình/IPC không tồn tại trong use-case thật, làm sai lệch so
sánh tốc độ.

Vì vậy `rust_pure` giờ cũng expose hàm qua PyO3 (`versions/rust_pure/pyo3_ext/`,
build bằng `maturin develop --release`, giống hệt cách `hybrid_pyo3` đã làm)
và được `bench.py` gọi **trực tiếp trong tiến trình Python**, đo bằng
`time.perf_counter()` y hệt 2 phiên bản kia. `versions/rust_pure/src/main.rs`
(bản CLI, nhận argv, in `ITER <i> <ns>`) vẫn được **giữ nguyên** — hữu ích để
chạy/debug thuật toán Rust độc lập ngoài Python — nhưng không còn nằm trong
đường đo tốc độ của `bench.py`.

## Cấu trúc thư mục

```
benchmark/
├── README.md
├── config.yaml              # data, target (mode/source), graph, llm, decision_gate, benchmark
├── config_loader.py         # helper đọc config.yaml (dùng chung mọi package)
├── run_pipeline.py          # điều phối full chain 6 stage (xem "Hai cách chạy")
├── requirements.txt
├── input/
│   └── intake.py             # local path | URL GitHub -> Path local (git clone)
├── data/
│   ├── loader.py             # load ảnh theo data_source + validate/convert format
│   ├── sample_input/          # ảnh mẫu thật (chess.jpg, ...) đã tải sẵn
│   ├── reference_repo/        # 4 file .py gốc viraj7 -- ví dụ test mode=repo
│   └── cloned_repos/          # repo do intake.py clone về (đã .gitignore)
├── stage0_graph/              # Stage 0: PCG/PSG + FuncRank + xuất context JSON
│   ├── models.py               # dataclass FunctionNode/CallEdge/FileNode/ProgramGraph
│   ├── builder.py               # parse .py -> PCG+PSG TĨNH (tree-sitter, fallback ast)
│   ├── rank.py                   # FuncRank tĩnh (PageRank) + động (POLO Eq.1-2)
│   └── context_export.py          # xuất graph -> results/graph_context_<ts>.json
├── stage1_profiling/
│   └── dynamic_profiler.py   # PCG ĐỘNG (POLO): profiling qua Scalene/cProfile
├── stage2_decision_gate/
│   └── gate.py               # skip | suggest_numpy_vectorization | candidate
├── stage3_context_packaging/
│   └── packager.py           # context 1 hotspot (POLO Fig.5) cho Generator Agent
├── stage4_llm_transpile/
│   ├── model_backend.py       # Anthropic API | server local OpenAI-style
│   ├── generator_agent.py      # sinh Rust -> pyo3_ext/generated/<func>.rs
│   └── decision_agent.py        # accept/reject sau benchmark (num_agents=2)
├── versions/                  # 3 bản cài đặt được đem đo (không phải 1 stage)
│   ├── python_pure/pipeline.py                       # TODO: điền logic từ repo viraj7
│   ├── rust_pure/
│   │   ├── Cargo.toml, src/main.rs                    # bản CLI Rust độc lập (cargo build --release)
│   │   ├── pyo3_ext/{Cargo.toml, src/lib.rs}          # TODO: extension PyO3 -- bench.py đo qua đây
│   │   ├── pyo3_ext/generated/                         # BẢN NHÁP do LLM sinh (đã .gitignore)
│   │   └── pipeline.py                                  # wrapper Python gọi pyo3_ext in-process
│   └── hybrid_pyo3/{Cargo.toml, src/lib.rs, pipeline.py}  # TODO: hàm hotspot qua PyO3
├── stage6_benchmark/
│   ├── bench.py              # chọn hàm (cứng hoặc top-K FuncRank), đo, lưu kết quả
│   └── report.py             # bảng mean/median/std/speedup + whole-scope report
└── results/                  # kết quả tự sinh (raw/report/graph_context/whole_scope/pipeline)
```

Ghi chú: `config_loader.py` được đặt ở gốc `benchmark/` (không nằm trong
`stage6_benchmark/`) để mọi package (`data`, `versions`, `stage0_graph`,
`stage6_benchmark`, ...) import theo cùng một cách:
`from config_loader import load_config`.

## Cài đặt & chạy nhanh (dummy, end-to-end)

```bash
cd benchmark
pip install -r requirements.txt
python stage6_benchmark/bench.py
```

> **Ổ C hết dung lượng?** Nếu `pip install` báo lỗi `[Errno 28] No space left
> on device` dù ổ D còn trống — Python hệ thống (`C:\PythonXXX`) cài package
> vào `site-packages` trên ổ C. Tạo virtualenv trên ổ D để né:
> ```bash
> python -m venv D:\đồ_án\.venv
> D:\đồ_án\.venv\Scripts\pip.exe install -r requirements.txt
> D:\đồ_án\.venv\Scripts\python.exe stage6_benchmark/bench.py
> ```
> (đã dùng đúng cách này để tự kiểm thử `stage0_graph/` — xem `.gitignore` gốc repo
> đã loại trừ `.venv/`).

Lệnh trên sẽ: nạp ảnh (mặc định dùng `chess.jpg` thật đã tải sẵn trong
`data/sample_input/`) → chạy `python_pure` và `hybrid_pyo3` (dummy) → kiểm
tra extension PyO3 `rust_pure_ext` đã build chưa (nếu chưa, in cảnh báo và
**bỏ qua**, không báo lỗi) → in bảng kết quả ra terminal và lưu vào `results/`.

Muốn có số liệu `rust_pure` (dù vẫn là dummy logic), build extension PyO3 của
nó bằng `maturin` (**không phải** `cargo build --release` — lệnh đó chỉ build
bản CLI rời, `bench.py` không dùng):

```bash
pip install maturin
cd versions/rust_pure/pyo3_ext
maturin develop --release
```

Muốn có số liệu `hybrid_pyo3` chạy qua extension Rust thật (thay vì fallback
Python), build tương tự:

```bash
pip install maturin
cd versions/hybrid_pyo3
maturin develop --release
```

## Ánh xạ hàm gốc (repo viraj7 → scaffold)

Đã xác minh trực tiếp từ source code trên GitHub (không đoán). Tất cả 4 hàm
đều nhận input qua `cv2.imread(path, 0)` → **ảnh xám (grayscale), uint8, 2
chiều (H, W)**. Đây cũng chính là `EXPECTED_SPEC` trong `data/loader.py`.

| Hàm trong scaffold | File gốc trong repo viraj7 | Thuật toán gốc (tóm tắt) |
|---|---|---|
| `edge_det(im, sig, ar)` | `edge_detection.py::edge_det` | Gaussian smoothing X/Y → derivative of Gaussian → magnitude/orientation → non-max suppression → hysteresis thresholding. Repo ghi nhận `sig=1` cho kết quả tốt nhất. |
| `harris(im)` | `"harris corner detection.py"::harris` | Gaussian smooth(1.5) → Ix/Iy (filter2D) → Laplace(Ix), Laplace(Iy) → response Harris `R = det(H) - 0.04*trace(H)^2` → đánh dấu corner đỏ trên ảnh RGB. |
| `hess_corner_det(im, sig, th)` | `"hessian corner detection.py"::hess_corner_det` | Gaussian smooth(sig) → đạo hàm bậc 1 (Ix, Iy) và bậc 2 (Ixx, Iyy, Ixy) → eigenvalues ma trận Hessian mỗi pixel → đánh dấu corner nếu `|l1|>th` và `|l2|>th`. |
| `im_threshold(im)` | `image_entropy.py::im_threshold` | Histogram 256 bin → tối đa hoá entropy `H(A)+H(B)` theo ngưỡng T → nhị phân hoá nền/vật thể (`<T`→0, `>=T`→255). |

**Lưu ý quan trọng khi port logic thật** (đã ghi chi tiết hơn trong
docstring từng hàm ở `versions/python_pure/pipeline.py`):

1. Các hàm gốc **không `return`** — chúng gọi `cv2.imshow()`/`cv2.waitKey()`
   để hiển thị từng bước trung gian (cần GUI). Benchmark chạy **headless**,
   nên khi port bắt buộc phải xoá các lệnh `cv2.imshow`/`cv2.waitKey`/
   `cv2.destroyAllWindows` và thêm `return <kết quả cuối>`.
2. `"harris corner detection.py"` và `image_entropy.py` viết bằng **cú pháp
   Python 2** (`print bien` thay vì `print(bien)`) → phải sửa sang Python 3
   trước, nếu không sẽ `SyntaxError` khi import.
3. `imtools.py` bị cắt cụt khi crawl qua GitHub, cần tự mở file gốc trên
   GitHub để xem đầy đủ nếu cần dùng các hàm tiện ích trong đó.
4. `harris()` và `hess_corner_det()` trả về **ảnh RGB `(H,W,3)`** (ảnh gốc +
   overlay corner màu đỏ), khác số chiều với input `(H,W)` — các stub dummy
   trong scaffold đã trả đúng shape `(H,W,3)` cho 2 hàm này.

## Checklist trước khi có số liệu thật

1. **Điền logic 3 pipeline**
   - `versions/python_pure/pipeline.py`: port trực tiếp 4 hàm từ repo viraj7
     theo bảng ánh xạ ở trên (nhớ xoá `cv2.imshow`/`waitKey`, thêm `return`,
     sửa cú pháp Python 2 nếu có).
   - `versions/rust_pure/pyo3_ext/src/lib.rs`: viết lại đúng thuật toán đó
     bằng Rust thuần trong 4 hàm, sau đó `maturin develop --release` trong
     `versions/rust_pure/pyo3_ext/` (đây là bản `bench.py` dùng để đo tốc
     độ). Nếu muốn `versions/rust_pure/src/main.rs` (bản CLI rời) cũng phản
     ánh logic thật, cập nhật thêm ở đó — 2 file này là 2 crate Cargo độc
     lập, KHÔNG dùng chung code (xem TODO trong `pyo3_ext/src/lib.rs`).
   - `versions/hybrid_pyo3/src/lib.rs` + `pipeline.py`: xác định phần nào là
     hotspot (thường là vòng lặp per-pixel lồng nhau, vd phần tính
     eigenvalues/response mỗi pixel trong harris/hessian), viết phần đó bằng
     Rust trong `lib.rs`, build bằng `maturin develop --release`, rồi nối
     lời gọi thật trong `pipeline.py` (thay các `raise NotImplementedError`).
   - Cả 3 bản phải cho **cùng kết quả** (trong sai số làm tròn) trên cùng 1
     ảnh input — nên thêm bước so sánh output (vd `np.allclose`) trước khi
     tin tưởng số liệu tốc độ.

2. **Chọn/điền model backend** (`stage4_llm_transpile/model_backend.py`)
   - Chọn `model_backend.provider: api` hoặc `local` trong `config.yaml`.
   - Nhánh `api`: nối SDK thật (Anthropic/OpenAI/...) trong
     `ApiModelBackend.generate_rust_code`, đọc key từ biến môi trường đặt
     tên trong `model_backend.api.api_key_env` (mặc định `ANTHROPIC_API_KEY`).
   - Nhánh `local`: nối endpoint model nội bộ thật (Ollama/vLLM) trong
     `LocalModelBackend.generate_rust_code`.
   - Module này hiện **không** được `stage6_benchmark/bench.py` gọi tự động — đây là
     bước Stage 4 (LLM transpile) riêng (offline), dùng để tạo bản nháp Rust từ Python trước
     khi bạn tinh chỉnh tay và dán vào `versions/rust_pure` /
     `versions/hybrid_pyo3`.
   - Khi chạy `target.mode: file`/`repo`, đã có sẵn
     `results/graph_context_<timestamp>.json` (PCG/PSG/FuncRank/source code,
     xem mục [3 cấp độ benchmark](#3-cấp-độ-benchmark-function--file--repo))
     — TODO: đọc file này trong `model_backend.py` và nhét phần liên quan
     (hàm cần dịch + hàm nó gọi/bị gọi) vào prompt thay vì chỉ gửi 1 hàm rời.

3. **Kiểm tra tương thích dữ liệu thư viện với input của repo viraj7**
   - Đặt `data.source: library_dataset` trong `config.yaml`, thử lần lượt
     `library_dataset_name`: `camera`, `coins` (đã là grayscale uint8, khớp
     thẳng `EXPECTED_SPEC`) và `astronaut` (RGB uint8, sẽ bị
     `data/loader.py::validate_and_convert` tự convert sang grayscale — kiểm
     tra log `WARNING` để thấy rõ bước convert này).
   - Thử `library_dataset_backend: torchvision` để kiểm tra layout
     channel-first `(C,H,W)` cũng được convert đúng.
   - Đọc kỹ log khi chạy `python data/loader.py` — mọi lệch định dạng (ndim,
     dtype, layout kênh) đều được log rõ (`WARNING`) hoặc raise lỗi tường
     minh (`ValueError`) trong `validate_and_convert`, **không** bao giờ
     âm thầm trả kết quả sai.

## Ghi chú kỹ thuật

- **Cách đo:** cả 3 phiên bản đều đo bằng `time.perf_counter()` **trong cùng
  1 tiến trình Python** (có warmup), gọi hàm trực tiếp `iterations` lần
  (`stage6_benchmark/bench.py::_time_callable`) — xem lý do ở mục
  [Vì sao đo in-process thay vì subprocess](#vì-sao-đo-in-process-thay-vì-subprocess).
  `rust_pure` và `hybrid_pyo3` đều cần extension PyO3 đã build (`maturin
  develop --release`) để có số liệu thật; nếu chưa build, `bench.py` tự phát
  hiện qua `HAS_EXT` và bỏ qua (rust_pure) hoặc fallback dummy Python
  (hybrid_pyo3 — xem khác biệt trong docstring 2 file `pipeline.py`).
- **Nếu chưa build `rust_pure`/`hybrid_pyo3`:** `stage6_benchmark/bench.py` vẫn chạy
  hết, chỉ in cảnh báo và bỏ qua phần thiếu trong báo cáo — không crash.
- **Yêu cầu để build Rust:** cài [Rust toolchain](https://rustup.rs/) +
  `pip install maturin`, dùng cho cả `versions/rust_pure/pyo3_ext/` và
  `versions/hybrid_pyo3/`. `versions/rust_pure/src/main.rs` (bản CLI rời,
  không bắt buộc để bench chạy) build bằng `cargo build --release` thường,
  yêu cầu Rust >= 1.66 (dùng `std::hint::black_box`).
- **Giới hạn đã biết của scaffold:**
  - `config_loader.py` yêu cầu PyYAML (không tự viết YAML parser để tránh
    bug âm thầm) — đã có trong `requirements.txt`.
  - `data/loader.py` dùng Pillow để đọc ảnh mẫu thật (thay vì OpenCV) nhằm
    giảm phụ thuộc; nếu muốn khớp 100% hành vi `cv2.imread(path, 0)` của
    repo gốc, có thể đổi sang `cv2.imread`.
  - `versions/rust_pure/pyo3_ext/src/lib.rs` và `versions/rust_pure/src/main.rs`
    là 2 crate Cargo độc lập, cố ý KHÔNG dùng chung code (giữ `main.rs`
    nguyên vẹn làm CLI rời) — khi điền logic Rust thật phải cập nhật đồng bộ
    ở cả hai nếu muốn cả CLI lẫn bản đo tốc độ đều đúng thuật toán.
  - Cargo.toml của `rust_pure` (bản CLI, `src/main.rs`) không có dependency
    ngoài để build ngay không cần mạng; `pyo3_ext` (cả của `rust_pure` và
    `hybrid_pyo3`) yêu cầu tải crate `pyo3` từ crates.io lần đầu build.
