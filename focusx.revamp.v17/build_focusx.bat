@echo off
setlocal
cd /d "%~dp0"

if not exist FocusXv17.py (
  echo FocusXv17.py not found next to this bat.
  pause
  exit /b 1
)

set EXTRA=
if exist Berry2.ico set EXTRA=%EXTRA% --windows-icon-from-ico=Berry2.ico --include-data-files=Berry2.ico=Berry2.ico

echo Building FocusXv17.exe ...
python -m nuitka ^
  --onefile ^
  --enable-plugin=tk-inter ^
  --include-package=pynput ^
  --windows-console-mode=disable ^
  %EXTRA% ^
  --output-filename=FocusXv17.exe ^
  --output-dir=dist ^
  --assume-yes-for-downloads ^
  --remove-output ^
  FocusXv17.py

if errorlevel 1 goto :failed
echo.
echo Build complete: %~dp0dist\FocusXv17.exe
echo Add FocusXv17.exe to your Windows Startup folder manually if desired.
pause
exit /b 0

:failed
echo Build failed. Scroll up for the Nuitka error.
pause
exit /b 1