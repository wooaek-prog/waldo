@echo off
REM ===================================================================
REM  계열사 뉴스 실시간 모니터 - 윈도우용 실행 파일
REM  이 파일을 더블클릭하면 됩니다.
REM ===================================================================
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

echo.
echo  계열사 뉴스 실시간 모니터를 시작합니다...
echo.

REM 파이썬 찾기: py 런처 먼저, 없으면 python
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 server.py %*
    goto done
)

where python >nul 2>&1
if %errorlevel%==0 (
    python server.py %*
    goto done
)

echo.
echo  [!] 이 컴퓨터에서 파이썬을 찾지 못했습니다.
echo.
echo      1. https://www.python.org/downloads/ 에서 파이썬을 내려받아 설치하세요.
echo      2. 설치 첫 화면에서 "Add python.exe to PATH" 를 반드시 체크하세요.
echo      3. 설치가 끝나면 이 파일을 다시 더블클릭하세요.
echo.

:done
echo.
echo  창을 닫으려면 아무 키나 누르세요.
pause >nul
