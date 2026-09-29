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
import json
import logging
import os
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
SELECTION_RULE = (
    "Sàng tối đa `screening_limit` repo đầu tiên theo THỨ TỰ TÊN. Giữ repo thoả "
    "CẢ HAI điều kiện: (1) bộ test Python gốc chạy được và pass >= 1 test "
    "(repo_status không thuộc {BASELINE_FAILED, INSTALL_FAILED, TIMEOUT}; "
    "bước sàng chạy KHÔNG có LLM nên ALL_HOTSPOTS_FAILED_COMPILE không thể "
    "xuất hiện ở đây), (2) có >= "
    "`min_replayable_hotspots` hotspot ghi + phát lại được đối số thật. Chọn "
    "`n_repos` repo đầu tiên thoả, cộng `n_backup_repos` repo dự phòng dùng để "
    "THAY khi một repo đã chọn bị INSTALL_FAILED ở lượt chạy chính."
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


def select_repos(cfg: dict, results_dir: Path, run_id: str, dry_run: bool = False) -> dict:
    """Sàng rồi chọn mẫu. Trả về dict đủ để ghi vào metadata."""
    from input.intake import IntakeError, resolve_dataset_repos

    exp = cfg.get("experiment") or {}
    limit = int(exp.get("screening_limit", 40))
    n_repos = int(exp.get("n_repos", 12))
    n_backup = int(exp.get("n_backup_repos", 3))
    min_replayable = int(exp.get("min_replayable_hotspots", 2))

    try:
        all_repos = resolve_dataset_repos((cfg.get("dataset") or {}).get("source_root", ""), 0)
    except IntakeError as exc:
        raise SystemExit(f"Không dùng được dataset:\n{exc}") from exc

    # Sắp theo TÊN (không theo kích thước/ngẫu nhiên): thứ tự tất định, ai chạy
    # lại cũng ra cùng danh sách ứng viên.
    candidates = sorted(all_repos, key=lambda p: p.name)[:limit]
    _banner(f"SÀNG LỌC -- {len(candidates)}/{len(all_repos)} repo ứng viên (chỉ Pha A-B)")
    print(f"  quy tắc: {SELECTION_RULE}")
    print(f"  cần {n_repos} repo + {n_backup} dự phòng, mỗi repo >= {min_replayable} "
          f"hotspot phát lại được\n")

    if dry_run:
        print("  --dry-run: KHÔNG sàng thật. Danh sách ứng viên theo thứ tự tên:")
        for i, r in enumerate(candidates, 1):
            print(f"    {i:>3}. {r.name}")
        return {
            "rule": SELECTION_RULE, "dry_run": True,
            "n_dataset_repos": len(all_repos),
            "candidates": [r.name for r in candidates],
            "selected": [], "backups": [], "rejected": {},
        }

    accepted: list[tuple[Path, str]] = []
    rejected: dict[str, str] = {}
    screened: list[dict] = []
    need = n_repos + n_backup

    for i, repo in enumerate(candidates, 1):
        print(f"  [{i}/{len(candidates)}] sàng {repo.name} ...", flush=True)
        row = screen_repo(cfg, repo, results_dir, run_id)
        screened.append({
            "label": row.get("label"), "repo_status": row.get("repo_status"),
            "seconds": row.get("_screen_seconds"),
            "counts": (row.get("funnel") or {}).get("counts") or {},
        })
        ok, why = evaluate_candidate(row, min_replayable)
        mark = "NHẬN" if ok else "loại"
        print(f"       {mark}: {why}  ({row.get('_screen_seconds')}s)")
        if ok:
            accepted.append((repo, why))
        else:
            rejected[repo.name] = why
        if len(accepted) >= need:
            print(f"\n  đã đủ {need} repo -- dừng sàng ở ứng viên thứ {i}.")
            break

    selected = [r for r, _ in accepted[:n_repos]]
    backups = [r for r, _ in accepted[n_repos:n_repos + n_backup]]

    print(f"\n  CHỌN ({len(selected)}): {[r.name for r in selected]}")
    print(f"  DỰ PHÒNG ({len(backups)}): {[r.name for r in backups]}")
    print(f"  LOẠI ({len(rejected)}): xem metadata.json để biết lý do từng repo")
    if len(selected) < n_repos:
        print(f"\n  CẢNH BÁO: chỉ chọn được {len(selected)}/{n_repos} repo trong "
              f"{len(candidates)} ứng viên. Tăng `experiment.screening_limit` "
              f"hoặc giảm `min_replayable_hotspots`. Vẫn chạy tiếp với số repo "
              f"hiện có -- KHÔNG tự nới quy tắc, vì nới sau khi xem kết quả là "
              f"chọn mẫu theo kết quả.")

    return {
        "rule": SELECTION_RULE,
        "dry_run": False,
        "n_dataset_repos": len(all_repos),
        "screening_limit": limit,
        "min_replayable_hotspots": min_replayable,
        "n_screened": len(screened),
        "candidates": [r.name for r in candidates],
        "screened": screened,
        "selected": [r.name for r in selected],
        "backups": [r.name for r in backups],
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

        if row.get("repo_status") == outcomes.INSTALL_FAILED and backups:
            sub = backups.pop(0)
            queue.append(sub)
            print(f"  {repo.name} bị INSTALL_FAILED -> thay bằng repo dự phòng "
                  f"'{sub.name}' (lỗi môi trường, không phải đặc điểm repo).")

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
