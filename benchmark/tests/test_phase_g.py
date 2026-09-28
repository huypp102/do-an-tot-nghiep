"""PHA G -- chay TOAN BO duong dong tren data/fake_dataset voi backend gia
va builder Rust gia (may dev khong co cargo/maturin).

Xac nhan 3 dieu theo yeu cau:
  1. Moi ham ra DUNG 1 ly do.
  2. Exit code KHAC 0 khi khong do duoc gi.
  3. Speedup giua cac vong KHAC nhau khi code khac nhau.
"""
import json
import logging
import os
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH))
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
from config_loader import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


def _work_root(tag: str) -> str:
    """Thư mục làm việc cho test, TRUNG TÍNH giữa Linux và Windows.

    Ưu tiên `RTB_WORK_DIR` (máy dev này ổ C: hay hết chỗ nên nên trỏ sang D:),
    không thì dùng thư mục tạm của hệ điều hành.
    """
    import os as _os
    import tempfile as _tf
    from pathlib import Path as _P

    base = _os.environ.get("RTB_WORK_DIR") or _tf.gettempdir()
    return str(_P(base) / tag)

# ---------------------------------------------------------------- BACKEND GIA
CALLS = []

# Cai "Rust" gia: mot module Python cai dat dung ham. Vong 1 dung ban CHAM,
# vong 2+ dung ban NHANH -> chung minh so do giua cac vong thuc su doi.
SLOW_IMPL = {
    "sum_squares": (
        "def sum_squares(values):\n"
        "    total = 0.0\n"
        "    for _ in range(40):\n"
        "        total = 0.0\n"
        "        for v in values:\n"
        "            total += float(v) * float(v)\n"
        "    return total\n"
    ),
    "accumulate_inplace": (
        "def accumulate_inplace(buffer, addend):\n"
        "    for _ in range(40):\n"
        "        pass\n"
        "    for i in range(len(buffer)):\n"
        "        buffer[i] += addend\n"
        "    return None\n"
    ),
    "checksum": (
        "def checksum(text):\n"
        "    total = 0\n"
        "    for _ in range(40):\n"
        "        total = 0\n"
        "        for i, ch in enumerate(text):\n"
        "            total = (total + (i + 1) * ord(ch)) % 1000003\n"
        "    return total\n"
    ),
    "count_vowels": (
        "def count_vowels(text):\n"
        "    n = 0\n"
        "    for _ in range(40):\n"
        "        n = sum(1 for ch in text.lower() if ch in 'aeiou')\n"
        "    return n\n"
    ),
}
FAST_IMPL = {
    "sum_squares": (
        "def sum_squares(values):\n"
        "    return float(sum(float(v) * float(v) for v in values))\n"
    ),
    "accumulate_inplace": (
        "def accumulate_inplace(buffer, addend):\n"
        "    buffer[:] = [x + addend for x in buffer]\n"
        "    return None\n"
    ),
    "checksum": (
        "def checksum(text):\n"
        "    total = 0\n"
        "    for i, ch in enumerate(text):\n"
        "        total = (total + (i + 1) * ord(ch)) % 1000003\n"
        "    return total\n"
    ),
    "count_vowels": (
        "def count_vowels(text):\n"
        "    return sum(1 for ch in text.lower() if ch in 'aeiou')\n"
    ),
}
# Tang 2: kernel nhan kieu goc, shim thao/dong goi doi tuong.
KERNEL_IMPL = {
    "scale_point": (
        "def scale_point_kernel(x, y, factor):\n"
        "    return (x * factor, y * factor)\n"
    ),
}
SHIM_IMPL = {
    "scale_point": (
        "from mypkg.models import Point\n"
        "import scale_point_rsext\n"
        "\n"
        "def scale_point(point, factor):\n"
        "    x, y = scale_point_rsext.scale_point_kernel(point.x, point.y, factor)\n"
        "    return Point(x, y, point.label)\n"
    ),
}


class MockBackend:
    name = "mock"

    def chat(self, messages, system=None, **kwargs):
        # **kwargs co Y DINH: chu ky chat() cua backend that da mo rong
        # 3 lan (model -> num_ctx/think -> temperature/seed). Liet ke tung
        # tham so o mock nghia la moi lan mo rong lai hong het test.
        role = "decision" if (system and "ĐÁNH GIÁ" in system) else "generator"
        text = messages[-1]["content"] if messages else ""
        CALLS.append({"role": role, "model": kwargs.get("model"),
                      "num_ctx": kwargs.get("num_ctx"),
                      "temperature": kwargs.get("temperature"),
                      "seed": kwargs.get("seed")})
        if role == "decision":
            # Luon CONTINUE -> phai dung dung o tran max_rounds.
            return ("## Decision\nACCEPT\n\n## Continue\nCONTINUE\n\n"
                    "## Next strategy\nDung iterator thay vi vong lap tay")
        # Suy ra ten ham tu prompt.
        name = ""
        for cand in list(SLOW_IMPL) + list(KERNEL_IMPL):
            if f"`{cand}`" in text:
                name = cand
                break
        if name in KERNEL_IMPL:
            return (
                "## Rust code\n```rust\n// kernel gia cho " + name + "\n```\n\n"
                "## Python shim\n```python\n" + SHIM_IMPL[name] + "```\n\n"
                "## Optimization strategy\nTach kernel."
            )
        return (
            "## Rust code\n```rust\n// ban dich gia cho " + (name or "?") + "\n```\n\n"
            "## Optimization strategy\nDich sang Rust."
        )

    def generate(self, prompt, model=None):
        return self.chat([{"role": "user", "content": prompt}], model=model)


# ------------------------------------------------------- BUILDER RUST GIA
import stage4_llm_transpile.model_backend as mb  # noqa: E402
import stage5_compiler_in_the_loop.compiler_loop as cl  # noqa: E402
import stage5_compiler_in_the_loop.crate_builder as cb  # noqa: E402

mb.get_model_backend = lambda cfg: MockBackend()

# cargo gia: luon bien dich OK ngay lan dau -> Pass@1 = 1.0
cl.cargo_available = lambda: True
cl.compile_and_classify = lambda crate_dir, timeout_sec=300: cl.CompileResult(ok=True)

_build_round = {}


def fake_build_crate(result, venv_python, timeout_sec=600):
    """Thay `maturin develop` bang viec ghi mot module Python dong vai extension.

    Kiem thu duoc TOAN BO duong ong (import, so khop, do toc do, build lai
    giua cac vong) ma khong can Rust toolchain.
    """
    name = result.function_name
    n = _build_round.get(name, 0) + 1
    _build_round[name] = n
    work_dir = Path(result.ext_root)

    if name in KERNEL_IMPL:
        body = KERNEL_IMPL[name]
    else:
        table = SLOW_IMPL if n == 1 else FAST_IMPL
        body = table.get(name)
        if body is None:
            result.status = cb.BUILD_FAILED
            result.output = f"builder gia khong co ban cai dat cho '{name}'"
            return result

    (work_dir / f"{result.ext_module}.py").write_text(
        f"# extension GIA (PHA G), vong build #{n}\n" + body, encoding="utf-8"
    )
    result.status = cb.BUILT_OK
    result.output = f"builder gia: vong build #{n}"
    return result


cb.build_crate = fake_build_crate
cb.toolchain_available = lambda: (True, "")

# ------------------------------------------------------------------- CHAY
import run_pipeline  # noqa: E402

# Chi chay cac repo gia ma test NAY dung lam fixture. `repo_zeta` la fixture
# cua test_ablation/test_confounded (kiem chung VACUOUS) nen khong thuoc pham vi
# o day -- de lan vao se lam test hong vi ly do chang lien quan.
import input.intake as _intake  # noqa: E402

_ONLY = {"repo_alpha", "repo_beta", "repo_delta", "repo_epsilon", "repo_gamma"}
_orig_resolve = _intake.resolve_dataset_repos
_intake.resolve_dataset_repos = lambda root, mx=0: [
    p for p in _orig_resolve(root, 0) if p.name in _ONLY
]

original_load = run_pipeline.load_config


def patched(*a, **k):
    cfg = original_load(*a, **k)
    cfg["llm"].update({"enabled": True, "backend": "local", "num_agents": 2})
    cfg["dataset"]["enabled"] = True
    cfg["dataset"]["source_root"] = str(BENCH / "data" / "fake_dataset")
    cfg["graph"]["top_k_hotspots"] = 25
    cfg["benchmark"].update({"iterations": 5, "warmup": 2})
    cfg["optimization_loop"]["max_rounds"] = 3
    cfg["decision_gate"]["enabled"] = False  # kiem thu duong ong, khong kiem thu gate
    cfg["repo_oracle"]["enabled"] = True
    # O tren D: -- o C: da het cho trong lan chay truoc.
    cfg["repo_oracle"]["work_root"] = _work_root("rtb_work_g")
    cfg["repo_oracle"]["keep_venv"] = False
    return cfg


run_pipeline.load_config = patched
code = run_pipeline.main()
print(f"\n\n### EXIT CODE = {code}")
print(f"### so loi goi LLM: {len(CALLS)}")

# ------------------------------------------------------------- KIEM CHUNG
print("\n" + "=" * 72)
print("KIEM CHUNG PHA G")
print("=" * 72)

results = BENCH / "results"
newest_ds = max(results.glob("dataset_summary_*.json"), key=lambda p: p.stat().st_mtime)
ds = json.loads(newest_ds.read_text(encoding="utf-8"))
ts = ds["timestamp"]

errs = []

# --- 1. status cua tung repo ---
EXPECT_STATUS = {
    "repo_alpha": "BASELINE_FAILED",   # khong co test
    "repo_beta": "BASELINE_FAILED",    # khong co test
    "repo_delta": "BASELINE_FAILED",   # test fail san
    "repo_epsilon": ("OK", "PARTIAL"),
    "repo_gamma": ("OK", "PARTIAL"),
}
print("\n-- status tung repo --")
for r in ds["repos"]:
    exp = EXPECT_STATUS.get(r["label"])
    got = r["repo_status"]
    ok = (got in exp) if isinstance(exp, tuple) else (got == exp)
    print(f"  {'OK ' if ok else 'SAI'} {r['label']:<16} {got:<24} (mong doi {exp})")
    if not ok:
        errs.append(f"{r['label']}: status {got}, mong doi {exp}")

# --- 2. moi hotspot ra dung 1 ly do ---
EXPECT_REASON = {
    "sum_squares": "MEASURED",
    "accumulate_inplace": "MEASURED",
    "scale_point": "MEASURED",
    "count_rows": "UNREPLAYABLE_ARGS",
    "unused_helper": "NOT_COVERED_BY_TESTS",
    "jittered_mean": "NONDETERMINISTIC",
    "walk_values": "UNSUPPORTED_KIND",
}
gamma_path = results / f"repo_summary_{ts}_repo_gamma.json"
print(f"\n-- ly do tung hotspot (repo_gamma) --")
if not gamma_path.exists():
    errs.append(f"thieu {gamma_path.name}")
else:
    gamma = json.loads(gamma_path.read_text(encoding="utf-8"))
    by_name = {h["function"]: h for h in gamma["hotspots"]}
    for name, exp in EXPECT_REASON.items():
        h = by_name.get(name)
        if h is None:
            print(f"  SAI {name:<20} THIEU khoi bang ket qua")
            errs.append(f"{name} thieu khoi bang")
            continue
        got = h["reason"]
        ok = got == exp
        print(f"  {'OK ' if ok else 'SAI'} {name:<20} {got:<24} tier={h.get('tier','-'):<14}")
        if not ok:
            errs.append(f"{name}: reason {got}, mong doi {exp}")
            print(f"       detail: {str(h.get('detail'))[:150]}")

    # --- 3. speedup giua cac vong phai KHAC nhau ---
    print("\n-- speedup theo tung vong (code vong 2 khac vong 1) --")
    for h in gamma["hotspots"]:
        if h["reason"] != "MEASURED" or len(h.get("rounds") or []) < 2:
            continue
        sps = [(r["round"], r.get("speedup", {})) for r in h["rounds"]]
        txt = "  ".join(
            f"v{n}:" + ",".join(f"{k}={v:.2f}x" for k, v in sp.items()) for n, sp in sps
        )
        print(f"  {h['function']:<20} {txt}")
        vals = [tuple(sorted(sp.items())) for _n, sp in sps if sp]
        if len(set(vals)) < 2:
            errs.append(f"{h['function']}: speedup giong nhau qua cac vong ({vals})")
            print(f"       !! speedup KHONG doi qua cac vong")
        builds = [r.get("build_status") for r in h["rounds"]]
        print(f"       build_status theo vong: {builds}")

    # --- correctness ca 2 phien ban ---
    print("\n-- correctness theo tung phien ban --")
    for h in gamma["hotspots"]:
        if h.get("correctness"):
            print(f"  {h['function']:<20} " + "  ".join(
                f"{v}={e.get('status')}" for v, e in h["correctness"].items()))

    # --- PHA E: test repo truoc/sau ---
    m = gamma.get("metrics") or {}
    print(f"\n-- PHA E: baseline_tests={m.get('baseline_tests')} "
          f"hybrid_tests={m.get('hybrid_tests')} "
          f"REGRESSION_FREE={m.get('regression_free')}")
    if m.get("hybrid_tests") is None:
        errs.append("PHA E khong chay duoc luot test hybrid")
    if m.get("regression_free") is not True:
        errs.append(f"REGRESSION_FREE={m.get('regression_free')} (mong doi True)")

    # --- metadata tai lap ---
    env = gamma.get("environment") or {}
    need = ["cpu", "python", "toolchain", "git", "llm", "os", "hostname"]
    missing = [k for k in need if not env.get(k)]
    print(f"\n-- metadata tai lap: {'day du' if not missing else 'THIEU ' + str(missing)}")
    if missing:
        errs.append(f"metadata thieu {missing}")
    print(f"   generator_model={env.get('llm', {}).get('generator_model')} "
          f"num_ctx={env.get('llm', {}).get('generator_num_ctx')}")
    print(f"   git commit={str(env.get('git', {}).get('commit'))[:12]} "
          f"dirty={env.get('git', {}).get('dirty')}")

# --- 4. exit code ---
print(f"\n-- exit code = {code} (mong doi 0 vi CO hotspot do duoc)")
if code != 0:
    errs.append(f"exit code {code}, mong doi 0")

print()
if errs:
    print(f"### PHA G: THAT BAI ({len(errs)} sai lech)")
    for e in errs:
        print(f"   - {e}")
    sys.exit(1)
print("### PHA G: PASS")
