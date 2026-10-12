#!/usr/bin/env bash
# travel-vedit 설치 스크립트 — iMac 2019 (Intel, macOS) 기준
# 사용법:  cd travel-vedit && bash setup/install_mac.sh
# 여러 번 실행해도 안전합니다 (이미 설치된 것은 건너뜀).
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
MODEL_NAME="ggml-large-v3-turbo-q5_0.bin"
MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${MODEL_NAME}"

say() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }

if [[ "$(uname)" != "Darwin" ]]; then
  echo "macOS 전용 스크립트입니다."; exit 1
fi
say "macOS $(sw_vers -productVersion) / $(uname -m) / $(sysctl -n machdep.cpu.brand_string)"

# 1) Homebrew
if ! command -v brew >/dev/null 2>&1; then
  say "Homebrew 가 없습니다. 아래 명령으로 먼저 설치한 뒤 이 스크립트를 다시 실행하세요:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  exit 1
fi

# 2) 영상/음성 도구
say "brew 패키지 설치 (ffmpeg, whisper-cpp, exiftool, python@3.12)"
brew install ffmpeg whisper-cpp exiftool python@3.12

# 3) 파이썬 가상환경
PY="$(brew --prefix python@3.12)/bin/python3.12"
if [[ ! -d .venv ]]; then
  say "가상환경 생성 (.venv)"
  "$PY" -m venv .venv
fi
say "파이썬 패키지 설치"
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -r requirements.txt

# 3-1) 모션 그래픽: 헤드리스 크롬(Playwright) + Pretendard 폰트(OFL, 템플릿 기본 폰트)
say "모션 그래픽용 Chromium 설치 (Playwright)"
.venv/bin/python -m playwright install chromium
if [[ ! -f motion/fonts/PretendardVariable.woff2 || ! -f motion/fonts/Pretendard-SemiBold.otf ]]; then
  say "Pretendard 폰트 다운로드"
  TMPD="$(mktemp -d)"
  curl -L --fail -o "$TMPD/pretendard.zip" "https://github.com/orioncactus/pretendard/releases/download/v1.3.9/Pretendard-1.3.9.zip"
  # woff2: 모션 템플릿용 / otf: 자막(libass) 번인용
  unzip -o -j -q "$TMPD/pretendard.zip" "web/variable/woff2/PretendardVariable.woff2" \
    "public/static/Pretendard-SemiBold.otf" "public/static/Pretendard-Bold.otf" -d motion/fonts/
  rm -rf "$TMPD"
fi

# 4) whisper 모델 (약 550MB)
mkdir -p models
if [[ ! -f "models/${MODEL_NAME}" ]]; then
  say "whisper 모델 다운로드: ${MODEL_NAME}"
  curl -L --fail -o "models/${MODEL_NAME}.part" "$MODEL_URL"
  mv "models/${MODEL_NAME}.part" "models/${MODEL_NAME}"
fi

# 5) whisper 속도 측정 (선택) — 1분짜리 테스트 음성으로 실시간 대비 속도 확인
if [[ "${BENCH:-0}" == "1" ]]; then
  say "whisper 속도 측정 (60초 테스트 톤)"
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i "sine=frequency=440:duration=60" -ar 16000 -ac 1 /tmp/vedit_bench.wav
  time whisper-cli -m "models/${MODEL_NAME}" -f /tmp/vedit_bench.wav -t 6 -l ko >/dev/null 2>&1 || true
fi

# 6) 점검
say "환경 점검"
.venv/bin/python -m vedit doctor || true

cat <<'EOF'

────────────────────────────────────────────────────────────
다음 단계 (수동):
 1. Claude Code 설치 및 로그인 (구독 계정)
      curl -fsSL https://claude.ai/install.sh | bash
      claude          # 최초 1회 로그인
 2. Antigravity 설치 (공식 사이트 안내에 따름) 후 로그인
      agy --help      # 헤드리스 실행 플래그 확인 → config/engines.yaml 수정
 3. 외장 SSD 에 projects 폴더를 두려면:
      mv projects /Volumes/<SSD이름>/vedit-projects && ln -s /Volumes/<SSD이름>/vedit-projects projects
 4. 첫 프로젝트:
      source .venv/bin/activate
      python -m vedit init kyoto_2026 -s "/Volumes/SSD/교토원본" --title "교토 3박4일" --timezone Asia/Tokyo
      python -m vedit run kyoto_2026
────────────────────────────────────────────────────────────
EOF
