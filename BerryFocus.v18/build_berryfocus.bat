@echo off
setlocal
cd /d "%~dp0"

if not exist BerryFocus.py (
  echo BerryFocus.py not found next to this bat.
  pause
  exit /b 1
)

set EXTRA=
if exist Berry2.ico set EXTRA=%EXTRA% --windows-icon-from-ico=Berry2.ico --include-data-files=Berry2.ico=Berry2.ico
if exist cherry.wav set EXTRA=%EXTRA% --include-data-files=cherry.wav=cherry.wav
if exist cherry.mp3 set EXTRA=%EXTRA% --include-data-files=cherry.mp3=cherry.mp3
if exist beep.wav   set EXTRA=%EXTRA% --include-data-files=beep.wav=beep.wav
if exist beep.mp3   set EXTRA=%EXTRA% --include-data-files=beep.mp3=beep.mp3

echo Building BerryFocus.exe ...
python -m nuitka ^
  --onefile ^
  --enable-plugin=tk-inter ^
  --include-package=pynput ^
  --windows-console-mode=disable ^
  %EXTRA% ^
  --output-filename=BerryFocus.exe ^
  --output-dir=dist ^
  --assume-yes-for-downloads ^
  --remove-output ^
  BerryFocus.py

if errorlevel 1 goto :failed
echo.
echo Build complete: %~dp0dist\BerryFocus.exe
echo Run it once. It registers itself in Task Scheduler (BerryFocus) and starts at every logon.
echo Your data lives in dist\BerryFocusData (copy that folder with the exe to move PCs).
pause
exit /b 0

:failed
echo Build failed. Scroll up for the Nuitka error.
pause
exit /b 1
