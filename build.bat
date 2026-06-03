@echo off
:: Build UniTool for Windows.
:: Usage:  build.bat
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ============================================
echo  UniTool -- building for Windows
echo ============================================

:: ── Python ──────────────────────────────────────────────────────────────────
where python >nul 2>&1 || (echo [ERROR] python not found in PATH & exit /b 1)
python --version

:: ── Virtual environment ─────────────────────────────────────────────────────
if not exist ".venv-windows" (
    echo [1/4] Creating virtual environment...
    python -m venv .venv-windows
)
call .venv-windows\Scripts\activate.bat

:: ── Dependencies ────────────────────────────────────────────────────────────
echo [2/4] Installing dependencies...
pip install -q --upgrade pip
pip install -q pyinstaller
pip install -q -r requirements.txt

:: ── icon.png ────────────────────────────────────────────────────────────────
if not exist "icon.png" (
    echo [3/4] Generating icon.png from icon.ico...
    python -c "from PIL import Image; Image.open('icon.ico').save('icon.png', 'PNG'); print('icon.png created')"
) else (
    echo [3/4] icon.png already exists -- skipping
)

:: ── PyInstaller ─────────────────────────────────────────────────────────────
echo [4/4] Running PyInstaller...
pyinstaller UniTool.spec --noconfirm

echo.
echo ============================================
echo  Build SUCCESS
echo  Output: dist\UniTool.exe
echo ============================================
