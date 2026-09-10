#!/bin/bash
# ===================================================================
#  계열사 뉴스 실시간 모니터 - 맥용 실행 파일
#  이 파일을 더블클릭하면 됩니다.
# ===================================================================
cd "$(dirname "$0")" || exit 1

export PYTHONIOENCODING=utf-8

echo
echo " 계열사 뉴스 실시간 모니터를 시작합니다..."
echo

if command -v python3 >/dev/null 2>&1; then
    python3 server.py "$@"
else
    echo
    echo " [!] 이 컴퓨터에서 파이썬을 찾지 못했습니다."
    echo
    echo "     맥에는 보통 파이썬이 들어 있지만, 없다면 아래 중 하나를 하세요."
    echo "       · https://www.python.org/downloads/ 에서 내려받아 설치"
    echo "       · 또는 터미널에서:  xcode-select --install"
    echo
    echo "     설치가 끝나면 이 파일을 다시 더블클릭하세요."
    echo
fi

echo
echo " 이 창은 닫아도 됩니다."
