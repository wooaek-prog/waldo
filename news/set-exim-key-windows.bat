@echo off
REM ===================================================================
REM  한국수출입은행 환율 API 키를 새로 입력합니다. 더블클릭하세요.
REM ===================================================================
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 server.py --set-exim-key %*
    goto done
)
where python >nul 2>&1
if %errorlevel%==0 (
    python server.py --set-exim-key %*
    goto done
)
echo.
echo  [!] 파이썬을 찾지 못했습니다. START-HERE.md 의 2단계를 먼저 진행해 주세요.
echo.

:done
echo.
echo  창을 닫으려면 아무 키나 누르세요.
pause >nul
