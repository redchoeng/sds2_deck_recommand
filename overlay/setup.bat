@echo off
chcp 65001 >nul
echo ===================================
echo   STS2 카드 추천 오버레이 설치
echo ===================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [오류] Python 3.11 이상이 필요합니다.
    echo https://python.org 에서 설치 후 다시 실행하세요.
    pause & exit /b 1
)

echo [1/2] 가상환경 생성 중...
python -m venv venv311
if %errorlevel% neq 0 (echo [오류] 가상환경 생성 실패 & pause & exit /b 1)

echo [2/2] 패키지 설치 중... (첫 실행 시 시간이 걸립니다)
call venv311\Scripts\activate
pip install -r requirements.txt -q
if %errorlevel% neq 0 (echo [오류] 패키지 설치 실패 & pause & exit /b 1)

echo.
echo ===================================
echo   설치 완료!
echo ===================================
echo.
echo 다음 단계:
echo   1. calibrate.bat  - 카드 영역 설정 (최초 1회 필수)
echo   2. run.bat        - 오버레이 실행
echo.
echo [선택] Claude AI 인식률 향상:
echo   overlay 폴더에 .api_key 파일 생성 후 Anthropic API 키 입력
echo   (없어도 기본 OCR로 동작합니다)
echo.
pause
