"""PHẦN 3.4 -- THỰC NGHIỆM 1: một lệnh chạy trọn trên máy Linux thuê.

    python run_experiment1.py --profile pilot_linux

LUỒNG:
    preflight  -> dừng ngay nếu thiếu cargo/maturin/Ollama/dataset, để không
                  đốt giờ GPU rồi mới phát hiện.
    sàng lọc   -> chạy CHỈ Pha A-B (không LLM, không Rust) trên tối đa
                  `screening_limit` repo ứng viên, sắp theo TÊN.
    chọn mẫu   -> lấy `n_repos` repo ĐẦU TIÊN thoả QUY TẮC CỐ ĐỊNH, cộng
                  `n_backup_repos` repo dự phòng.
    chạy chính -> pipeline hai nhánh ablation cho từng repo đã chọn.
    báo cáo    -> phễu, bảng theo repo, APR/SR, ablation.

VỀ THIÊN LỆCH CHỌN MẪU (phải nêu trong luận văn): repo được chọn KHÔNG phải mẫu
ngẫu nhiên từ 171 repo. Chúng là những repo đầu tiên (theo thứ tự tên) mà bộ
test gốc chạy được VÀ có >= `min_replayable_hotspots` hotspot ghi/phát lại được.
Quy tắc này được CHỐT TRƯỚC khi xem kết quả và ghi nguyên văn vào metadata cùng
danh sách repo bị loại kèm lý do -- để người đọc tự đánh giá được mức thiên lệch
thay vì phải tin vào lời kể.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parent
if str(BENCHMARK_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ROOT))

import copy as _copy  # noqa: E402

from config_loader import ensure_utf8_stdio, load_config  # noqa: E402

ensure_utf8_stdio()
logger = logging.getLogger("benchmark.run_experiment1")

# QUY TẮC CHỌN MẪU -- chốt trước, ghi vào metadata, không đổi sau khi xem kết quả.
#
# LẦN CHẠY 4 (2026-09-30): đổi THỨ TỰ TÊN -> HOÁN VỊ NGẪU NHIÊN CÓ SEED CỐ ĐỊNH
# (`experiment.sample_seed`, xem `full_shuffle()`). SỬA LẦN 2 (cùng ngày): đổi
# từ "sàng phẳng rồi cân bằng miền SAU" sang "sàng 2 GIAI ĐOẠN theo miền" --
# giai đoạn 1 sàng TOÀN BỘ repo `ai_preprocessing` (trừ danh sách loại tay ở
# `selection/domain_exclusions.txt`) TRƯỚC, theo đúng thứ tự hoán vị; giai
# đoạn 2 mới sàng `general`. Lý do đổi: với 2 giai đoạn tách bạch, quota 5 repo
# ai_preprocessing được ưu tiên tối đa cơ hội đạt (sàng HẾT ~20 repo miền này
# nếu cần), thay vì phụ thuộc may rủi vị trí chúng rơi vào trong 1 hoán vị
# phẳng chung với general. Quy tắc NHẬN/LOẠI từng repo (`evaluate_candidate`)
# giữ NGUYÊN như 3 pilot trước.
SELECTION_RULE = (
    "HOÁN VỊ NGẪU NHIÊN CỐ ĐỊNH của toàn bộ dataset "
    "(random.Random(sample_seed).shuffle, ghi seed vào selection.json -- "
    "KHÔNG sắp theo tên). Tách hoán vị thành 2 dòng theo nhãn miền (bỏ các "
    "repo trong `selection/domain_exclusions.txt`): dòng ai_preprocessing và "
    "dòng general, MỖI DÒNG giữ nguyên thứ tự hoán vị gốc. SÀNG 2 GIAI ĐOẠN: "
    "(1) AI-FIRST -- sàng dòng ai_preprocessing theo thứ tự, DỪNG khi đủ 5 "
    "repo hợp lệ (quota tối đa) HOẶC hết dòng HOẶC chạm `screening_limit`; "
    "(2) GENERAL -- sàng tiếp dòng general theo thứ tự, DỪNG khi TỔNG hợp lệ "
    "(cả 2 giai đoạn) đủ `n_repos + n_backup_repos` HOẶC hết dòng HOẶC chạm "
    "`screening_limit`. Điều kiện NHẬN 1 repo (cả 2 giai đoạn): (a) bộ test "
    "Python gốc chạy được và pass >= 1 test (repo_status không thuộc "
    "{BASELINE_FAILED, INSTALL_FAILED, TIMEOUT}; bước sàng KHÔNG có LLM nên "
    "ALL_HOTSPOTS_FAILED_COMPILE không thể xuất hiện ở đây), (b) có >= "
    "`min_replayable_hotspots` hotspot ghi + phát lại được đối số thật. "
    "`n_repos` ĐẦU TIÊN trong danh sách hợp lệ (AI trước, general sau, đúng "
    "thứ tự tìm được) làm mẫu chính -- tối đa 5 trong đó là ai_preprocessing, "
    "ít hơn 5 thì lấy hết rồi bù general. Phần hợp lệ còn dư làm dự phòng, "
    "dùng để THAY khi một repo đã chọn bị INSTALL_FAILED ở lượt chạy chính -- "
    "KHÔNG phân biệt miền khi chọn dự phòng."
)


def _banner(title: str) -> None:
    print("\n" + "=" * 92)
    print(title)
    print("=" * 92)


# ---------------------------------------------------------------- sàng lọc
def screen_repo(cfg: dict, repo: Path, results_dir: Path, run_id: str) -> dict:
    """Chạy CHỈ Pha A-B cho 1 repo: không LLM, không Rust, không ablation.

    Dùng lại nguyên `run_repo_pipeline` với `llm.enabled=false`: nó sẽ đi hết
    copy → venv → Stage 0 → ghi đối số → phát lại → phân tầng rồi dừng ở Stage
    3/4 với lý do LLM_FAILED. Đúng phạm vi cần cho việc sàng, và không có bản
    logic thứ hai để lệch nhau.
    """
    from repo_pipeline import run_repo_pipeline

    scfg = _copy.deepcopy(cfg)
    scfg["llm"]["enabled"] = False
    scfg.setdefault("ablation", {})["enabled"] = False
    scfg.setdefault("compiler_loop", {})["enabled"] = False
    scfg["_resume"] = False
    scfg.pop("_arm_cache_dir", None)

    out_dir = Path(results_dir) / run_id / "screening"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        row = run_repo_pipeline(
            cfg=scfg, repo_path=repo, results_dir=out_dir,
            timestamp="screen", label=repo.name, benchmark_root=BENCHMARK_ROOT,
        )
    except Exception as exc:  # noqa: BLE001 -- 1 repo hỏng không dừng việc sàng
        logger.exception("Sàng repo '%s' lỗi bất ngờ.", repo.name)
        row = {"label": repo.name, "repo_status": "SCREEN_ERROR",
               "status_note": f"{type(exc).__name__}: {exc}", "funnel": {}}
    row["_screen_seconds"] = round(time.perf_counter() - t0, 1)
    return row


def evaluate_candidate(row: dict, min_replayable: int) -> tuple[bool, str]:
    """Áp QUY TẮC CỐ ĐỊNH lên kết quả sàng. Trả về (nhận?, lý do)."""
    status = row.get("repo_status", "?")
    # `ALL_HOTSPOTS_FAILED_COMPILE` cũng là loại: repo đã tới được bước sinh
    # code mà không biên dịch được thì đem vào lượt chính cũng không ra số
    # liệu. (Bước sàng không bật LLM nên thực tế không gặp, liệt kê cho đủ.)
    if status in ("BASELINE_FAILED", "INSTALL_FAILED", "TIMEOUT", "SCREEN_ERROR",
                  "ALL_HOTSPOTS_FAILED_COMPILE"):
        return False, f"{status}: {str(row.get('status_note') or '')[:160]}"
    counts = (row.get("funnel") or {}).get("counts") or {}
    n_replayable = int(counts.get("replayable") or 0)
    if n_replayable < min_replayable:
        return False, (
            f"chỉ {n_replayable} hotspot phát lại được (cần >= {min_replayable}); "
            f"tìm thấy {counts.get('hotspot_found', 0)} hotspot"
        )
    return True, f"{n_replayable} hotspot phát lại được"


# Tỉ lệ đạt ĐO ĐƯỢC ở pilot 1 (run_20260928_150827, 40 ứng viên đầu theo thứ
# tự TÊN trên dataset 171 repo -- pilot 1-3 sàng theo tên, LẦN CHẠY 4 đổi sang
# hoán vị ngẫu nhiên có seed, xem `shuffled_candidates()`). Dùng làm căn cứ
# cảnh báo, KHÔNG phải phỏng đoán -- coi tỉ lệ 24-30% là ĐẶC ĐIỂM DATASET (repo
# nào qua được sàng, không phải vị trí trong danh sách), nên áp dụng lại được
# cho một mẫu ngẫu nhiên cùng cỡ, dù thứ tự cụ thể sẽ khác.
#
#   trong 25 ứng viên đầu:  6 đạt (24%)
#   trong 30 ứng viên đầu:  9 đạt (30%)
#   trong 40 ứng viên đầu: 12 đạt (30%)
#
# Lý do bị loại: 16 BASELINE_FAILED, 9 không đủ hotspot phát lại được,
# 3 INSTALL_FAILED.
PILOT1_PASS_AT = {10: 3, 15: 4, 20: 4, 25: 6, 30: 9, 40: 12}
PILOT1_TOTAL_SCREENED = 40


def estimate_pass_count(limit: int) -> tuple[int, str]:
    """Số repo dự kiến qua sàng trong `limit` ứng viên đầu, theo số liệu pilot 1.

    Dùng mốc đo được gần nhất KHÔNG vượt `limit`; ngoài phạm vi đã đo thì suy
    theo tỉ lệ 30% của toàn bộ 40 ứng viên. Nói rõ cách suy ra để người đọc
    biết con số nào là đo và con số nào là ngoại suy.
    """
    exact = PILOT1_PASS_AT.get(limit)
    if exact is not None:
        return exact, f"đo được ở pilot 1: {exact}/{limit} ứng viên đầu qua sàng"

    known = [k for k in sorted(PILOT1_PASS_AT) if k <= limit]
    if known:
        base = known[-1]
        return PILOT1_PASS_AT[base], (
            f"mốc đo gần nhất ở pilot 1: {PILOT1_PASS_AT[base]}/{base} ứng viên đầu "
            f"qua sàng (chưa đo riêng cho {limit})"
        )
    rate = PILOT1_PASS_AT[PILOT1_TOTAL_SCREENED] / PILOT1_TOTAL_SCREENED
    return int(limit * rate), f"ngoại suy theo tỉ lệ {rate:.0%} của pilot 1"


def warn_if_screening_too_small(limit: int, n_repos: int, n_backup: int) -> list[str]:
    """Cảnh báo về cỡ mẫu, BA mức tuỳ quan hệ giữa "cần" và "ước lượng đạt".

    Ba mức, vì gộp chúng lại thì thông điệp sai với thực tế:
      * đạt > cần   -> ĐỦ, có dư. Một dòng thông báo là xong.
      * đạt == cần  -> SÁT NGƯỠNG. Về lý thuyết vừa đủ, nhưng KHÔNG dư sai số:
                       một repo đạt mà hỏng môi trường ở lượt chính là hết dự
                       phòng. Nói "có thể không đủ" ở đây là sai (lý thuyết là
                       đủ); nói "đủ" mà im lặng cũng sai (không có biên an toàn).
      * đạt < cần   -> CÓ THỂ KHÔNG ĐỦ.

    CỐ Ý KHÔNG tự tăng `screening_limit`: quy mô là quyết định của người chạy,
    và tự nới nó sau khi đã chốt là thay đổi mẫu mà không ai biết.
    """
    need = n_repos + n_backup
    expected, how = estimate_pass_count(limit)
    lines: list[str] = []

    if expected > need:
        lines.append(
            f"  Ước lượng: ~{expected} repo qua sàng trong {limit} ứng viên "
            f"({how}) -- đủ cho {n_repos} + {n_backup} dự phòng, dư "
            f"{expected - need} repo."
        )
        for line in lines:
            print(line)
        return lines

    if expected == need:
        lines += [
            "",
            "  " + "-" * 72,
            f"  CẢNH BÁO CỠ MẪU: SÁT NGƯỠNG, không dư ứng viên nào để loại thêm "
            f"nếu có repo INSTALL_FAILED mới xuất hiện.",
            f"    cần            : {n_repos} repo + {n_backup} dự phòng = {need}",
            f"    ước lượng đạt  : ~{expected} repo  ({how})",
            "",
            "  Về lý thuyết VỪA ĐỦ, nhưng không có biên an toàn: dự phòng chỉ bù",
            f"  được {n_backup} repo hỏng môi trường. Từ repo INSTALL_FAILED thứ "
            f"{n_backup + 1}",
            f"  trở đi sẽ không còn gì để thay, và pilot chạy với ít hơn {n_repos} repo.",
            "  Đó là hành vi ĐÚNG (chạy với số tìm được, ghi rõ trong metadata),",
            "  KHÔNG phải lỗi -- pipeline không tự hạ quy tắc và không báo lỗi im lặng.",
            "",
            "  Ước lượng dựa trên pilot 1, nơi 3/40 repo bị INSTALL_FAILED. Hai bản",
            "  sửa ở lượt trước có thể nâng tỉ lệ đạt (pilot 1 chạy khi chưa có chúng):",
            "    - cài thêm requirements-dev/test-requirements  -> cứu bớt BASELINE_FAILED",
            "    - lọc hàm test khỏi candidate_pool             -> cứu bớt 'ít hotspot'",
            "  " + "-" * 72,
            "",
        ]
        for line in lines:
            print(line)
        return lines

    lines += [
        "",
        "  " + "!" * 72,
        f"  CẢNH BÁO CỠ MẪU: screening_limit={limit} có thể KHÔNG đủ.",
        f"    cần            : {n_repos} repo + {n_backup} dự phòng = {need}",
        f"    ước lượng đạt  : ~{expected} repo  ({how})",
        "    lý do loại ở pilot 1: 16 BASELINE_FAILED, 9 không đủ hotspot phát",
        "                          lại được, 3 INSTALL_FAILED",
        "",
        "  KHÔNG tự tăng screening_limit -- quy mô là quyết định của người chạy.",
        "  Hai bản sửa ở lượt trước CÓ THỂ nâng tỉ lệ đạt (pilot 1 chạy khi chưa",
        "  có chúng):",
        "    - cài thêm requirements-dev/test-requirements  -> cứu bớt BASELINE_FAILED",
        "    - lọc hàm test khỏi candidate_pool             -> cứu bớt 'ít hotspot'",
        "  Nếu sau khi sàng thật mà không đủ, pipeline vẫn CHẠY TIẾP với số repo",
        "  tìm được và ghi rõ trong metadata -- không tự nới quy tắc.",
        "  " + "!" * 72,
        "",
    ]
    for line in lines:
        print(line)
    return lines


DEFAULT_DOMAIN_TAGS_CSV = BENCHMARK_ROOT / "selection" / "domain_tags.csv"
AI_DOMAIN_QUOTA = 5  # xem SELECTION_RULE: tối đa 5 repo ai_preprocessing trong n_repos.


def load_domain_tags(path: Path = DEFAULT_DOMAIN_TAGS_CSV) -> dict[str, str]:
    """Đọc `selection/domain_tags.csv` (ghi bởi `selection/scan_domains.py`) ->
    {tên repo: domain_label}. File chưa có (chưa chạy quét tĩnh) thì trả về
    dict rỗng -- gọi nơi dùng phải coi repo không có trong dict là 'unknown'
    (KHÔNG chặn pipeline, chỉ mất khả năng cân bằng miền)."""
    tags: dict[str, str] = {}
    if not path.exists():
        logger.warning(
            "Không thấy %s -- bỏ qua cân bằng miền (mọi repo coi như 'unknown', "
            "n_repos sẽ lấy đơn thuần theo thứ tự sàng). Chạy "
            "`python selection/scan_domains.py` trước để có cân bằng miền.", path,
        )
        return tags
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tags[row["repo"]] = row.get("domain_label", "unknown")
    return tags


DEFAULT_DOMAIN_EXCLUSIONS_TXT = BENCHMARK_ROOT / "selection" / "domain_exclusions.txt"


def load_domain_exclusions(path: Path = DEFAULT_DOMAIN_EXCLUSIONS_TXT) -> set[str]:
    """Đọc `selection/domain_exclusions.txt` -> tập tên repo bị LOẠI KHỎI
    TOÀN BỘ candidate pool (không sàng, dù nhãn miền là gì). Chỗ duy nhất cần
    sửa khi rà tay phát hiện repo gắn nhãn sai (vd `mgedmin_check-manifest`)
    -- KHÔNG cần sửa code. File chưa có thì coi như rỗng (không loại ai)."""
    if not path.exists():
        return set()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


def shuffled_candidates(all_repos: list[Path], seed: int, limit: int) -> list[Path]:
    """Hoán vị NGẪU NHIÊN CỐ ĐỊNH (seed ghi vào selection.json) của toàn bộ
    dataset, cắt lấy `limit` ứng viên đầu. `random.Random(seed)` độc lập với
    hash seed của tiến trình (PYTHONHASHSEED) nên cùng seed luôn ra cùng thứ
    tự, khác hẳn `sorted(..., key=name)` (thứ tự tên) dùng ở 3 pilot trước."""
    pool = sorted(all_repos, key=lambda p: p.name)  # nền tất định trước khi xáo
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:limit]


PHASE_AI_FIRST = "ai_first"
PHASE_GENERAL = "general"


def _screen_stream(
    cfg: dict, results_dir: Path, run_id: str, stream: list[Path], phase: str,
    min_replayable: int, budget_left: int, stop_when,
) -> tuple[list[tuple[Path, str, str]], dict[str, str], list[dict], str]:
    """Sàng 1 DÒNG ứng viên (AI hoặc general) cho tới khi `stop_when(n_ok_so_far)`
    trả True, hết `stream`, hoặc hết `budget_left` (ngân sách CÒN LẠI trong
    `screening_limit` tổng, dùng chung cho cả 2 giai đoạn).

    Trả về (accepted [(repo, lý_do_nhận, phase)], rejected {tên: lý_do_loại},
    screened [chi tiết từng repo, có "phase"], lý_do_dừng).
    """
    accepted: list[tuple[Path, str, str]] = []
    rejected: dict[str, str] = {}
    screened: list[dict] = []
    n_ok = 0
    for i, repo in enumerate(stream, 1):
        if stop_when(n_ok):
            return accepted, rejected, screened, "đủ quota giai đoạn"
        if len(screened) >= budget_left:
            return accepted, rejected, screened, f"chạm screening_limit (ngân sách còn {budget_left})"
        print(f"  [{phase}:{i}/{len(stream)}] sàng {repo.name} ...", flush=True)
        row = screen_repo(cfg, repo, results_dir, run_id)
        screened.append({
            "label": row.get("label"), "repo_status": row.get("repo_status"),
            "seconds": row.get("_screen_seconds"),
            "counts": (row.get("funnel") or {}).get("counts") or {},
            "phase": phase,
        })
        ok, why = evaluate_candidate(row, min_replayable)
        mark = "NHẬN" if ok else "loại"
        print(f"       {mark}: {why}  ({row.get('_screen_seconds')}s)")
        if ok:
            accepted.append((repo, why, phase))
            n_ok += 1
        else:
            rejected[repo.name] = why
    return accepted, rejected, screened, "đã sàng hết dòng"


def select_repos(cfg: dict, results_dir: Path, run_id: str, dry_run: bool = False) -> dict:
    """Sàng rồi chọn mẫu. Trả về dict đủ để ghi vào metadata."""
    from input.intake import IntakeError, resolve_dataset_repos

    exp = cfg.get("experiment") or {}
    limit = int(exp.get("screening_limit", 40))
    n_repos = int(exp.get("n_repos", 12))
    n_backup = int(exp.get("n_backup_repos", 3))
    min_replayable = int(exp.get("min_replayable_hotspots", 2))
    seed = int(exp.get("sample_seed", 42))
    need = n_repos + n_backup

    try:
        all_repos = resolve_dataset_repos((cfg.get("dataset") or {}).get("source_root", ""), 0)
    except IntakeError as exc:
        raise SystemExit(f"Không dùng được dataset:\n{exc}") from exc

    domain_tags = load_domain_tags()
    excluded = load_domain_exclusions()
    full_order = shuffled_candidates(all_repos, seed, len(all_repos))  # TOÀN BỘ, không cắt
    n_excluded = sum(1 for r in full_order if r.name in excluded)
    ai_stream = [r for r in full_order if r.name not in excluded
                 and domain_tags.get(r.name) == "ai_preprocessing"]
    general_stream = [r for r in full_order if r.name not in excluded
                       and domain_tags.get(r.name) != "ai_preprocessing"]

    _banner(f"SÀNG LỌC -- 2 giai đoạn AI-first / general (chỉ Pha A-B)")
    print(f"  seed hoán vị: {seed}  (random.Random(seed).shuffle trên {len(all_repos)} repo)")
    print(f"  loại tay (selection/domain_exclusions.txt): {n_excluded} repo -- {sorted(excluded)}")
    print(f"  dòng ai_preprocessing: {len(ai_stream)} repo | dòng general: {len(general_stream)} repo")
    print(f"  quy tắc: {SELECTION_RULE}")
    print(f"  cần {n_repos} repo + {n_backup} dự phòng (tối đa {AI_DOMAIN_QUOTA} "
          f"ai_preprocessing), mỗi repo >= {min_replayable} hotspot phát lại được")
    # Cảnh báo CÓ CĂN CỨ từ số liệu pilot 1. In ở cả `--dry-run` và lượt chạy
    # thật -- biết trước khi tốn giờ GPU mới có ích.
    warn_lines = warn_if_screening_too_small(limit, n_repos, n_backup)
    print()

    if dry_run:
        preview_general_n = max(0, limit - len(ai_stream))
        preview = [(r, "ai_preprocessing") for r in ai_stream] + \
                  [(r, domain_tags.get(r.name, "unknown")) for r in general_stream[:preview_general_n]]
        print("  --dry-run: KHÔNG sàng thật. Thứ tự dự kiến (AI-first, rồi general):")
        for i, (r, tag) in enumerate(preview, 1):
            print(f"    {i:>3}. {r.name}  [{tag}]")
        return {
            "rule": SELECTION_RULE, "dry_run": True,
            "seed": seed,
            "n_dataset_repos": len(all_repos),
            "screening_limit": limit,
            "n_repos": n_repos,
            "n_backup_repos": n_backup,
            "min_replayable_hotspots": min_replayable,
            "sample_size_warning": warn_lines,
            "n_excluded": n_excluded,
            "excluded_repos": sorted(excluded),
            "n_ai_preprocessing_candidates": len(ai_stream),
            "n_general_candidates": len(general_stream),
            "candidates": [r.name for r, _ in preview],
            "selected": [], "backups": [], "rejected": {}, "domain_composition": {},
            "phases": {},
        }

    # ---- GIAI ĐOẠN 1: AI-FIRST -- sàng TOÀN BỘ dòng ai_preprocessing trước,
    # dừng khi đủ quota AI_DOMAIN_QUOTA hợp lệ (không cần sàng hết nếu đã đủ).
    ai_accepted, ai_rejected, ai_screened, ai_stop = _screen_stream(
        cfg, results_dir, run_id, ai_stream, PHASE_AI_FIRST, min_replayable,
        budget_left=limit, stop_when=lambda n_ok: n_ok >= AI_DOMAIN_QUOTA,
    )
    print(f"\n  GIAI ĐOẠN 1 (AI-first) dừng: {ai_stop}. "
          f"{len(ai_accepted)}/{AI_DOMAIN_QUOTA} repo ai_preprocessing hợp lệ, "
          f"đã sàng {len(ai_screened)}/{len(ai_stream)}.\n")

    # ---- GIAI ĐOẠN 2: GENERAL -- sàng tiếp tới khi TỔNG (cả 2 giai đoạn) đủ
    # `need`, dùng phần ngân sách CÒN LẠI của screening_limit.
    budget_left_for_general = max(0, limit - len(ai_screened))
    general_accepted, general_rejected, general_screened, general_stop = _screen_stream(
        cfg, results_dir, run_id, general_stream, PHASE_GENERAL, min_replayable,
        budget_left=budget_left_for_general,
        stop_when=lambda n_ok: len(ai_accepted) + n_ok >= need,
    )
    print(f"\n  GIAI ĐOẠN 2 (general) dừng: {general_stop}. "
          f"{len(general_accepted)} repo general hợp lệ, "
          f"đã sàng {len(general_screened)}/{len(general_stream)}.\n")

    accepted = ai_accepted + general_accepted  # AI (<=5) trước, general sau -- ĐÚNG thứ tự cần cho selected[:n_repos]
    rejected = {**ai_rejected, **general_rejected}
    screened = ai_screened + general_screened

    selected_items = accepted[:n_repos]
    backup_items = accepted[n_repos:n_repos + n_backup]
    selected = [r for r, _, _ in selected_items]
    backups = [r for r, _, _ in backup_items]

    domain_composition = {
        "n_ai_preprocessing_accepted": len(ai_accepted),
        "n_general_accepted": len(general_accepted),
        "n_ai_preprocessing_selected": sum(1 for _, _, d in selected_items if d == PHASE_AI_FIRST),
        "n_general_selected": sum(1 for _, _, d in selected_items if d == PHASE_GENERAL),
        "ai_quota": AI_DOMAIN_QUOTA,
        "note": (
            f"chỉ tìm được {len(ai_accepted)}/{AI_DOMAIN_QUOTA} repo ai_preprocessing "
            "hợp lệ dù đã sàng TOÀN BỘ dòng ai_preprocessing -- đã lấy hết rồi bù "
            "bằng general, đúng quy tắc dự phòng, KHÔNG phải lỗi."
        ) if len(ai_accepted) < AI_DOMAIN_QUOTA else "",
    }

    print(f"  CHỌN ({len(selected)}): {[r.name for r in selected]}")
    print(f"    cân bằng miền: {domain_composition['n_ai_preprocessing_selected']} "
          f"ai_preprocessing + {domain_composition['n_general_selected']} general")
    if domain_composition["note"]:
        print(f"    LƯU Ý: {domain_composition['note']}")
    print(f"  DỰ PHÒNG ({len(backups)}): {[r.name for r in backups]}")
    print(f"  LOẠI ({len(rejected)}): xem metadata.json để biết lý do từng repo")
    if len(selected) < n_repos:
        print(f"\n  CẢNH BÁO: chỉ chọn được {len(selected)}/{n_repos} repo trong "
              f"{len(screened)} ứng viên đã sàng. Tăng `experiment.screening_limit` "
              f"hoặc giảm `min_replayable_hotspots`. Vẫn chạy tiếp với số repo "
              f"hiện có -- KHÔNG tự nới quy tắc, vì nới sau khi xem kết quả là "
              f"chọn mẫu theo kết quả.")

    return {
        "rule": SELECTION_RULE,
        "dry_run": False,
        "seed": seed,
        "n_dataset_repos": len(all_repos),
        "screening_limit": limit,
        "min_replayable_hotspots": min_replayable,
        "n_screened": len(screened),
        "n_repos": n_repos,
        "n_backup_repos": n_backup,
        "sample_size_warning": warn_lines,
        "n_excluded": n_excluded,
        "excluded_repos": sorted(excluded),
        "n_ai_preprocessing_candidates": len(ai_stream),
        "n_general_candidates": len(general_stream),
        "candidates": [s["label"] for s in screened],  # THỨ TỰ ĐÃ DUYỆT THẬT, không phải cả pool
        "phases": {
            "ai_first": {
                "n_candidates": len(ai_stream), "n_screened": len(ai_screened),
                "n_accepted": len(ai_accepted), "stop_reason": ai_stop,
            },
            "general": {
                "n_candidates": len(general_stream), "n_screened": len(general_screened),
                "n_accepted": len(general_accepted), "stop_reason": general_stop,
            },
        },
        "screened": screened,
        "selected": [r.name for r in selected],
        "backups": [r.name for r in backups],
        "domain_composition": domain_composition,
        "rejected": rejected,
        "_selected_paths": [str(r) for r in selected],
        "_backup_paths": [str(r) for r in backups],
    }


# ---------------------------------------------------------------- chạy chính
def run_main_phase(
    cfg: dict, selection: dict, results_dir: Path, run_id: str,
    max_wall_hours: float, resume: bool,
) -> list[dict]:
    """Chạy pipeline hai nhánh cho từng repo đã chọn, GHI NGAY khi xong từng repo."""
    from repo_pipeline import run_repo_pipeline

    run_dir = Path(results_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    per_repo_dir = run_dir / "repos"
    per_repo_dir.mkdir(exist_ok=True)

    cfg = _copy.deepcopy(cfg)
    cfg["_resume"] = resume
    cfg["_arm_cache_dir"] = str(run_dir / "arm_cache")

    queue = [Path(p) for p in selection.get("_selected_paths", [])]
    backups = [Path(p) for p in selection.get("_backup_paths", [])]
    # Giữ số ban đầu để báo cáo được "đã dùng hết bao nhiêu dự phòng".
    n_repos_configured = len(queue)
    n_backup_total = len(backups)
    exhausted_backups: list[str] = []
    timestamp = run_id

    rows: list[dict] = []
    t_start = time.perf_counter()
    budget = max_wall_hours * 3600.0
    stopped_early = False

    i = 0
    while i < len(queue):
        repo = queue[i]
        i += 1
        elapsed = time.perf_counter() - t_start
        if elapsed > budget:
            stopped_early = True
            logger.error(
                "Chạm mốc max_wall_hours=%.1fh sau %d/%d repo -- DỪNG và ghi kết "
                "quả dở dang (đã ghi từng repo xong nên không mất gì).",
                max_wall_hours, i - 1, len(queue),
            )
            break

        done_path = per_repo_dir / f"{repo.name}.json"
        if resume and done_path.exists():
            try:
                rows.append(json.loads(done_path.read_text(encoding="utf-8")))
                print(f"  RESUME | bỏ qua {repo.name} (đã có {done_path.name})")
                continue
            except (OSError, json.JSONDecodeError):
                logger.warning("%s có nhưng không đọc được -- chạy lại.", done_path.name)

        _banner(f"REPO {i}/{len(queue)}: {repo.name}   "
                f"(đã dùng {elapsed / 3600:.2f}h / {max_wall_hours}h)")
        try:
            row = run_repo_pipeline(
                cfg=cfg, repo_path=repo, results_dir=run_dir,
                timestamp=timestamp, label=repo.name, benchmark_root=BENCHMARK_ROOT,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Repo '%s' lỗi bất ngờ -- ghi nhận rồi chạy tiếp.", repo.name)
            import outcomes

            row = {"label": repo.name, "ok": False,
                   "repo_status": outcomes.NO_MEASURABLE_HOTSPOT,
                   "status_note": f"lỗi bất ngờ: {type(exc).__name__}: {exc}",
                   "metrics": {}, "hotspots": []}

        # GHI NGAY: lượt chạy có thể bị cắt bất cứ lúc nào.
        try:
            done_path.write_text(
                json.dumps(row, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Không ghi được %s: %s", done_path.name, exc)
        rows.append(row)

        # Repo hỏng MÔI TRƯỜNG -> thay bằng repo dự phòng. Chỉ thay cho
        # INSTALL_FAILED: đó là lỗi môi trường, không phải đặc điểm của repo.
        # BASELINE_FAILED thì KHÔNG thay -- nó là dữ liệu thật về dataset và
        # thay nó đi là chọn mẫu theo kết quả.
        import outcomes

        if row.get("repo_status") == outcomes.INSTALL_FAILED:
            if backups:
                sub = backups.pop(0)
                queue.append(sub)
                print(f"  {repo.name} bị INSTALL_FAILED -> thay bằng repo dự phòng "
                      f"'{sub.name}' (lỗi môi trường, không phải đặc điểm repo). "
                      f"Còn {len(backups)} dự phòng.")
            else:
                # HẾT DỰ PHÒNG. Nói rõ ngay tại chỗ: đây là lúc cỡ mẫu thật tụt
                # xuống dưới `n_repos`, và nếu im lặng thì bảng kết quả cuối sẽ
                # có ít repo hơn dự tính mà không ai biết vì sao.
                # KHÔNG crash, KHÔNG tự hạ quy tắc -- chạy tiếp với số tìm được.
                logger.error(
                    "Repo '%s' bị INSTALL_FAILED nhưng ĐÃ HẾT repo dự phòng "
                    "(n_backup_repos=%d đã dùng hết). Cỡ mẫu thật sẽ NHỎ HƠN "
                    "n_repos đã cấu hình. Pipeline chạy tiếp với số repo tìm "
                    "được -- xem `selection.json` và `metadata.json` để biết "
                    "chính xác đã chạy trên mấy repo.",
                    repo.name, n_backup_total,
                )
                exhausted_backups.append(repo.name)

    if exhausted_backups:
        print()
        print(f"  HẾT DỰ PHÒNG: {len(exhausted_backups)} repo bị INSTALL_FAILED mà "
              f"không còn repo nào để thay: {exhausted_backups}")
        print(f"  Cỡ mẫu thật = {len(rows)} repo đã chạy (n_repos cấu hình = "
              f"{n_repos_configured}, n_backup_repos = {n_backup_total}).")

    return rows, stopped_early


# ---------------------------------------------------------------------- báo cáo
def build_reports(cfg: dict, rows: list[dict], results_dir: Path, run_id: str) -> None:
    import ablation as ab
    import env_metadata
    import funnel as funnel_mod
    from stage6_benchmark import report as rp

    run_dir = Path(results_dir) / run_id
    funnels = []
    for r in rows:
        f = (r.get("funnel") or {})
        rf = funnel_mod.RepoFunnel(label=r.get("label", "?"))
        rf.baseline_pass = bool((f.get("counts") or {}).get("baseline_pass"))
        rf.hotspots = f.get("hotspots") or {}
        rf.drop_reasons = f.get("drop_reasons") or {}
        rf.vacuous = set(f.get("vacuous") or [])
        rf.confounded = set(f.get("confounded") or [])
        funnels.append(rf)

    agg = funnel_mod.aggregate(funnels)
    funnel_table = funnel_mod.format_funnel_table(agg, funnels)
    repo_table = rp.build_repo_table(rows)
    dataset_table = rp.build_dataset_metrics_table(rows)
    meta_text = env_metadata.format_for_report(env_metadata.collect(cfg, BENCHMARK_ROOT))

    parts = [meta_text, funnel_table, repo_table, dataset_table]

    if ab.is_enabled(cfg):
        per_arm: dict[str, dict] = {a: {} for a in ab.arms(cfg)}
        for r in rows:
            for arm, table in (r.get("ablation_arm_results") or {}).items():
                for name, raw in (table or {}).items():
                    per_arm.setdefault(arm, {})[f"{r.get('label')}::{name}"] = ab.ArmResult(
                        arm=arm, flags=raw.get("flags") or {},
                        reason=raw.get("reason", ""),
                        prompt_tokens=raw.get("prompt_tokens"),
                        truncated=bool(raw.get("truncated")),
                        fix_rounds=int(raw.get("fix_rounds") or 0),
                        llm_seconds=raw.get("llm_seconds"),
                        vacuous=bool(raw.get("vacuous")),
                        speedup=raw.get("speedup") or {},
                    )
        paired = ab.build_pairs(
            per_arm,
            min_discordant=int((cfg.get("ablation") or {}).get("min_discordant_pairs", 10)),
        )
        _md, _js, ab_text = ab.write_reports(paired, run_dir, run_id, cfg)
        parts.append(ab_text)

    for part in parts:
        print("\n" + part)

    (run_dir / "report.md").write_text("\n\n".join(parts), encoding="utf-8")
    (run_dir / "funnel.json").write_text(
        json.dumps({"aggregate": agg, "per_repo": [f.as_dict() for f in funnels]},
                   indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Thực nghiệm 1: sàng, chọn mẫu, chạy ablation 2 nhánh, báo cáo"
    )
    parser.add_argument("--profile", default=os.environ.get("RUN_PROFILE") or None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="chỉ in kế hoạch (ứng viên, quy tắc chọn), không chạy gì")
    parser.add_argument("--resume", action="store_true",
                        help="bỏ qua repo và cặp (repo, nhánh) đã xong")
    parser.add_argument("--max-wall-hours", type=float, default=None)
    parser.add_argument("--skip-preflight", action="store_true",
                        help="CHỈ dùng khi thử trên máy dev -- máy thuê đừng dùng")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
    cfg = load_config(profile=args.profile)
    results_dir = BENCHMARK_ROOT / (cfg.get("paths") or {}).get("results_dir", "results")
    run_id = args.run_id or time.strftime("run_%Y%m%d_%H%M%S")
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    exp = cfg.get("experiment") or {}
    max_wall_hours = (
        args.max_wall_hours if args.max_wall_hours is not None
        else float(exp.get("max_wall_hours", 6))
    )

    _banner(f"THỰC NGHIỆM 1 -- run_id={run_id}  profile={cfg.get('_profile') or '(mặc định)'}")
    print(f"  ngân sách thời gian : {max_wall_hours}h")
    print(f"  resume              : {args.resume}")
    print(f"  ablation            : {(cfg.get('ablation') or {}).get('enabled')}")
    print(f"  kết quả             : {run_dir}")

    # ---------------------------------------------------------- PREFLIGHT
    import preflight

    if args.skip_preflight:
        print("\n  BỎ QUA PREFLIGHT (--skip-preflight) -- chỉ hợp lệ trên máy dev.")
        checks = []
    else:
        _banner("PREFLIGHT")
        checks = preflight.run_all(cfg)
        print(preflight.format_report(checks))
        if any(c.required and not c.ok for c in checks):
            preflight.write_metadata(checks, cfg, results_dir, run_id)
            print("\nDỪNG: preflight hỏng. Sửa xong rồi chạy lại "
                  "(không mất gì, chưa gọi LLM lần nào).")
            return 1

    meta_path = preflight.write_metadata(checks, cfg, results_dir, run_id)

    # ----------------------------------------------------------- SÀNG LỌC
    sel_path = run_dir / "selection.json"
    if args.resume and sel_path.exists():
        selection = json.loads(sel_path.read_text(encoding="utf-8"))
        print(f"\n  RESUME | dùng lại kết quả chọn mẫu từ {sel_path.name}: "
              f"{selection.get('selected')}")
    else:
        selection = select_repos(cfg, results_dir, run_id, dry_run=args.dry_run)
        sel_path.write_text(
            json.dumps(selection, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # Ghi quy tắc + danh sách loại vào metadata: đây là THÔNG TIN VỀ THIÊN LỆCH
    # CHỌN MẪU, phải nêu trong luận văn.
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["selection"] = {k: v for k, v in selection.items() if not k.startswith("_")}
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Không cập nhật được metadata.json: %s", exc)

    if args.dry_run:
        print("\n--dry-run: dừng ở đây. Đã ghi kế hoạch vào "
              f"{sel_path.name} và {meta_path.name}.")
        return 0
    if not selection.get("_selected_paths") and not selection.get("selected"):
        print("\nDỪNG: không chọn được repo nào thoả quy tắc.")
        return 2
    if not selection.get("_selected_paths"):
        # Resume từ file cũ: dựng lại đường dẫn từ tên repo.
        root = Path((cfg.get("dataset") or {}).get("source_root", ""))
        selection["_selected_paths"] = [str(root / n) for n in selection.get("selected", [])]
        selection["_backup_paths"] = [str(root / n) for n in selection.get("backups", [])]

    # ---------------------------------------------------------- CHẠY CHÍNH
    _banner(f"CHẠY CHÍNH -- {len(selection['_selected_paths'])} repo, "
            f"{len(ablation_arms(cfg))} nhánh")
    rows, stopped_early = run_main_phase(
        cfg, selection, results_dir, run_id, max_wall_hours, args.resume
    )

    # ------------------------------------------------------------- BÁO CÁO
    _banner("BÁO CÁO")
    build_reports(cfg, rows, results_dir, run_id)

    total_measured = sum((r.get("metrics") or {}).get("n_measured", 0) for r in rows)
    n_ok = sum(1 for r in rows if r.get("ok"))
    print(f"\n  repo có số liệu dùng được : {n_ok}/{len(rows)}")
    print(f"  tổng hotspot MEASURED     : {total_measured}")
    print(f"  kết quả đầy đủ            : {run_dir}")
    if stopped_early:
        print(f"\n  DỪNG SỚM vì chạm max_wall_hours={max_wall_hours}h. "
              f"Kết quả các repo đã xong đã được ghi. Chạy lại với --resume "
              f"--run-id {run_id} để tiếp tục phần còn lại.")
        return 3
    if total_measured == 0:
        print("\n  KHÔNG hotspot nào đo được -- xem lý do từng hotspot trong "
              f"{run_dir}/repos/*.json")
        return 2
    return 0


def ablation_arms(cfg: dict) -> list[str]:
    import ablation as ab

    return ab.arms(cfg) if ab.is_enabled(cfg) else ["(không ablation)"]


if __name__ == "__main__":
    sys.exit(main())
