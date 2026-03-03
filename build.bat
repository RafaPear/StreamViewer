@echo off
REM Build StreamsClient as a standalone Windows .exe
REM
REM Usage:
REM   build.bat           Build the app
REM   build.bat clean     Remove build artifacts
REM
REM Prerequisites:
REM   - Python 3.10+
REM   - VLC installed (runtime dependency)
REM   - pip install pyinstaller (build dependency only)
REM
REM Output:
REM   dist\StreamsClient\StreamsClient.exe

cd /d "%~dp0"

if "%1"=="clean" (
    echo Cleaning build artifacts...
    rmdir /s /q build 2>nul
    rmdir /s /q dist 2>nul
    rmdir /s /q __pycache__ 2>nul
    echo Done.
    exit /b 0
)

REM Activate venv if present.
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
)

REM Ensure PyInstaller is available.
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller --quiet
)

echo Building StreamsClient...
python -m PyInstaller StreamsClient.spec ^
    --noconfirm ^
    --clean ^
    --distpath dist ^
    --workpath build

if exist "dist\StreamsClient\StreamsClient.exe" (
    echo.
    echo Build successful!
    echo   Executable: dist\StreamsClient\StreamsClient.exe
    echo.
    echo Note: VLC must be installed on the system.
) else (
    echo.
    echo Build failed. Check output above for errors.
    exit /b 1
)
