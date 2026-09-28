"""Script CON của PHA B/E -- chạy BÊN TRONG venv riêng của repo.

Chỉ được gọi bởi `stage1_profiling/replay.py::run_replay_checks`. Lý do phải
là tiến trình con trong venv repo: file `.pkl` chứa instance của các lớp thuộc
repo, mà cloudpickle pickle lớp import được THEO THAM CHIẾU -- chỉ unpickle
được ở nơi `import <module của repo>` chạy được.

Ba việc, theo đúng thứ tự:
  1. Phát lại 2 lần trên bản Python. Lệch -> NONDETERMINISTIC, loại hotspot
     khỏi so sánh correctness (không có chuẩn ổn định thì so với cái gì).
  2. Phân tầng kiểu từ ĐỐI SỐ THẬT (Tầng 1 kiểu gốc / Tầng 2 kernel / ngoài
     tầng -> UNSUPPORTED_KIND). Việc phân tầng do CODE quyết, không hỏi LLM.
  3. Nếu được truyền `--rust-targets`, so khớp từng phiên bản Rust
     (`rust_pure`, `hybrid_pyo3`) với bản Python trên CÙNG bộ lời gọi.

Kết quả ghi ra JSON cho tiến trình cha đọc. KHÔNG raise ra ngoài: mọi lỗi
được gói thành `reason` + `detail` của hotspot tương ứng.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

R_UNSUPPORTED_KIND = "UNSUPPORTED_KIND"
R_UNREPLAYABLE_ARGS = "UNREPLAYABLE_ARGS"
R_NONDETERMINISTIC = "NONDETERMINISTIC"
R_UNRESOLVABLE_IMPORT = "UNRESOLVABLE_IMPORT"
R_CORRECTNESS_FAILED = "CORRECTNESS_FAILED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rtol", type=float, default=1e-5)
    parser.add_argument("--atol", type=float, default=1e-8)
    parser.add_argument("--rust-targets", default=None)
    args = parser.parse_args()

    capture_dir = Path(args.capture_dir)
    index_path = capture_dir / "capture_index.json"
    if not index_path.exists():
        Path(args.out).write_text("[]", encoding="utf-8")
        print(f"[rtb-replay] không thấy {index_path}", file=sys.stderr)
        return 1

    index = json.loads(index_path.read_text(encoding="utf-8"))

    rust_targets: dict = {}
    if args.rust_targets and Path(args.rust_targets).exists():
        rust_targets = json.loads(Path(args.rust_targets).read_text(encoding="utf-8"))

    from stage1_profiling.deep_compare import classify_tier, deep_compare
    from stage1_profiling.replay import (
        _loads_map,
        _loads_seq,
        compare_runs,
        load_calls,
        replay_all,
        resolve_callable,
    )

    out: list[dict] = []
    for meta in index:
        name = meta["function_name"]
        record: dict = {
            "function_name": name,
            "reason": meta.get("reason"),
            "detail": meta.get("detail", ""),
            "n_calls": meta.get("n_calls_captured", 0),
            "observed_arg_types": meta.get("observed_arg_types") or [],
            "observed_kwarg_types": meta.get("observed_kwarg_types") or {},
            "tier": "",
            "tier_reason": "",
            "correctness": {},
            "io_examples": [],
        }

        # Hotspot đã bị loại từ lúc GHI (NOT_COVERED_BY_TESTS,
        # UNREPLAYABLE_ARGS, UNSUPPORTED_KIND...) -> giữ nguyên lý do đó.
        if meta.get("reason") or not meta.get("calls_path"):
            out.append(record)
            continue

        try:
            calls = load_calls(meta["calls_path"])
        except Exception as exc:  # noqa: BLE001
            record["reason"] = R_UNREPLAYABLE_ARGS
            record["detail"] = f"không unpickle được lời gọi đã ghi: {type(exc).__name__}: {exc}"
            out.append(record)
            continue

        try:
            py_fn = resolve_callable(
                meta["module"], meta["qualname"], meta.get("import_root")
            )
        except Exception as exc:  # noqa: BLE001
            record["reason"] = R_UNRESOLVABLE_IMPORT
            record["detail"] = f"{type(exc).__name__}: {exc}"
            out.append(record)
            continue

        # --- 2. Phân tầng kiểu từ đối số thật (dùng lời gọi đầu tiên) -------
        try:
            first = calls[0]
            sample_args = list(_loads_seq(first.get("args_pre")))
            sample_args += list(_loads_map(first.get("kwargs_pre")).values())
            tier, tier_reason = classify_tier(sample_args)
            record["tier"] = tier
            record["tier_reason"] = tier_reason
        except Exception as exc:  # noqa: BLE001
            record["tier"] = ""
            record["tier_reason"] = f"không phân tầng được: {exc}"

        # --- 1. Tất định? Phát lại 2 lần trên chính bản Python --------------
        try:
            run1 = replay_all(py_fn, calls)
            run2 = replay_all(py_fn, calls)
        except Exception:  # noqa: BLE001
            record["reason"] = R_UNREPLAYABLE_ARGS
            record["detail"] = "phát lại trên bản Python thất bại:\n" + traceback.format_exc(limit=3)
            out.append(record)
            continue

        # --- Ví dụ VÀO/RA cho prompt của Generator (PHẦN 2.2) ---------------
        # Phải dựng ở ĐÂY chứ không ở tiến trình cha: `repr()` của instance
        # thuộc lớp của repo chỉ gọi được nơi import được lớp đó.
        def _short(value, limit=200):
            try:
                text = repr(value)
            except Exception as exc:  # noqa: BLE001
                return f"<không repr được: {type(exc).__name__}>"
            return text if len(text) <= limit else text[:limit] + "...(cắt)"

        examples = []
        for i, call in enumerate(calls[:3]):
            try:
                a = _loads_seq(call.get("args_pre"))
                kw = _loads_map(call.get("kwargs_pre"))
                a_after = _loads_seq(call.get("args_post")) if call.get("args_post") else None
            except Exception:  # noqa: BLE001
                continue
            entry = {
                "function": name,
                "args_repr": [_short(x) for x in a],
                "kwargs_repr": {k: _short(v) for k, v in kw.items()},
                "exception": call.get("exception"),
            }
            if call.get("result") is not None:
                try:
                    import cloudpickle

                    entry["result_repr"] = _short(cloudpickle.loads(call["result"]))
                except Exception:  # noqa: BLE001
                    entry["result_repr"] = "<không đọc được>"
            else:
                entry["result_repr"] = "None"
            if a_after is not None:
                entry["args_after_repr"] = [_short(x) for x in a_after]
            examples.append(entry)
        record["io_examples"] = examples

        same, why = compare_runs(run1, run2, args.rtol, args.atol)
        if not same:
            record["reason"] = R_NONDETERMINISTIC
            record["detail"] = (
                f"phát lại 2 lần trên bản Python ra kết quả khác nhau -- {why}. "
                "Loại khỏi so sánh correctness vì không có chuẩn ổn định."
            )
            out.append(record)
            continue

        # --- 3. So khớp từng phiên bản Rust với bản Python ------------------
        targets = rust_targets.get(name) or {}
        for version, spec in targets.items():
            entry = {"status": "ERROR", "detail": "", "n_matched": 0, "n_samples": len(calls)}
            try:
                rust_fn = resolve_callable(
                    spec["ext_module"], spec.get("ext_func") or name, spec.get("ext_root")
                )
            except Exception as exc:  # noqa: BLE001
                entry["detail"] = f"không import được {spec.get('ext_module')}: {exc}"
                record["correctness"][version] = entry
                continue

            try:
                rust_run = replay_all(rust_fn, calls)
            except Exception:  # noqa: BLE001
                entry["detail"] = "phát lại trên bản Rust thất bại:\n" + traceback.format_exc(limit=3)
                record["correctness"][version] = entry
                continue

            mismatches: list[str] = []
            n_matched = 0
            for i, (ref, cand) in enumerate(zip(run1, rust_run)):
                if (ref["exception"] is None) != (cand["exception"] is None):
                    mismatches.append(
                        f"lời gọi #{i}: Python {'ném ' + str(ref['exception']) if ref['exception'] else 'chạy OK'}, "
                        f"Rust {'ném ' + str(cand['exception']) if cand['exception'] else 'chạy OK'}"
                    )
                    continue
                if ref["exception"] is not None:
                    # Cả hai đều ném lỗi: coi là khớp nếu cùng loại exception.
                    if ref["exception"].split(":")[0] == cand["exception"].split(":")[0]:
                        n_matched += 1
                    else:
                        mismatches.append(
                            f"lời gọi #{i}: loại exception khác nhau "
                            f"({ref['exception']} vs {cand['exception']})"
                        )
                    continue
                ok, why = deep_compare(ref["result"], cand["result"], args.rtol, args.atol)
                if not ok:
                    mismatches.append(f"lời gọi #{i} giá trị trả về: {why}")
                    continue
                ok, why = deep_compare(
                    ref.get("args_after"), cand.get("args_after"), args.rtol, args.atol
                )
                if not ok:
                    mismatches.append(f"lời gọi #{i} đối số sau lời gọi: {why}")
                    continue
                n_matched += 1

            entry["n_matched"] = n_matched
            if mismatches:
                entry["status"] = "MISMATCH"
                entry["detail"] = mismatches[0]
                entry["mismatches"] = mismatches[:10]
            else:
                entry["status"] = "MATCH"
                entry["detail"] = f"khớp trên toàn bộ {len(calls)} lời gọi thật"
            record["correctness"][version] = entry

        # Rust có mặt mà lệch -> ghi thẳng lý do cuối cho hotspot.
        statuses = {v: e["status"] for v, e in record["correctness"].items()}
        if statuses and any(s == "MISMATCH" for s in statuses.values()):
            record["reason"] = R_CORRECTNESS_FAILED
            record["detail"] = "; ".join(
                f"{v}: {record['correctness'][v]['detail']}"
                for v, s in statuses.items() if s == "MISMATCH"
            )

        out.append(record)

    Path(args.out).write_text(
        json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    print(f"[rtb-replay] đã xử lý {len(out)} hotspot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
