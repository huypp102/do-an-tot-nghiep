#!/usr/bin/env bash
# Chạy THỰC NGHIỆM 1 bằng nohup, log ra file, in cách theo dõi.
#
# Dùng nohup vì lượt chạy mất nhiều giờ: mất SSH giữa đường không được làm mất
# lượt chạy (và mất tiền GPU đã tiêu).
#
#     bash scripts/run_pilot.sh                 # chạy mới
#     bash scripts/run_pilot.sh --resume --run-id run_20260101_120000
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

PY="${PY:-$HERE/.venv/bin/python}"
[ -x "$PY" ] || { echo "Không thấy $PY -- chạy scripts/setup_linux.sh trước."; exit 1; }

# Nạp đường dẫn dataset mà setup_linux.sh đã ghi, nếu chưa có trong môi trường.
if [ -z "${REPOTRANSBENCH_ROOT:-}" ] && [ -f "$HERE/.dataset_root" ]; then
  REPOTRANSBENCH_ROOT="$(cat "$HERE/.dataset_root")"
  export REPOTRANSBENCH_ROOT
fi
export RUN_PROFILE="${RUN_PROFILE:-pilot_linux}"
export RTB_WORK_DIR="${RTB_WORK_DIR:-/tmp/rtb_work}"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"

RUN_ID="${RUN_ID:-run_$(date +%Y%m%d_%H%M%S)}"
# --run-id do người dùng truyền (khi --resume) thì ưu tiên nó.
for i in "$(seq 1 $#)"; do :; done
ARGS=("$@")
for idx in "${!ARGS[@]}"; do
  if [ "${ARGS[$idx]}" = "--run-id" ]; then
    RUN_ID="${ARGS[$((idx + 1))]}"
  fi
done

LOG_DIR="$HERE/results/$RUN_ID"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/pilot.log"

cat <<EOF
==========================================================================
THỰC NGHIỆM 1
  run_id              : $RUN_ID
  profile             : $RUN_PROFILE
  REPOTRANSBENCH_ROOT : ${REPOTRANSBENCH_ROOT:-(CHƯA ĐẶT)}
  OLLAMA_BASE_URL     : $OLLAMA_BASE_URL
  RTB_WORK_DIR        : $RTB_WORK_DIR
  log                 : $LOG
==========================================================================
EOF

if [ -z "${REPOTRANSBENCH_ROOT:-}" ]; then
  echo "DỪNG: chưa đặt REPOTRANSBENCH_ROOT (và không có .dataset_root)." >&2
  exit 1
fi

HAS_RUN_ID=0
for a in "$@"; do [ "$a" = "--run-id" ] && HAS_RUN_ID=1; done
EXTRA=()
[ "$HAS_RUN_ID" -eq 0 ] && EXTRA=(--run-id "$RUN_ID")

nohup "$PY" run_experiment1.py --profile "$RUN_PROFILE" "${EXTRA[@]}" "$@" \
  > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$LOG_DIR/pilot.pid"

cat <<EOF

Đã chạy nền, pid=$PID.

Theo dõi:
  tail -f $LOG
  grep -E 'REPO |PHỄU|BÁO CÁO' $LOG        # chỉ các mốc lớn
  ls $LOG_DIR/repos/                        # repo nào đã xong (ghi ngay khi xong)
  nvidia-smi                                # VRAM
  curl -s \$OLLAMA_BASE_URL/api/ps          # 2 model có cùng nạp không

Dừng:
  kill \$(cat $LOG_DIR/pilot.pid)

Chạy tiếp phần còn lại (an toàn, bỏ qua cặp repo/nhánh đã xong):
  bash scripts/run_pilot.sh --resume --run-id $RUN_ID

Thu kết quả về máy:
  bash scripts/collect_results.sh $RUN_ID
EOF
