# AUDIT — Output của pipeline có đủ để so sánh CORRECTNESS và EFFICIENCY chưa?

**Phạm vi audit gốc:** chỉ đọc code + chạy thử với mock/dataset giả, không sửa file nào.
**Cập nhật 2026-09-29 (4 vòng):** vòng 4 chuẩn bị PILOT 2 -- đưa sửa tay từ pilot 1 vào code, PSG đầy đủ theo POLO Table 1, Decision Gate 3 tầng độc lập, nhiễu nền cho ablation -- xem mục *CHUẨN BỊ CHO PILOT 2* ngay dưới.

**Cập nhật 2026-09-29 (vòng 3):** vòng 3 sửa 3 vấn đề phát hiện từ LƯỢT CHẠY THỰC NGHIỆM ĐẦU TIÊN trên máy thuê (lệch API PyO3, nhãn trạng thái gộp, hàm test chiếm pool) -- xem mục *BA VẤN ĐỀ TỪ LƯỢT CHẠY THỰC NGHIỆM 1 ĐẦU TIÊN* ngay dưới, có trích nguyên văn lỗi `cargo check` thật.

**Cập nhật 2026-09-28 (2 vòng):** vòng 1 thi hành Pha 0→G sửa các lỗ hổng; vòng 2 chuẩn bị Thực nghiệm 1 (chọn hotspot, phễu, VACUOUS, ablation, profile/preflight/scripts) -- xem mục *CHUẨN BỊ THỰC NGHIỆM 1* ngay dưới. Chi tiết vòng 1 — xem mục *TRẠNG THÁI SAU KHI THI HÀNH PHA 0 → G* ngay dưới phần mở đầu.
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

---

---

---

# CHUẨN BỊ CHO PILOT 2 (Pha 0→6) — 2026-09-29

**23/23 test PASS** (`python tests/run_all.py`, 195s).

## Bảng trạng thái 6 pha

| Pha | Việc | Trạng thái | Bằng chứng |
|---|---|---|---|
| **0.1** | `scipy` vào `requirements.txt` | **ĐÃ SỬA** | `networkx.pagerank` cần scipy; pilot 1 phải cài tay trên máy thuê |
| **0.2** | `gdown` bỏ `--id` | **ĐÃ SỬA** | dòng gọi nay là `gdown "$DATASET_DRIVE_ID" -O "$ZIP"` |
| **0.3** | subprocess không treo chờ nhập | **ĐÃ SỬA** | `git ls-remote` vào repo không tồn tại **trả về sau 1.8s** (exit 128) thay vì treo |
| **1** | Cài phụ thuộc test | **ĐÃ SỬA** | tìm 7 tên file + extras `.[test]`/`.[dev]`, cài SAU `requirements.txt` |
| **2** | Resolve tên trùng | **ĐÃ SỬA** | 3 nhánh ưu tiên; không phân biệt được → cờ `AMBIGUOUS_NAME` |
| **3** | PSG đầy đủ theo POLO Table 1 | **ĐÃ SỬA** | 2 class, 2 biến toàn cục, 2 chiều cạnh kế thừa, 6 cạnh sở hữu; PSG rút gọn còn nguyên |
| **4** | Decision Gate 3 tầng | **ĐÃ SỬA** | AND logic, `graph_confidence`, phụ thuộc cấp hàm; `simple_label` tương thích ngược |
| **5** | Nhiễu nền ablation | **ĐÃ SỬA** | nhánh `graph2`, tự gắn `KHÔNG PHÂN BIỆT ĐƯỢC VỚI NHIỄU` |
| **6** | Test + báo cáo | **ĐÃ XONG** | 23/23 PASS, mục này |

---

## Pha 0.3 — lỗi treo im lặng, đáng viết vào Method

Ở pilot 1, repo `Shpota_github-activity-generator` làm cả lượt chạy dataset **đứng yên**: bộ test của nó gọi `git`, git hỏi username/password, và vì subprocess thừa hưởng stdin của tiến trình cha nên nó chờ nhập mãi. Chạy bằng `nohup` trên máy thuê thì không ai thấy prompt đó — chỉ thấy lượt chạy im lặng không tiến triển tới khi hết giờ.

Hai lớp chặn, vì `stdin` một mình không đủ:

* `stdin=subprocess.DEVNULL` ở **mọi** `subprocess.run` của code sản phẩm (10 file, test tự kiểm số lượng khớp nhau).
* Biến môi trường trong `build_child_env()`: `GIT_TERMINAL_PROMPT=0`, `GIT_ASKPASS=/bin/true`, `SSH_ASKPASS=/bin/true`, `GCM_INTERACTIVE=never`, `PIP_NO_INPUT=1`, `DEBIAN_FRONTEND=noninteractive`. Cần lớp này vì git có đường riêng: nó có thể mở `/dev/tty` hoặc gọi askpass helper, **bỏ qua stdin**.

Đã bổ sung cả `input/intake.py` (`git clone`) — đó thực ra là chỗ rủi ro treo cao nhất mà pilot 1 chưa chạm tới.

Test dùng lệnh **thật**: `git ls-remote https://github.com/khong-ton-tai/x` qua đúng cơ chế đó, và khẳng định nó trả về nhanh (< 55s) với exit code khác 0.

## Pha 2 — tên trùng không còn dùng nhầm âm thầm

Ở pilot 1, `__init__` khớp 4 node và bản cũ **lặng lẽ lấy node đầu tiên**. Nghĩa là đối số ghi từ `A.__init__` có thể bị đem so với bản Rust dịch từ `B.__init__` — sai mà không có dấu hiệu nào trong kết quả.

Ba nhánh ưu tiên trong `_pick_node`:

1. node **cùng file** với nơi hotspot được phát hiện;
2. `qualified_name` đủ điều kiện phân biệt (`module.ClassName.method`);
3. hết cách → giữ hành vi cũ (node đầu tiên, sắp theo id cho tất định) **nhưng** gắn `ambiguous_name=True` kèm `ambiguity_detail` liệt kê các node trùng, và ghi thẳng vào `repo_summary_*.json`.

Thông điệp thật khi chạy: *"tên `__init__` khớp 2 node không phân biệt được: `__init__@x.py:1`; `__init__@y.py:1`. Đã dùng node đầu tiên (x.py) — đối số ghi được và bản Rust có thể KHÔNG thuộc cùng một hàm."*

## Pha 3 — PSG đầy đủ, và ba tên gọi không còn lẫn nhau

`stage0_graph/psg.py` thêm node Class (tên, method, lớp cha) và Global variable (tên, nơi gán, có phải `TÊN_HẰNG`), cùng cạnh `superclassOf`/`subclassOf` và `hasmember`/`ismember` — đúng POLO Table 1.

**Đặt field riêng, không ghi đè.** `graph.classes`, `graph.global_vars`, `graph.inheritance_edges`, `graph.ownership_edges`, `graph.psg_backend` — đều có default nên mọi chỗ đang dựng `ProgramGraph` bằng 5 tham số cũ vẫn chạy. `files`/`import_edges` (PSG rút gọn) không bị chạm, vì `stage3_context_packaging` đang dùng chúng. Test khẳng định cả hai cùng còn.

**Lớp cha ngoài scope không bị bỏ.** `class C(unittest.TestCase)` sinh cạnh `subclassOf` với `resolved=False` và giữ nguyên tên `TestCase`. Thông tin "class này kế thừa TestCase" vẫn hữu ích cho prompt, chỉ là nó không trỏ tới node nào trong graph — đoán bừa thì tệ hơn.

**3.3** `packager` thêm `class_detail`: tên class, lớp cha (kèm cờ trong/ngoài scope và method của cha), và tách riêng *method cùng class mà hotspot gọi* với *method cùng class gọi hotspot*. Hai quan hệ đó nói hai điều khác nhau về việc có tách hàm ra được hay không. Hàm cấp module cho `class_detail = None` — không bịa ra.

**3.4** Ba tên gọi được phân biệt rõ trong code, comment và README (mục *Ba khác biệt so với POLO gốc*): **PCG** (call graph, tĩnh trước, làm giàu bằng runtime nếu có) — POLO thì luôn động qua Callgrind; **PSG rút gọn** (chỉ import) — POLO không có khái niệm này; **PSG đầy đủ** — mới là đối tác đúng nghĩa; và `funcrank_static`/`funcrank_dynamic` là khái niệm hệ thống tự thêm, **không map 1-1** vào PCG/PSG của POLO.

## Pha 4 — ba tầng độc lập, không cộng điểm

`stage2_decision_gate/gate3.py`. Ba tầng kiểm **đồng thời bằng AND**; một tầng fail là loại ngay:

| Tầng | Giá trị |
|---|---|
| `hotspot_level` | `HIGH` / `MEDIUM` / `LOW_CONFIDENCE` / `LOW` |
| `feasibility` | `FEASIBLE` / `TEST_ONLY` / `BLOCKED` |
| `confidence_level` | `HIGH` / `MEDIUM` / `LOW` |

Test khẳng định **không có bù trừ**: `HIGH` + `BLOCKED` + `HIGH` vẫn ra `REJECT_BLOCKED`; `LOW` + `FEASIBLE` + `HIGH` ra `KEEP_PYTHON`.

`LOW_CONFIDENCE` cố ý khác `LOW`. `LOW` = *"đo được và nó không nóng"*; `LOW_CONFIDENCE` = *"chưa đo được nên chưa biết"* → ra `REVIEW`, không phải `KEEP_PYTHON`. Gộp hai thứ đó là mất đúng thông tin cần cho quyết định.

**4.1 `graph_confidence`** (`stage0_graph/confidence.py`), công thức lấy trực tiếp từ code teammate để hai bên so số được với nhau: `edge_quality = exact_ratio + 0.6 * heuristic_ratio`. Để đếm được, `CallEdge` có thêm field `resolution` (`exact` | `heuristic`) và `FunctionNode` có `unresolved_call_count`. Lời gọi ra **ngoài** scope (stdlib, thư viện) không tính vào `unresolved` — nó không nói gì về chất lượng graph. **Nhãn này không loại repo nào**; loại repo vì graph xấu là chọn mẫu theo chất lượng công cụ của chính mình.

**4.2** `funcrank_static` và `funcrank_dynamic` giữ **song song**, cộng `rank_source`. Một hàm static cao + dynamic thấp là hàm được gọi khắp nơi nhưng rẻ — biết được điều đó mới ra quyết định đúng.

**4.3 phụ thuộc cấp HÀM** (`dependency_roots.py`) — sửa đúng lỗi chặn oan. Với file có `import cv2` và `import numpy as np`:

| hàm | roots | blocked |
|---|---|---|
| `def add(a,b): return a+b` | `[]` | **False** ← trước đây bị chặn oan |
| `def blur(i): return cv2.blur(i)` | `['cv2']` | True |
| `def norm(x): return np.sum(x)` | `['numpy']` | False |

Giới hạn đã biết, ghi ra để không ai tưởng là bug: phân tích tĩnh theo tên nên không bắt `getattr(cv2, "blur")`. Bỏ sót theo chiều **an toàn cho tốc độ** (đánh dấu ít blocked hơn thực tế) — chặn oan là lỗi tệ hơn, vì nó làm mất hẳn hotspot khỏi thực nghiệm.

**`translation_unit`**: `BATCH_CALLER` cho hàm `SELECT` gọi ≥ 10 000 lần mà < 10 µs/lần. Với hàm như vậy, chi phí vượt biên Python↔Rust mỗi lời gọi ăn hết phần tiết kiệm, nên thứ đáng dịch là **vòng lặp gọi**. Gợi ý cho Stage 4, không phải loại bỏ.

**4.5** `simple_label` map `SELECT`/`REVIEW` → `candidate`, `REJECT_BLOCKED` → `skip`, `KEEP_PYTHON` → `suggest_numpy_vectorization`, nên `run_pipeline.py` và `stage3_context_packaging` không phải sửa gì. Chi tiết ba tầng vẫn đủ trong `stages.stage2.gate3` và `graph_context_*.json`.

Chạy thật trên `repo_eta`: chọn đúng 5 hàm sản phẩm `['add','div','mul','sub','sum_list']`, 12 hàm test bị gán `TEST_ONLY`.

## Pha 5 — nhiễu nền

Nhánh thứ ba `graph2`: lượt sinh **độc lập thứ hai của chính nhánh `graph`** (prompt giống từng ký tự, chỉ khác seed), tối đa `noise_floor_max_hotspots=10` hotspot, **không** chạy lại baseline/capture.

Vì sao bắt buộc: nếu graph-vs-none bất đồng ở 3 hotspot mà graph-vs-graph cũng bất đồng ở 3, thì con số đầu không nói gì về context — nó chỉ nói LLM trả lời khác nhau giữa hai lần gọi. Thiếu nhiễu nền là cách dễ nhất để rút ra một "phát hiện" không tồn tại.

Báo cáo đặt hai con số **cạnh nhau** và tự gắn kết luận. Kiểm chứng: nhiễu 2 ≥ hiệu ứng 1 → `KHÔNG PHÂN BIỆT ĐƯỢC VỚI NHIỄU`, `conclusive=False`; hiệu ứng 3 > nhiễu 0 → `HIỆU ỨNG VƯỢT NHIỄU`, `conclusive=True`. Chưa chạy `graph2` → `available=False` kèm ghi chú rằng **chưa có cơ sở** kết luận.

`compare_two_arms()` dùng chung cho cả graph-vs-none và graph-vs-graph2 — hai hàm riêng là cách chắc chắn nhất để hai con số lệch nhau theo thời gian.

---

## Chỉ xác nhận được trên máy thuê

**BẮT BUỘC TRƯỚC KHI CHẠY HẾT PILOT 2: mục `pyo3_example_compiles` của `preflight` phải PASS.** Đây là bằng chứng duy nhất cho thấy lỗi PyO3 gốc (làm `compiled=0` ở cả 9 repo pilot 1) đã thật sự hết. **Chưa ai xác nhận bằng `cargo` thật** — máy dev không có Rust toolchain, nên khuôn mẫu trong prompt chỉ được đối chiếu với thông báo lỗi 0.22 và migration guide. Nếu mục đó hỏng thì **dừng ngay**: model bắt chước khuôn mẫu sai sẽ cho `COMPILE_FAILED` hàng loạt y như pilot 1.

Các mục khác:

17. **Phụ thuộc test cài xong có làm bộ test xanh hơn không.** Đã thêm 7 tên file + extras, nhưng chưa biết pilot 1 mất bao nhiêu repo vì lý do này.
18. **`GIT_ASKPASS=/bin/true` trên Linux thật.** Test chạy trên Windows với Git for Windows; đường askpass của Linux khác. Lệnh `git ls-remote` fail nhanh là dấu hiệu tốt nhưng chưa phải xác nhận trên chính repo `Shpota_github-activity-generator`.
19. **PSG đầy đủ trên repo thật.** Kiểm trên repo giả (2 class, 2 biến toàn cục). Chưa biết trên repo hàng trăm file thì `_resolve_ownership` khớp được bao nhiêu phần trăm method, và `psg_full` làm `graph_context_*.json` nặng thêm bao nhiêu.
20. **Gate 3 tầng có chọn ra hotspot khác pilot 1 không.** Với `build_mode: dynamic` của `pilot_linux`, `hotspot_level` sẽ dùng số liệu Scalene thật lần đầu — mọi lượt chạy tới nay đều `static`, nên nhánh `HIGH`/`MEDIUM` theo `direct_runtime_share_pct` chưa bao giờ được thực thi với dữ liệu thật.
21. **Nhiễu nền có lớn đến mức nào.** Nếu `options.seed` không làm Ollama tất định, nhiễu nền có thể lớn hơn hiệu ứng ở **mọi** chỉ số — và khi đó kết luận đúng của thực nghiệm là *"không đo được tác động của context với cỡ mẫu này"*. Đó là một kết quả hợp lệ, phải báo cáo như vậy chứ không đi tìm cách khác để có số đẹp.
22. **Chi phí thời gian của nhánh thứ ba.** `graph2` thêm tối đa 10 lời gọi LLM mỗi repo. Với 12 repo, `max_wall_hours=6` có thể không còn đủ.

---

# BA VẤN ĐỀ TỪ LƯỢT CHẠY THỰC NGHIỆM 1 ĐẦU TIÊN — ĐÃ SỬA (2026-09-29)

Lượt chạy đầu tiên trên máy Linux thuê (`run_20260928_150827`, 9 repo RepoTransBench) cho **0 hotspot MEASURED**. Mục này ghi nguyên nhân gốc, bằng chứng, và cách sửa — dùng trích dẫn trực tiếp cho phần Method của luận văn.

**22/22 test PASS** sau khi sửa (`python tests/run_all.py`, 81s).

## Số liệu lượt chạy đầu tiên

| Repo | status cũ | hotspot | replayable | tier | **generated** | **compiled** |
|---|---|---|---|---|---|---|
| 7ossam81_EvoloPy | NO_MEASURABLE_HOTSPOT | 30 | 9 | 9 | 3 | **0** |
| BBuf_onnx_learn | NO_MEASURABLE_HOTSPOT | 23 | 6 | 4 | 4 | **0** |
| LanceGin_haishoku | NO_MEASURABLE_HOTSPOT | 30 | 11 | 9 | 5 | **0** |
| OthersideAI_chronology | NO_MEASURABLE_HOTSPOT | 15 | 3 | 3 | 3 | **0** |
| Shpota_github-activity-generator | NO_MEASURABLE_HOTSPOT | 19 | 5 | 2 | 1 | **0** |
| TypeError_secure | NO_MEASURABLE_HOTSPOT | 22 | 18 | 16 | 5 | **0** |
| alastair_python-musicbrainzngs | NO_MEASURABLE_HOTSPOT | 30 | 24 | 22 | 5 | **0** |
| alingse_jsoncsv | NO_MEASURABLE_HOTSPOT | 21 | 6 | 6 | 5 | **0** |
| andreroggeri_pynubank | NO_MEASURABLE_HOTSPOT | 29 | 16 | 9 | 5 | **0** |

Đường ống chạy đúng tới bước sinh code ở **cả 9 repo** (31 hotspot có code Rust), rồi **không hotspot nào biên dịch được**. Một nguyên nhân duy nhất, chặn toàn bộ.

---

## VẤN ĐỀ 1 — Lệch phiên bản API PyO3 (**ĐÃ SỬA**)

### Nguyên nhân gốc

Crate sinh tự động ghim `pyo3 = "0.22"`, nhưng prompt **không hề nói phiên bản**, nên Generator Agent viết theo API PyO3 ≤ 0.20:

```rust
#[pyfunction]
fn clear_kernel(directives: &PyList) -> PyResult<()> {
    directives.clear();
    Ok(())
}

#[pymodule]
fn clear_rsext(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(clear_kernel, m)?)?;
    Ok(())
}
```

PyO3 0.21 đã chuyển sang API `Bound<'py, T>`; mọi tham chiếu trần không còn dùng được làm tham số `#[pyfunction]`/`#[pymodule]`.

### Bằng chứng — `cargo check` thật (nguyên văn)

```
error[E0599]: no method named `clear` found for reference `&PyList` in the current scope
 --> src/lib.rs:6:16
  |
6 |     directives.clear();
  |                ^^^^^ method not found in `&PyList`

error[E0277]: the trait bound `&PyList: pyo3::impl_::extract_argument::PyFunctionArgument<'_, '_>` is not satisfied
   --> src/lib.rs:5:29
    |
  5 | fn clear_kernel(directives: &PyList) -> PyResult<()> {
    |                             ^ the trait `PyClass` is not implemented for `&PyList`
    |
help: the following other types implement trait `PyFunctionArgument<'a, 'py>`
 40 | impl<'a, 'py, T: 'py> PyFunctionArgument<'a, 'py> for &'a Bound<'py, T>

error[E0599]: no method named `add_function` found for reference `&pyo3::types::PyModule`
error[E0277]: the trait bound `&pyo3::types::PyModule: WrapPyFunctionArg<'_, _>` is not satisfied
   = help: the following other types implement trait `WrapPyFunctionArg<'py, T>`:
             &pyo3::Bound<'py, pyo3::types::PyModule>
```

Chính thông báo lỗi chỉ ra kiểu đúng: `&'a Bound<'py, T>` và `&Bound<'py, PyModule>`.

### Cách sửa

Thêm hằng `PYO3_API_VERSION` cùng **hai khối bắt buộc** vào prompt của cả hai tầng ([generator_agent.py](stage4_llm_transpile/generator_agent.py)):

* `PYO3_API_RULES` — nêu rõ phiên bản, liệt kê kiểu đúng (`&Bound<'_, PyList/PyDict/PyAny>`), chữ ký `#[pymodule] fn ten(m: &Bound<'_, PyModule>) -> PyResult<()>` (không có `_py: Python`), các method có sẵn trên `Bound<PyList>`, và quy tắc dùng `call_method0("clear")` cho method không có sẵn.
* `PYO3_EXAMPLE` — **khuôn mẫu hoàn chỉnh** gồm ba trường hợp: kiểu gốc vào/ra, sửa đối số tại chỗ qua `&Bound<'_, PyList>`, và gọi method qua `call_method0`.

System prompt cũng bỏ ràng buộc chữ ký ảnh cứng (`Vec<u8>` + width/height) — ràng buộc đó chỉ đúng cho 4 hàm viraj7 ở chế độ legacy, và ở chế độ repo động nó dạy model sai.

Khối này có ở **cả hai nhánh ablation**: nó không phải context graph mà là điều kiện để code biên dịch được. Thiếu nó thì `compiled = 0` ở mọi nhánh và ablation không so được gì.

### Hai lớp chặn tái diễn

**Lớp 1 — test so khớp chuỗi**, chạy được ở máy không có Rust: [tests/test_pyo3_prompt_matches_version.py](tests/test_pyo3_prompt_matches_version.py) assert `crate_builder.PYO3_VERSION == generator_agent.PYO3_API_VERSION`, assert Cargo.toml sinh ra ghim đúng version đó, và quét **các khối ```rust** trong ví dụ mẫu lẫn trong prompt thật để chặn mọi mẫu API cũ. Tôi kiểm test có "răng" bằng hai phép thử âm: đặt lệch version → bắt được; đổi ví dụ mẫu về `&PyModule` + `_py: Python` → bắt được cả hai lỗi ở đúng số dòng.

**Lớp 2 — biên dịch thật trong preflight**: `preflight.check_pyo3_example_compiles()` dựng crate tạm từ chính `PYO3_EXAMPLE` + `CARGO_TOML_TEMPLATE` rồi chạy `cargo check`. Hỏng ở đây thì preflight **dừng lượt chạy** trước lời gọi LLM đầu tiên. Lý do phải có lớp này: nếu chính khuôn mẫu ta đưa vào prompt còn không biên dịch được, không có cơ sở nào để tin code model viết theo nó sẽ biên dịch được. Trên máy dev (không có cargo) mục này tự hạ xuống "bỏ qua, không bắt buộc".

> **Chưa xác nhận được trên máy dev:** khuôn mẫu mới được đối chiếu với chính thông báo lỗi của `cargo check` 0.22 ở trên và với migration guide của PyO3, nhưng **chưa biên dịch thật** vì máy dev không có cargo. `preflight` sẽ tự chứng minh điều đó trên máy thuê ngay bước đầu.

---

## VẤN ĐỀ 1B — Vòng sửa lỗi không nhận được nội dung lỗi (**ĐÃ SỬA**)

### Nguyên nhân gốc

`compile_and_classify()` chạy `cargo check --message-format=json`, nên chẩn đoán đầy đủ nằm trong field `rendered` của các message JSON trên **stdout**. Nhưng code cũ ưu tiên stderr:

```python
human = (proc.stderr or "").strip() or "\n".join(rendered)
```

Với `--message-format=json`, stderr chỉ còn **một dòng tóm tắt**:

```
error: could not compile `clear_rsext` (lib) due to 4 previous errors
```

Dòng đó là toàn bộ nội dung `{compiler_output}` mà `FIX_PROMPT_TEMPLATE` đưa cho Generator Agent, kèm yêu cầu *"Sửa ĐÚNG các lỗi trên"*. Agent được bảo sửa lỗi mà **không hề biết lỗi là gì**. Đó là lý do vòng sửa lỗi ở lượt chạy đầu không sửa được gì — nó không hỏng, nó bị bỏ đói thông tin.

### Cách sửa

Ưu tiên bản render từ stdout JSON, nối thêm stderr chỉ khi nó thêm thông tin mới, và nếu cả hai đều rỗng thì nói rõ thay vì đưa chuỗi rỗng vào prompt.

### Bằng chứng sau khi sửa

Test dùng lại **nguyên văn** lỗi E0599/E0277 ở trên, đóng gói theo định dạng JSON của cargo, với stderr chỉ có dòng tóm tắt. `compiler_output` giờ dài 562 ký tự và chứa: mã `error[E0599]`, mã `error[E0277]`, đoạn code `directives.clear();`, gợi ý `PyFunctionArgument`, **và** vẫn giữ dòng tóm tắt. Mã lỗi parse ra `['E0277', 'E0599']`, phân loại `TYPE_ERROR`.

---

## VẤN ĐỀ 2 — Một nhãn cho hai nguyên nhân khác nhau (**ĐÃ SỬA**)

Cả 9 repo nhận `NO_MEASURABLE_HOTSPOT`, nhãn đó đọc ra là *"không tìm được hotspot nào"* — sai hoàn toàn nguyên nhân. Thực tế đường ống chạy tốt tới bước sinh code; chỗ vỡ là prompt.

Thêm `outcomes.ALL_HOTSPOTS_FAILED_COMPILE`, gán khi `funnel.counts.generated > 0` **và** `funnel.counts.compiled == 0`. `NO_MEASURABLE_HOTSPOT` giữ nguyên nghĩa hẹp: rụng **trước** bước sinh code.

Phân biệt này quan trọng vì **cách sửa hoàn toàn khác nhau**: `NO_MEASURABLE_HOTSPOT` là vấn đề của khâu ghi/phát lại đối số hoặc phân tầng kiểu; `ALL_HOTSPOTS_FAILED_COMPILE` là vấn đề của prompt hoặc model.

Đã cập nhật: `REPO_STATUSES`, `decide_repo_status()` (nhận thêm `n_generated`/`n_compiled`), chú giải bảng tổng kết trong `report.build_repo_table()`, mô tả quy tắc chọn mẫu và `evaluate_candidate()` trong `run_experiment1.py`. Phễu giờ được dựng **trước** khi chốt status vì status cần số liệu của phễu.

Lỗi mức môi trường vẫn **thắng** nhãn mới: `BASELINE_FAILED`/`INSTALL_FAILED`/`TIMEOUT` được kiểm trước. Và `is_success()` không coi nhãn mới là thành công.

Kiểm chứng đầu-cuối trên `repo_eta` với cargo giả luôn trả lỗi API cũ: phễu `generated=5, compiled=0` → `repo_status = ALL_HOTSPOTS_FAILED_COMPILE`, exit code 2, và `detail` của từng hotspot mang mã lỗi thật `E0277`.

---

## VẤN ĐỀ 3 — Hàm test chiếm chỗ trong candidate_pool (**ĐÃ SỬA**)

### Bằng chứng định lượng

| Repo | pool | hàm test | % |
|---|---|---|---|
| BBuf_onnx_learn | 23 | 14 | **60%** |
| Shpota_github-activity-generator | 19 | 10 | **52%** |
| OthersideAI_chronology | 15 | 6 | **40%** |
| LanceGin_haishoku | 30 | 11 | **36%** |
| **Tổng 9 repo** | **219** | **41** | **18%** |

`BBuf_onnx_learn` có `funcrank_order` là `['div', 'mul', 'sub', 'identity', 'multiply', 'add', 'make_identity', 'sum_list', 'dummy_resize_func', 'test_placeholder', 'test_make_identity', ...]` — 14 tên `test_*`. Chúng nằm **cùng file** `tools/tool.py` với code sản phẩm, nên lọc theo đường dẫn `tests/` là không đủ.

Dịch hàm test sang Rust là vô nghĩa: nó không nằm trong đường chạy của người dùng, và nếu thay nó bằng Rust thì đổi luôn chính **oracle** của thí nghiệm.

### Cách sửa

[stage0_graph/test_filter.py](stage0_graph/test_filter.py), ba tiêu chí (khớp một là đủ): (a) đường dẫn trong `tests/`, `public_tests/`, `test/`, `spec/`; (b) tên khớp `test_*`, `*_test`, `setUp*`, `tearDown*`, `setup_method`...; (c) file import `pytest`/`unittest` **và** hàm không được file khác gọi — điều kiện thứ hai quan trọng, vì module sản phẩm có thể import pytest mà hàm của nó vẫn là code thật nếu nơi khác gọi tới.

Lọc áp dụng **trước khi xếp hạng**, qua tham số `exclude_ids` mới của `rank.top_k_functions()`. Hai quyết định thiết kế:

* **Không xoá node khỏi graph.** Stage 3 vẫn cần biết "hàm test nào gọi hotspot" làm context, và ví dụ vào/ra cũng lấy từ lời gọi của test. Chỉ danh sách ứng viên bị lọc.
* **Điểm FuncRank vẫn tính trên graph đầy đủ**, nên công thức không đổi — chỉ danh sách được chọn đổi.

Số liệu lọc ghi vào `stages.stage0.test_filter` (số hàm bị loại, tỉ lệ, phân loại theo lý do, danh sách tên) để kiểm được từ file kết quả.

### Bằng chứng sau khi sửa

`repo_eta` (fixture mô phỏng đúng `BBuf_onnx_learn`: hàm test cùng file với code sản phẩm, cộng một `tests/` riêng): 19 hàm → loại **14 (73.7%)**, pool còn đúng 5 hàm sản phẩm `['add', 'div', 'mul', 'sub', 'sum_list']`. Không hàm test nào lọt, không hàm sản phẩm nào bị loại oan, graph không bị sửa.

---

## Lỗi phụ phát hiện khi kiểm chứng

`tests/test_confounded.py` ghim `num_ctx=550`, con số calibrate theo độ dài prompt **cũ** (graph 620 / none 486 token). Khối hướng dẫn API PyO3 làm cả hai prompt vượt 550 nên cả hai bị cắt, và test fail vì lý do chẳng liên quan tới CONFOUNDED. Đã sửa để **tính ngưỡng động** từ chính hai prompt (giờ graph 1227 / none 1186 → `num_ctx=1206`), nên prompt đổi thì ngưỡng tự đổi theo.

---

## Bổ sung vào mục "chỉ xác nhận được trên máy thuê"

14. **Khuôn mẫu PyO3 mới có biên dịch được thật hay không.** Đã đối chiếu với thông báo lỗi 0.22 và migration guide, nhưng máy dev không có cargo. `preflight.check_pyo3_example_compiles()` là phép kiểm quyết định — **nếu mục này hỏng thì đừng chạy thực nghiệm**, vì model bắt chước khuôn mẫu sai sẽ cho `COMPILE_FAILED` hàng loạt như lượt trước.
15. **Vòng sửa lỗi có thật sự sửa được khi đã có chẩn đoán đầy đủ.** Nay agent nhận đủ mã lỗi + đoạn code + gợi ý, nhưng còn phải xem `DSR@1` thật là bao nhiêu.
16. **Tỉ lệ hotspot đi tới bước đo sau khi lọc hàm test.** Pool sạch hơn 18% chỗ, nhưng chưa biết điều đó chuyển thành bao nhiêu hotspot `MEASURED` thêm.

---

# CHUẨN BỊ THỰC NGHIỆM 1 (Phần 1→4) — 2026-09-28

Vòng này không sửa lỗ hổng mới mà **chuẩn bị chạy thật**: chọn hotspot cho đủ để có gì mà so, dựng phễu có mẫu số, chống "đúng một cách rỗng", thêm ablation đối chứng, và chuẩn hoá setup để máy thuê không phải sửa code.

## Kết quả kiểm chứng

**19/19 test PASS** (`python tests/run_all.py`, 78s). Toàn bộ test đã được dồn từ `.scratch/` vào `benchmark/tests/` và bỏ đường dẫn Windows hard-code.

| Kiểm chứng | Kết quả |
|---|---|
| Phễu có mẫu số từng bước | 23 hotspot → 7 phát lại được (30%) → 5 accepted (22%) |
| Hai prompt ablation chỉ khác khối context | 4/4 cặp: **chỉ thêm**, không xoá dòng nào |
| Hai nhánh cách ly | crate riêng `.rtb_crates_graph` / `_none`, gỡ cài có **xác minh** `still_importable` rỗng |
| VACUOUS | `tally` `rust_call_count=0` → vacuous, loại khỏi regression-free; `always_used` gọi 2× → không vacuous |
| CONFOUNDED | `num_ctx=550`: graph bị cắt 5×, none 1× → cả 5 bị loại khỏi so sánh |
| seed tất định | cùng (hotspot, arm, repeat) luôn ra cùng seed; hai nhánh khác seed; temperature `0.2` đồng nhất |
| `--resume` | lượt 2 gọi LLM **0 lần**; xoá `repos/*.json` mà giữ `arm_cache/` vẫn 0 lần |
| `max_wall_hours=0` | dừng ngay, exit **3**, vẫn ghi `report.md` |
| `--dry-run` | in kế hoạch, không chạy repo nào, có ghi quy tắc chọn mẫu |
| preflight thiếu cargo/Ollama | dừng, exit 1, mỗi mục kèm lệnh sửa |
| preflight trỏ nhầm `target_projects` | **chặn** — phát hiện 167 repo ở đó, đúng thứ phải chặn |
| Chế độ legacy | vẫn đo đúng 4 hàm viraj7, không lạc sang đường động |
| Tính di động | quét 51 file: không còn ổ đĩa / `/tmp` / `Scripts-python` viết cứng |

**Đo trên prompt thật:** nhánh `graph` dài hơn `none` (620 vs 486 token trung bình) — đúng chiều dự kiến, và đó chính là lý do phải có nhãn CONFOUNDED.

## Những gì thay đổi

**Chọn hotspot (1.1).** `graph.candidate_pool=30` + `graph.top_k_translate=5`. Công thức FuncRank **không đổi**, không có điểm cộng thủ công nào — chỉ mở rộng phạm vi xét rồi lọc bằng tiêu chí khách quan (phát lại được + thuộc Tầng 1/2), giữ 5 hotspot đầu **theo đúng thứ tự FuncRank**. Lý do loại từng hotspot được log và ghi vào `stages.hotspot_selection.dropped` kèm vị trí FuncRank.

Lý do phải làm vậy: đo thật trên `piskvorky_sqlitedict` cho thấy **5/5 hotspot hạng cao bị loại**. Lấy thẳng top-5 thì thường còn 0 hotspot, và ablation không có gì để so.

**Phễu (1.2).** [funnel.py](funnel.py) — 10 bước, mỗi bước là tập con của bước trước, có `ratio_vs_prev` và `ratio_vs_first`. Mỗi đơn vị có mốc riêng: bước đơn vị *hotspot* so với `hotspot_found`, không so với số repo (chia ra sẽ là 460% vô nghĩa).

**Chống "đúng một cách rỗng" (1.3).** Plugin hoán đổi đếm `rust_call_count`. Bằng 0 → **VACUOUS**, loại khỏi `regression_free_rate` (mẫu số `n_regression_free_judged` ghi kèm). `repo_zeta` là fixture dựng cố ý cho trường hợp này.

**Ablation (Phần 2).** Hai nhánh sinh từ **một** hàm `build_signature_prompt`, khác nhau bởi cờ `include_graph_context`. Cả hai đều có source hotspot, chữ ký, kiểu đối số quan sát được và **ví dụ vào/ra thật** — mấy thứ đó là đặc tả tối thiểu để viết chữ ký PyO3, bỏ đi thì hai nhánh thành hai bài toán khác nhau. Prompt đầy đủ được dump ra `.rtb_prompts/` để `diff`. So sánh **theo cặp** (bảng 2×2), và **không kết luận thống kê** khi số cặp bất đồng < 10.

**Chuẩn hoá setup (Phần 3).** `config.yaml` chỉ còn mặc định trung tính; `profiles/pilot_linux.yaml` + `profiles/dev_windows.yaml` gộp đè, chọn bằng `RUN_PROFILE`/`--profile`. [preflight.py](preflight.py) kiểm 13 mục trước khi gọi LLM lần đầu. [run_experiment1.py](run_experiment1.py) là một lệnh chạy trọn. `scripts/` có 3 script không cần Docker.

## Bốn quyết định thiết kế đáng nêu

**Cách ly ablation bằng gỡ cài, không đổi tên module.** Tên `#[pymodule]` nằm **trong prompt**. Đổi nó theo nhánh thì hai prompt khác nhau ở *hai* biến chứ không phải một, và ablation mất giá trị. Nên hai nhánh dùng chung tên module, và `repo_runner.uninstall_extensions()` gỡ bản của nhánh trước ở 3 nơi (pip, `.so`/`.pyd` sót trong site-packages, shim `.py`) rồi **xác minh lại** bằng `find_spec`. Không tin vào việc đã gỡ — kiểm lại, vì nếu còn import được thì nhánh sau dùng nhầm bản build của nhánh trước và số liệu sai mà không báo lỗi.

**Không thay repo `BASELINE_FAILED` bằng repo dự phòng.** Chỉ thay khi `INSTALL_FAILED` — đó là lỗi môi trường. `BASELINE_FAILED` là **dữ liệu thật về dataset**; thay nó đi là chọn mẫu theo kết quả.

**`regression_free` giữ nghiêm ngặt, nhưng tách "fail" khỏi "skip".** Một test pass ở baseline mà **bị skip** ở lượt hybrid vẫn tính là mất (nghiêm ngặt), nhưng `regression_breakdown` ghi riêng `n_lost_now_failing` / `n_lost_now_skipped` / `n_lost_missing` và cả `regression_free_ignoring_skips`. Âm thầm bỏ qua test bị skip sẽ che đúng trường hợp bản Rust *gây ra* skip; không tách thì lại quy oan cho bản dịch.

**Seed hai nhánh cố ý KHÁC nhau.** Cùng seed mà prompt khác nhau thì không có ý nghĩa gì — seed chỉ cố định dòng ngẫu nhiên cho *một* prompt. Ràng buộc cần có là "cùng (hotspot, arm, repeat) luôn ra cùng seed", tức chạy lại cho kết quả như cũ. Dùng SHA-256 thay `hash()` vì `hash()` của str bị ngẫu nhiên hoá theo tiến trình.

## Lỗi thật phát hiện vòng này

**`test_rebuild_measure.py` phụ thuộc thư mục ngoài repo.** Nó trỏ vào `.scratch/fake_ext/` nên hỏng ngay khi thư mục đó bị dọn. Đã sửa để tự dựng fixture trong thư mục tạm — test phải mang theo fixture của chính nó.

**Mock backend hỏng lần thứ ba** khi chữ ký `chat()` mở rộng (`model` → `num_ctx`/`think` → `temperature`/`seed`). Đã đổi **toàn bộ** mock sang `chat(self, messages, system=None, **kwargs)` để lần mở rộng sau không kéo theo sửa test nữa.

## Trạng thái lỗ hổng cũ còn lại

| # | Lỗ hổng | Trạng thái |
|---|---|---|
| 14 | `regenerate_latest()` glob `raw_*.json` không thấy `pipeline_raw_*.json` | **ĐÃ SỬA** — pattern `*raw_*.json`, sắp theo mtime, thông báo lỗi nêu cả hai dạng tên file |
| 12 | Không có số đo mức repo | **MỘT PHẦN, có chủ ý** — `duration_sec` của cả hai lượt test đã ghi, nhưng cố ý không trình bày như chỉ số hiệu năng: bộ test đo *tính đúng*, không phải workload |
| 15 | Không pin CPU | **MỘT PHẦN** — `load_average` ghi được trên POSIX (tức là **có** trên máy thuê); vẫn chưa có `taskset` và chưa có bước xác nhận Ollama idle trước khi đo |

Phát hiện mới ở vòng trước (FuncRank thiên về method của đối tượng có trạng thái) đã được **xử lý** bằng `candidate_pool` + lọc, thay vì sửa công thức FuncRank.

## Bổ sung vào mục "chỉ xác nhận được trên máy thuê"

Ngoài 8 mục đã liệt kê ở vòng trước, thêm:

10. **`options.seed` có thật làm Ollama tất định hay không.** Ablation giả định điều đó. Nếu không đúng thì `repeats: 1` chưa đủ và phải tăng số lần lặp — và bảng so sánh theo cặp hiện tại sẽ phản ánh nhiễu lấy mẫu chứ không phản ánh context.
11. **Gỡ cài extension `.so` thật giữa hai nhánh.** Trong test, "extension" chỉ là file `.py` nên việc gỡ dễ hơn thực tế. Trên máy thuê phải kiểm `still_importable` trong `stages.isolation` của nhánh thứ hai — nếu không rỗng thì **toàn bộ số liệu ablation của repo đó phải bỏ**.
12. **`setup_linux.sh` với Ollama là PID 1.** Nhánh mở bản riêng ở cổng 11435 chưa chạy thật lần nào.
13. **Thời gian thật cho 12 repo.** Ước từ 2 repo đã chạy (~17s/repo cho Pha A-B, không LLM/Rust). Có LLM + build Rust + 2 nhánh thì lâu hơn nhiều bậc; `max_wall_hours=6` là phỏng đoán chứ chưa có căn cứ đo.

---

# TRẠNG THÁI SAU KHI THI HÀNH PHA 0 → G

**Cập nhật:** 2026-09-28, sau khi sửa 2 lỗ hổng chặn + 3 vấn đề đo lường.
Phần audit gốc bên dưới được giữ NGUYÊN VĂN để đối chiếu.

## Kết quả kiểm chứng đã chạy

| Lượt kiểm chứng | Kết quả |
|---|---|
| Pha A+B trên `repo_gamma` (repo giả, 7 hàm) | **PASS** — cả 7 hàm ra đúng 1 lý do, đúng tầng |
| Pha G toàn bộ đường động, 5 repo giả | **PASS** — `repo_gamma`/`repo_epsilon`=PARTIAL, 3 repo còn lại=BASELINE_FAILED |
| Không có fallback âm thầm (Pha E) | **PASS** — Rust ném lỗi ⇒ 3/8 test fail, `REGRESSION_FREE=False` |
| Chế độ LEGACY (`target.mode=function`) | **PASS** — vẫn đo đúng 4 hàm viraj7, không lạc sang đường động |
| Exit code khi không đo được gì | **PASS** — exit code **2**, không còn báo thành công |
| 9 test hồi quy cũ trong `.scratch/` | **PASS** (sau khi thêm `repo_oracle.enabled=false` — xem *Ghi chú test* cuối mục) |
| **Pha A-B trên DATASET THẬT** (2 repo) | **CHẠY ĐƯỢC** — xem bảng ngay dưới |

### Pha A-B trên dataset thật (máy dev, không LLM, không cargo)

| Repo | Thời gian | repo_status | Bộ test baseline | Kết cục hotspot |
|---|---|---|---|---|
| `piskvorky_sqlitedict` | **20.3s** | `NO_MEASURABLE_HOTSPOT` | **95/95 pass** (9.1s) | 3× `UNREPLAYABLE_ARGS`, 1× `UNSUPPORTED_KIND` (generator), 1× `UNRESOLVABLE_IMPORT` |
| `JoshData_pdf-redactor` | **13.4s** | `BASELINE_FAILED` | **0/1 pass** (3.7s) | cả 5 hotspot bị loại cùng repo |

Đây là bằng chứng trực tiếp cho hai điều:

* **Oracle mức repo hoạt động thật.** `sqlitedict` chạy 95/95 test qua venv riêng do pipeline tự dựng — tức là Pha A làm được đúng việc mà audit gốc nói là lỗ hổng chính.
* **`BASELINE_FAILED` không phải tình huống giả định.** `pdf-redactor` fail sẵn 1/1 test khi chưa ai chạm tới Rust. Trước Pha A, lỗi đó sẽ bị tính cho bản hybrid.

**PHÁT HIỆN MỚI (chưa có trong audit gốc), mức TRUNG:** FuncRank chọn hotspot thiên về **method của đối tượng có trạng thái**, mà đó đúng là nhóm khó dịch nhất. Trên `sqlitedict`, 3/5 hotspot là method của `SqliteMultithread` (giữ `_contextvars.Context`, `traceback`) nên không pickle được ⇒ không phát lại được ⇒ bị loại. Hệ quả thực tế: trên dataset thật, tỉ lệ hotspot đi được tới bước đo sẽ **thấp hơn nhiều** so với repo giả. *Đề xuất:* cho FuncRank cộng điểm cho hàm mà đối số đều là kiểu gốc (biết được sau một lượt Pha B thăm dò), hoặc lấy top-K lớn hơn rồi lọc theo tầng.

---

## Bảng chuyển trạng thái từng lỗ hổng

| # | Lỗ hổng (audit gốc) | Trạng thái | Sửa ở đâu / vì sao chưa sửa |
|---|---|---|---|
| **1** | Registry hard-code 4 hàm viraj7 ⇒ không đo được repo nào, pipeline vẫn báo thành công | **ĐÃ SỬA** | Pha C: [versions/registry.py](versions/registry.py) dựng registry ĐỘNG từ `module:qualname` của chính repo; [repo_pipeline.py](repo_pipeline.py) là đường chạy mới. Chứng minh: `repo_gamma` có 3 hotspot `MEASURED`. Chế độ legacy vẫn dùng registry cứng, không đổi. |
| **2** | Không có correctness mức repo, không có APR/SR | **ĐÃ SỬA** | Pha A+E: [stage5_compiler_in_the_loop/repo_runner.py](stage5_compiler_in_the_loop/repo_runner.py) chạy `pytest --junitxml`, parse ra `passed_ids`/`n_total`, tính `pass_rate`/SR; `dataset_apr_sr()` + `report.build_dataset_metrics_table()` cho APR/SR mức dataset; `baseline_failed()` gán `BASELINE_FAILED` và LOẠI repo khỏi mẫu. |
| **3** | Baseline Python không đo lại ở vòng ≥2 ⇒ so hai cách đo khác nhau | **ĐÃ SỬA** | Pha F: [stage6_benchmark/_pair_runner.py](stage6_benchmark/_pair_runner.py) đo `python_pure` VÀ mọi bản Rust trong **cùng một tiến trình**, cùng đối số, cùng vòng lặp `warmup+N`, ở **mọi** vòng. |
| **4** | `speedup` là MAX qua các vòng, có thể thuộc vòng `INITIAL` | **ĐÃ SỬA** | `HotspotRecord.accepted_speedup` + `accepted_round` = vòng được Decision Agent ACCEPT cuối cùng (phiên bản sẽ dùng thật). Best-of vẫn lưu nhưng ở khoá riêng `best_speedup_any_round`, và bảng ghi rõ nhãn. |
| **5** | `hybrid_pyo3` chưa từng được kiểm correctness | **ĐÃ SỬA** | [stage1_profiling/_replay_runner.py](stage1_profiling/_replay_runner.py) so khớp **từng phiên bản** trong `rust_targets`. Chứng minh: `sum_squares` và `accumulate_inplace` có `rust_pure=MATCH` **và** `hybrid_pyo3=MATCH`. |
| **6** | Không có metadata tái lập nào | **ĐÃ SỬA** | [env_metadata.py](env_metadata.py): CPU + số core, OS, Python, `rustc`/`cargo`/`maturin`/`uv`, git commit **kèm cờ `dirty`**, hostname, 2 tên model + 2 `num_ctx`, thời điểm, load average. Ghi vào `repo_summary_*.json`, `dataset_summary_*.json` và đầu `report_*_repos.md`. |
| **7** | Không có shape/dtype input ⇒ không diễn giải được speedup | **ĐÃ SỬA** | `_pair_runner._describe_input()` ghi `input_spec` = kiểu quan sát được từng đối số + số phần tử, vào `repo_summary_*.json`. |
| **8** | mean/median/std chỉ có trong stdout; `run_pipeline` không ghi `.md` | **ĐÃ SỬA** | `repo_pipeline._stats()` ghi `stats_ms` (mean/median/std/n) cho TỪNG phiên bản TỪNG vòng vào JSON; `_main_dynamic` ghi `report_<ts>_repos.md`; `bench_params` ghi `warmup`/`iterations`. |
| **9** | Stage 6 đo & gọi Decision Agent cho cả hotspot mà Gate đã gạt | **ĐÃ SỬA** | Hotspot bị gate nhận `GATE_SKIPPED` ngay ở Stage 2 và không vào danh sách `eligible`, nên không tốn lời gọi LLM và không lẫn vào bảng. |
| **10** | `correctness=ERROR` không chặn vòng tối ưu (chỉ `MISMATCH` chặn) | **ĐÃ SỬA** | Thay cơ chế: mọi hotspot có `reason` khác rỗng đều bị loại khỏi Pha D/E/F. Không còn đường nào cho hotspot không so được đi tiếp. |
| **11** | `rtol`/`atol` và shape sample không vào file; MISMATCH bị cắt còn 1 dòng | **ĐÃ SỬA** | Mỗi mục `correctness[version]` ghi kèm `rtol`, `atol`, `n_matched`, `n_samples`, và `mismatches` (tối đa 10 mục) thay vì chỉ `detail`. |
| **12** | Không có số đo mức repo (workload/test time) | **MỘT PHẦN — có chủ ý** | `baseline_tests.duration_sec` và `hybrid_tests.duration_sec` ĐÃ được ghi, nhưng cố ý **không** trình bày như chỉ số hiệu năng: bộ test RepoTransBench đo TÍNH ĐÚNG, không phải workload. Dùng nó làm số hiệu năng sẽ sai về phương pháp. Giới hạn này cần ghi vào luận văn. |
| **13** | Không có timeout per-repo | **ĐÃ SỬA** | `repo_oracle.repo_time_budget_sec` (mặc định 1800s) kiểm ở 3 chốt trong `repo_pipeline`, cộng `test_timeout_sec` cho mỗi lượt pytest; vượt ⇒ status `TIMEOUT`. |
| **14** | `report.regenerate_latest()` glob `raw_*.json` nên không thấy `pipeline_raw_*.json` | **CHƯA SỬA** | Ngoài phạm vi Pha 0→G và chỉ ảnh hưởng một tiện ích in lại báo cáo của chế độ legacy. Sửa 1 dòng: đổi pattern thành `*raw_*.json`. |
| **15** | Không pin CPU, không ghi tải máy lúc đo | **MỘT PHẦN** | `env_metadata` ghi `load_average` (chỉ có trên POSIX, tức là CÓ trên máy Linux thuê). Vẫn **chưa** có `taskset`/CPU affinity và chưa có bước xác nhận Ollama đã idle trước khi đo. |
| **16** | `pass_at_1=null` không phân biệt "Stage 5 tắt" với "thiếu cargo" | **ĐÃ SỬA** | Enum `HotspotReason` phân biệt `COMPILE_FAILED` / `BUILD_FAILED`, `stages.stage5.reason` ghi lý do bỏ qua, và `build_status` ghi riêng `BUILD_FAILED` vs `SKIPPED_NO_TOOLCHAIN`. |

## Hai lỗi thật phát hiện trong lúc thi hành (đã sửa)

1. **Mọi `subprocess.run(text=True)` giải mã stdout bằng cp1252 trên Windows.** Ngoại lệ `UnicodeDecodeError` bị thread đọc stdout nuốt, nên output của tiến trình con **mất âm thầm** mà không ai thấy lỗi. Ảnh hưởng cả `measure_subprocess.py` (parse JSON từ stdout). Đã thêm `encoding="utf-8", errors="replace"` cho cả 9 chỗ.
2. **Stage 0 quét cả venv mà pipeline vừa tạo.** `.rtb_venv` nằm trong bản copy của repo, nên FuncRank xếp hạng hàm nội bộ của pytest (`__init__` trùng ở **354 nơi**) thay vì hàm của repo — hotspot chọn ra không phải code cần dịch. Đã thêm `.rtb_venv`, `.rtb_capture`, `.rtb_crates` vào `_IGNORE_DIR_NAMES` của `stage0_graph/builder.py`.

## Ghi chú test

5 test hồi quy cũ trong `.scratch/` đặt `target.mode="repo"`, mà từ nay `mode=repo` mặc định đi đường ĐỘNG. Đã thêm `cfg["repo_oracle"]["enabled"] = False` vào cả 5 để chúng tiếp tục kiểm thử đúng đường chạy cũ. Ngoài ra `test_gen.py` có `FakeBackend` chỉ định nghĩa `generate()` — nó đã hỏng từ trước lượt này (khi `AgentSession` chuyển sang gọi `chat()`); đã thêm `chat(self, messages, system=None, **kwargs)` dùng `**kwargs` để lần mở rộng chữ ký sau không làm hỏng mock nữa.

## Chỉ xác nhận được trên máy Linux thuê

Mọi kết quả trên đạt được **không có** Rust toolchain, **không có** GPU, **không có** LLM thật. Những điều sau vẫn chưa kiểm chứng được:

1. **`cargo check` và `maturin develop` thật.** Máy dev không có `cargo`/`maturin` (metadata in ra `(không có)`). Trong Pha G, `compile_and_classify` và `build_crate` bị thay bằng bản giả: "extension Rust" thực chất là một module Python cùng tên hàm. Toàn bộ đường ống (import, so khớp, hoán đổi, đo, build lại giữa các vòng) đã được kiểm, nhưng **bản thân việc biên dịch Rust thì chưa**.
2. **Code Rust do LLM sinh có biên dịch được không, và Pass@1/DSR@1 thật là bao nhiêu.** Prompt Tầng 1/Tầng 2 và ánh xạ kiểu (`list[float]` → `Vec<f64>`, mutate tại chỗ → `&Bound<'_, PyList>`) mới chỉ được kiểm bằng backend giả. Đây là ẩn số lớn nhất còn lại.
3. **Tầng 2 (kernel + shim) với PyO3 thật.** Shim Python trong Pha G là do tôi viết tay trong test, không phải do LLM sinh. Chưa biết LLM có trả đúng khối `## Python shim` khớp chữ ký hàm gốc hay không.
4. **`maturin develop` cài extension vào venv RIÊNG của từng repo.** Cơ chế truyền `VIRTUAL_ENV` + `--interpreter` chưa chạy thật lần nào.
5. **Lời gọi LLM thật tới Ollama:** 2 model, `options.num_ctx`, `think: false`, lọc `<think>`, và liệu ~28GB VRAM có nạp nổi cả hai model cùng lúc.
6. **Toàn bộ 171 repo của dataset.** Đã chạy thật **2 repo** (33.7s tổng). Chưa biết: `repo_time_budget_sec=1800` có đủ cho repo lớn, `pip install -e .` fail ở bao nhiêu repo, và tỉ lệ hotspot đi được tới bước đo trên toàn dataset — mà phát hiện ở `sqlitedict` cho thấy tỉ lệ này sẽ thấp.
7. **`load_average`** chỉ có trên POSIX nên trên máy dev luôn `null`; chỉ máy Linux thuê mới ghi được.
8. **Profiling động (Scalene).** `graph.build_mode` để `static` trong mọi lượt chạy; đường `dynamic` (POLO Eq.1-2) không nằm trong phạm vi Pha 0→G.

---

## (a) Bảng checklist (AUDIT GỐC — giữ nguyên để đối chiếu)

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
