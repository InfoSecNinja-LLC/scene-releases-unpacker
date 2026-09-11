# Repository Guidelines

`unpacker` (Scene Release Unpacker) is a Windows background watcher that auto-extracts scene releases dropped into a folder: it iteratively extracts `.zip` wrappers, extracts the resulting multi-volume `.rar` set, and sends the originals to the Recycle Bin once extraction succeeds. Run with `uv` or as a standalone `.exe`.

## Project Structure & Module Organization

- `unpacker.py` — the entire tool (a single script): folder watcher, zip/rar extraction, recycle-bin cleanup, rotating logging, CLI argument parsing.
- `unpacker.spec` + `build.bat` — PyInstaller one-file build.
- `run-unpacker.bat` — convenience launcher; edit the `FOLDER` line and double-click. Uses `dist\unpacker.exe` if built, else falls back to `python unpacker.py`.
- `pyproject.toml` + `uv.lock` — dependencies (`watchdog`, `rarfile`, `send2trash`; PyInstaller in the `dev` group; `[tool.uv] package = false`).
- Build output (do not commit): `build/`, `dist/`, `unpacker.log`.

## Build, Test, and Development Commands

Requires [uv](https://docs.astral.sh/uv/).

Run from source:

```cmd
uv sync
uv run unpacker.py --folder "F:\Downloads"
uv run unpacker.py --folder "F:\Downloads" --once       # process existing subfolders once, then exit
```

Build the standalone exe (installs deps via `uv sync`, then PyInstaller `--onefile --console --name unpacker`):

```cmd
build.bat
dist\unpacker.exe --folder "F:\Downloads"
```

There is no test suite in this repository.

## Coding Style & Naming Conventions

- Python 3.10+ (`requires-python = ">=3.10"`), 4-space indentation, PEP 8, `from __future__ import annotations`.
- Single-file script with a module docstring; `snake_case` for functions and variables.
- CLI via `argparse`; logging via `RotatingFileHandler` (2 MB, 3 backups) to both console and `unpacker.log`.

## Commit & Pull Request Guidelines

- Keep commits focused and describe the behavior change. Bump `version` in `pyproject.toml` for user-facing changes.
- Do not commit `build/`, `dist/`, or `unpacker.log`.

## Security & Configuration Tips

- Requires a **real** `UnRAR.exe` (WinRAR / rarlab standalone) or `7z.exe` on disk — the script auto-detects standard install locations, `.\bin\`, or next to `unpacker.py`; override with `--unrar <full-path>`.
- Do **not** rely on the WindowsApps `7z.exe` shim (a Microsoft Store stub) — the script deliberately skips it; install real 7-Zip or WinRAR.
- On extraction failure for a folder, no archives are deleted so the originals can be investigated. Cleanup uses the Recycle Bin by default; `--no-trash` deletes permanently.
