@echo off
setlocal
cd /d "%~dp0"

if not exist FocusXv16.py (
  echo FocusXv16.py not found next to this bat.
  pause
  exit /b 1
)

set EXTRA=
if exist berry.png set EXTRA=%EXTRA% --windows-icon-from-ico=berry.png --include-data-files=berry.png=berry.png

echo Building FocusX.exe ...
python -m nuitka ^
  --onefile ^
  --enable-plugin=tk-inter ^
  --include-package=pynput ^
  --windows-console-mode=disable ^
  %EXTRA% ^
  --output-filename=FocusX.exe ^
  --output-dir=dist ^
  --assume-yes-for-downloads ^
  --remove-output ^
  FocusXv16.py

if errorlevel 1 goto :failed
echo.
echo Build complete: %~dp0dist\FocusX.exe
echo Add FocusX.exe to your Windows Startup folder manually if desired.
pause
exit /b 0

:failed
echo Build failed. Scroll up for the Nuitka error.
pause
exit /b 1