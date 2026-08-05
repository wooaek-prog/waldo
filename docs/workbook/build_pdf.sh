#!/usr/bin/env bash
# 워크북 HTML -> 인쇄용 A4 PDF 빌드
# 요구: Chromium(headless), python3 + reportlab + PyPDF2, 한글 폰트(Noto Sans CJK KR / Nanum)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/워크북.html"
RAW="$DIR/.raw.pdf"
OUT="$DIR/../재건축_시공사선정_건설비검증_대의원워크북.pdf"

CHROME="${CHROME:-/opt/pw-browsers/chromium-1194/chrome-linux/chrome}"
[ -x "$CHROME" ] || CHROME="$(command -v chromium || command -v google-chrome || echo '')"
[ -n "$CHROME" ] || { echo "Chromium을 찾을 수 없습니다"; exit 1; }

echo "[1/2] HTML -> PDF 렌더링"
"$CHROME" --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
  --no-pdf-header-footer --print-to-pdf-no-header \
  --virtual-time-budget=20000 \
  --print-to-pdf="$RAW" "file://$SRC" 2>/dev/null

echo "[2/2] 페이지 번호 / 러닝 푸터 스탬프"
# reportlab + PyPDF2 가 함께 동작하는 인터프리터를 고른다
PY=""
for cand in python3 python3.12 python3.11 python3.13; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c "import reportlab.pdfgen.canvas, PyPDF2" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -n "$PY" ] || { echo "reportlab/PyPDF2를 쓸 수 있는 python이 없습니다 (pip install reportlab pypdf2)"; exit 1; }
"$PY" "$DIR/stamp.py" "$RAW" "$OUT"
rm -f "$RAW"
echo "완료: $OUT"
