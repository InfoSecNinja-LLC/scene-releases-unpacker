@echo off
REM Convenience launcher. Edit FOLDER below to point at your download dir.
set FOLDER=F:\Downloads
set STABILITY=30

if exist "%~dp0dist\unpacker.exe" (
    "%~dp0dist\unpacker.exe" --folder "%FOLDER%" --stability %STABILITY%
) else (
    python "%~dp0unpacker.py" --folder "%FOLDER%" --stability %STABILITY%
)
