@echo off
REM ---------------------------------------------------------------
REM Build a standalone unpacker.exe with PyInstaller.
REM Run this once on the Windows box where you want to use the tool.
REM Requires uv (https://docs.astral.sh/uv/) on PATH.
REM ---------------------------------------------------------------
setlocal

echo === Installing/upgrading dependencies ===
uv sync
if errorlevel 1 (
    echo Failed to install dependencies.
    exit /b 1
)

echo.
echo === Building unpacker.exe ===
uv run pyinstaller ^
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
