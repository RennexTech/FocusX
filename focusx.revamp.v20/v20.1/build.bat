@echo off
setlocal
title FocusX Builder
cd /d "%~dp0"

echo ==================================================
echo   FocusX build  (Nuitka + Zig)
echo ==================================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    pause & exit /b 1
)
if not exist "focusx.v20.py" (
    echo [ERROR] focusx.v20.py not found in %CD%
    pause & exit /b 1
)
if not exist "cherry.ico" (
    echo [ERROR] cherry.ico not found in %CD%
    pause & exit /b 1
)

echo.
echo [1/4] Installing / updating build tools and dependencies...
python -m pip install --upgrade pip
python -m pip install --upgrade nuitka ziglang ordered-set zstandard pynput psutil
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause & exit /b 1
)

echo.
echo [2/4] Locating Zig...
python -c "import ziglang,os;print(os.path.dirname(ziglang.__file__))" > "%TEMP%\zigdir.txt"
set /p ZIGDIR=<"%TEMP%\zigdir.txt"
set "PATH=%ZIGDIR%;%PATH%"
echo Zig folder: %ZIGDIR%

echo.
echo [3/4] Closing running FocusX (if any) and compiling...
taskkill /f /im FocusX.v20.exe >nul 2>nul

python -m nuitka ^
  --onefile ^
  --zig ^
  --enable-plugin=tk-inter ^
  --include-package=pynput ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=cherry.ico ^
  --include-data-files=cherry.ico=cherry.ico ^
  --product-name=FocusX ^
  --file-description="FocusX Deep Work Protocol" ^
  --product-version=20.0.0.0 ^
  --output-filename=FocusX.v20.exe ^
  --output-dir=dist ^
  --assume-yes-for-downloads ^
  --remove-output ^
  focusx.v20.py

if errorlevel 1 (
    echo.
    echo [ERROR] Compilation failed. Read the messages above.
    echo Tip: if --zig is rejected, make sure nuitka is up to date.
    pause & exit /b 1
)

echo.
echo [4/4] Build OK:  %CD%\dist\FocusX.exe
echo.
choice /c YN /m "Add FocusX to Windows Startup folder now"
if errorlevel 2 goto done
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\FocusX.lnk'); $s.TargetPath='%CD%\dist\FocusX.exe'; $s.WorkingDirectory='%CD%\dist'; $s.Save()"
echo Startup shortcut created.

:done
echo.
echo Done.
pause
