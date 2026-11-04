@echo off
setlocal
cd /d "%~dp0"

if not exist FocusXv15.py goto :missing_script
if not exist cherry.wav goto :missing_wav
if not exist cherry.ico goto :missing_ico

rem Optional beep file for the Sprint tab (wav or mp3). Baked in if present.
set EXTRA=
if exist beep.wav set EXTRA=%EXTRA% --include-data-files=beep.wav=beep.wav
if exist beep.mp3 set EXTRA=%EXTRA% --include-data-files=beep.mp3=beep.mp3

echo Building FocusX15.exe ...
python -m nuitka ^
  --onefile ^
  --zig ^
  --enable-plugin=tk-inter ^
  --include-package=pynput ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=cherry.ico ^
  --include-data-files=cherry.wav=cherry.wav ^
  --include-data-files=cherry.ico=cherry.ico ^
  %EXTRA% ^
  --output-filename=FocusX15.exe ^
  --output-dir=dist ^
  --assume-yes-for-downloads ^
  --remove-output ^
  FocusXv15.py

if errorlevel 1 goto :failed
echo.
echo Done: %~dp0dist\FocusX15.exe
pause
exit /b 0

:missing_script
echo FocusXv15.py not found next to this bat.
pause
exit /b 1
:missing_wav
echo cherry.wav not found next to this bat.
pause
exit /b 1
:missing_ico
echo cherry.ico not found next to this bat.
pause
exit /b 1
:failed
echo Build failed, scroll up for the Nuitka error.
pause
exit /b 1
