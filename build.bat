@echo off
REM ---------------------------------------------------------------
REM Build a standalone unpacker.exe with PyInstaller.
REM Run this once on the Windows box where you want to use the tool.
REM Requires Python 3.10+ on PATH.
REM ---------------------------------------------------------------
setlocal

echo === Installing/upgrading dependencies ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
    echo Failed to install dependencies.
    exit /b 1
)

echo.
echo === Building unpacker.exe ===
python -m PyInstaller ^
    --onefile ^
    --console ^
    --name unpacker ^
    --clean ^
    unpacker.py
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo === Done ===
echo Executable: %CD%\dist\unpacker.exe
echo.
echo Quick start:
echo     dist\unpacker.exe --folder "F:\Downloads"
endlocal
