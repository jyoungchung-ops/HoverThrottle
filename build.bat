@echo off
rem HoverThrottle.exe 로컬 빌드 (Windows PC). Python 3.12 64비트가 필요합니다.
rem 사용법: build.bat          → dist\HoverThrottle.exe
rem         build.bat notest   → 테스트 건너뛰기
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo [1/4] 가상환경 생성
    py -3.12 -m venv .venv || python -m venv .venv || goto :fail
)
set PY=.venv\Scripts\python.exe

echo [2/4] 의존성 설치
%PY% tools\install_deps.py --build || goto :fail

if /i not "%1"=="notest" (
    echo [3/4] 테스트
    set QT_QPA_PLATFORM=offscreen
    %PY% -m pytest -q || goto :fail
) else (
    echo [3/4] 테스트 건너뜀
)

echo [4/4] PyInstaller 빌드
if not exist bin\blackbox_decode.exe echo    참고: bin\blackbox_decode.exe 가 없어 orangebox 디코더만 포함됩니다.
%PY% -m PyInstaller --noconfirm --clean HoverThrottle.spec || goto :fail

echo.
echo 완료: %CD%\dist\HoverThrottle.exe
exit /b 0

:fail
echo.
echo 빌드 실패 (위 오류를 확인하세요)
exit /b 1
