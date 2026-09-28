#!/usr/bin/env bash
# Nén results/ lại và in lệnh scp để tải về máy cá nhân.
#
#     bash scripts/collect_results.sh              # nén toàn bộ results/
#     bash scripts/collect_results.sh run_2026...  # chỉ một lượt chạy
#
# CỐ Ý loại các thứ nặng và không cần cho phân tích: bản copy repo, venv,
# crate Rust. Trên máy thuê thường chỉ có kết nối chậm, và cái cần mang về là
# SỐ LIỆU + BÁO CÁO, không phải môi trường build.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

TARGET="${1:-}"
STAMP="$(date +%Y%m%d_%H%M%S)"
if [ -n "$TARGET" ]; then
  SRC="results/$TARGET"
  [ -d "$SRC" ] || { echo "Không thấy $SRC"; exit 1; }
  OUT="rtb_results_${TARGET}.tar.gz"
else
  SRC="results"
  OUT="rtb_results_all_${STAMP}.tar.gz"
fi

echo "Nén $SRC -> $OUT"
tar czf "$OUT" \
  --exclude='*.rtb_venv*' \
  --exclude='*/.rtb_crates*' \
  --exclude='*/site-packages/*' \
  --exclude='*.so' --exclude='*.pyd' \
  --exclude='__pycache__' \
  "$SRC"

SIZE="$(du -h "$OUT" | cut -f1)"
N_REPO=0
[ -d "$SRC/repos" ] && N_REPO="$(find "$SRC/repos" -name '*.json' | wc -l)"

cat <<EOF

Xong: $OUT ($SIZE)
  repo có kết quả: $N_REPO

Nội dung quan trọng:
  metadata.json          môi trường + preflight + QUY TẮC CHỌN MẪU + repo bị loại
  selection.json         ứng viên, repo chọn, repo dự phòng, lý do loại từng repo
  report.md              phễu + bảng theo repo + APR/SR + ablation
  funnel.json            phễu dạng máy đọc được
  ablation_report.{md,json}
  repos/<repo>.json      kết quả từng repo (lý do từng hotspot, số đo từng vòng)

Tải về máy cá nhân (chạy TRÊN MÁY CÁ NHÂN, đổi user@host và cổng cho đúng):
  scp -P 22 user@host:$HERE/$OUT .
  tar xzf $(basename "$OUT")

Nếu máy thuê chỉ cho kết nối qua cổng khác (vd 2222):
  scp -P 2222 user@host:$HERE/$OUT .
EOF
