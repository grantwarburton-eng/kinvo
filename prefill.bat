@echo off
rem Double-click to review a drafted application and open its form pre-filled.
cd /d "%~dp0"
python -c "import playwright, tomllib" 2>nul || (
  echo First run: installing the browser helper. This takes a minute or two.
  python -m pip install --quiet playwright
)
rem Does nothing if the browser is already installed.
python -m playwright install chromium >nul
python prefill.py %*
echo.
pause
