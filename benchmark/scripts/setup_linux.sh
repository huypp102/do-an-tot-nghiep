#!/usr/bin/env bash
# Dựng môi trường cho THỰC NGHIỆM 1 trên máy Linux thuê. KHÔNG cần Docker.
#
# IDEMPOTENT: chạy lại nhiều lần an toàn -- mọi bước đều kiểm tra trước khi làm.
# Trên máy tính tiền theo giờ, script phải chịu được việc bị cắt giữa đường rồi
# chạy lại, chứ không bắt dựng lại từ đầu.
#
# Cách dùng:
#     export DATASET_DRIVE_ID=<id file Google Drive chứa dataset>
#     bash scripts/setup_linux.sh
#
# Biến môi trường:
#     DATASET_DRIVE_ID   (bắt buộc nếu chưa có dataset) id file trên Drive.
#                        CỐ Ý không hard-code trong repo -- id là thứ thuộc về
#                        người chạy, không thuộc về source.
#     RTB_DATA_DIR       nơi giải nén dataset (mặc định ./data_repotransbench)
#     OLLAMA_PORT        cổng Ollama (mặc định 11434)
#     GENERATOR_MODEL / DECISION_MODEL   model để pull
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

RTB_DATA_DIR="${RTB_DATA_DIR:-$HERE/data_repotransbench}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
GENERATOR_MODEL="${OLLAMA_GENERATOR_MODEL:-devstral-small-2:latest}"
DECISION_MODEL="${OLLAMA_DECISION_MODEL:-qwen3:14b}"
VENV_DIR="$HERE/.venv"

log()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf '\n\033[31mLỖI: %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------- apt
log "1/7 Gói hệ thống"
APT_PKGS=(python3-venv python3-dev build-essential pkg-config libssl-dev git curl unzip)
MISSING=()
for p in "${APT_PKGS[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if [ ${#MISSING[@]} -eq 0 ]; then
  info "đã có đủ: ${APT_PKGS[*]}"
else
  info "còn thiếu: ${MISSING[*]}"
  SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq
  $SUDO apt-get install -y -qq "${MISSING[@]}"
fi

# ------------------------------------------------------------------ rustup
log "2/7 Rust toolchain"
if command -v cargo >/dev/null 2>&1; then
  info "cargo đã có: $(cargo --version)"
else
  info "cài rustup (không tương tác)"
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path
fi
# shellcheck disable=SC1091
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
command -v cargo >/dev/null 2>&1 || die "vẫn không thấy cargo. Thêm \$HOME/.cargo/bin vào PATH."
info "cargo: $(cargo --version) | rustc: $(rustc --version)"

# -------------------------------------------------------------------- venv
log "3/7 Virtualenv + phụ thuộc Python"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
  info "đã tạo $VENV_DIR"
else
  info "venv đã có: $($VENV_DIR/bin/python --version)"
fi
PY="$VENV_DIR/bin/python"
"$PY" -m pip install -q --upgrade pip
if [ -f "$HERE/requirements.txt" ]; then
  "$PY" -m pip install -q -r "$HERE/requirements.txt"
  info "đã cài requirements.txt"
else
  info "không có requirements.txt -- cài trực tiếp phụ thuộc tối thiểu"
  "$PY" -m pip install -q pyyaml numpy networkx tree-sitter tree-sitter-python cloudpickle pytest
fi
"$PY" -m pip install -q maturin gdown
info "maturin: $("$VENV_DIR/bin/maturin" --version 2>/dev/null || echo '(chưa thấy)')"

# ----------------------------------------------------------------- dataset
log "4/7 Dataset RepoTransBench"
SRC_DIR="$(find "$RTB_DATA_DIR" -maxdepth 3 -type d -name Python -path '*source_projects*' 2>/dev/null | head -1 || true)"
if [ -n "$SRC_DIR" ] && [ "$(find "$SRC_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l)" -gt 0 ]; then
  N=$(find "$SRC_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l)
  info "đã có dataset: $SRC_DIR ($N repo)"
else
  [ -n "${DATASET_DRIVE_ID:-}" ] || die \
    "chưa có dataset và DATASET_DRIVE_ID chưa đặt. Chạy:
     export DATASET_DRIVE_ID=<id file Drive>
   (id KHÔNG được hard-code trong repo -- nó thuộc về người chạy.)"
  mkdir -p "$RTB_DATA_DIR"
  ZIP="$RTB_DATA_DIR/dataset.zip"
  if [ ! -s "$ZIP" ]; then
    info "tải dataset bằng gdown"
    "$VENV_DIR/bin/gdown" --id "$DATASET_DRIVE_ID" -O "$ZIP"
  else
    info "đã có $ZIP -- bỏ qua bước tải"
  fi
  info "giải nén"
  unzip -q -o "$ZIP" -d "$RTB_DATA_DIR"
  SRC_DIR="$(find "$RTB_DATA_DIR" -maxdepth 4 -type d -name Python -path '*source_projects*' | head -1 || true)"
  [ -n "$SRC_DIR" ] || die "giải nén xong nhưng không tìm thấy source_projects/Python trong $RTB_DATA_DIR"
fi

N=$(find "$SRC_DIR" -maxdepth 1 -mindepth 1 -type d | wc -l)
if [ "$N" -ne 171 ]; then
  info "CẢNH BÁO: thấy $N repo, bản dataset dự kiến có 171."
  info "Không chặn, nhưng phải ghi rõ trong luận văn là chạy trên bản khác."
else
  info "đủ 171 repo"
fi
echo "$SRC_DIR" > "$HERE/.dataset_root"
info "đã ghi đường dẫn vào .dataset_root"

# ------------------------------------------------------------------ ollama
log "5/7 Ollama"
if ! command -v ollama >/dev/null 2>&1; then
  info "cài ollama"
  curl -fsSL https://ollama.com/install.sh | sh
fi

# Nếu đã có `ollama serve` chạy sẵn (hay gặp trên image thuê: nó là PID 1 và
# nghe 0.0.0.0), KHÔNG giết nó -- mở bản RIÊNG ở cổng khác. Giết tiến trình PID 1
# có thể làm sập cả container.
EXISTING_PID="$(pgrep -f 'ollama serve' | head -1 || true)"
USE_PORT="$OLLAMA_PORT"
if [ -n "$EXISTING_PID" ] && [ "$EXISTING_PID" = "1" ]; then
  USE_PORT=11435
  info "đã có 'ollama serve' làm PID 1 -> mở bản riêng ở cổng $USE_PORT"
elif [ -n "$EXISTING_PID" ]; then
  info "đã có 'ollama serve' (pid $EXISTING_PID) -- dùng lại, không khởi động thêm"
fi

OLLAMA_BASE_URL="http://127.0.0.1:$USE_PORT"
if ! curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
  info "khởi động ollama ở $OLLAMA_BASE_URL (chỉ nghe localhost)"
  # OLLAMA_HOST=127.0.0.1 -> KHÔNG mở ra mạng ngoài. Máy thuê có IP công khai,
  # để 0.0.0.0 là mở endpoint LLM cho cả Internet.
  OLLAMA_HOST="127.0.0.1:$USE_PORT" \
  OLLAMA_MAX_LOADED_MODELS=2 \
  OLLAMA_KEEP_ALIVE=30m \
    nohup ollama serve > "$HERE/ollama.log" 2>&1 &
  for _ in $(seq 1 30); do
    curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
fi
curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1 \
  || die "không kết nối được Ollama tại $OLLAMA_BASE_URL (xem $HERE/ollama.log)"
info "Ollama sẵn sàng tại $OLLAMA_BASE_URL"

# ------------------------------------------------------------------- models
log "6/7 Pull 2 model"
for M in "$GENERATOR_MODEL" "$DECISION_MODEL"; do
  if curl -fsS "$OLLAMA_BASE_URL/api/tags" | grep -q "\"${M%%:*}"; then
    info "đã có $M"
  else
    info "pull $M (có thể lâu)"
    OLLAMA_HOST="127.0.0.1:$USE_PORT" ollama pull "$M"
  fi
done

# -------------------------------------------------------------------- xong
log "7/7 Biến môi trường cần đặt"
cat <<EOF
   export REPOTRANSBENCH_ROOT="$SRC_DIR"
   export OLLAMA_BASE_URL="$OLLAMA_BASE_URL"
   export RTB_WORK_DIR="\${RTB_WORK_DIR:-/tmp/rtb_work}"
   export RUN_PROFILE=pilot_linux

   # Kiểm tra trước khi chạy thật (không đốt giờ GPU):
   $VENV_DIR/bin/python preflight.py --profile pilot_linux

   # Chạy thực nghiệm:
   bash scripts/run_pilot.sh
EOF
printf '\n\033[32mSETUP XONG\033[0m\n'
