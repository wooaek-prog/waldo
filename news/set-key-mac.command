#!/bin/bash
# ===================================================================
#  네이버 API 키를 새로 입력합니다. 이 파일을 더블클릭하세요.
# ===================================================================
cd "$(dirname "$0")" || exit 1
export PYTHONIOENCODING=utf-8

if command -v python3 >/dev/null 2>&1; then
    python3 server.py --set-key "$@"
else
    echo
    echo " [!] 파이썬을 찾지 못했습니다. START-HERE.md 의 2단계를 먼저 진행해 주세요."
    echo
fi
