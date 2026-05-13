# Scene Release Unpacker

A Windows background watcher that auto-extracts scene releases dropped into a folder.

For each new subfolder, it:

1. Extracts every `.zip` (iteratively, so wrapper-zip-inside-zip works).
2. Extracts the resulting `.rar` set (multi-volume `.partNN.rar` and old-style `.rNN` both handled).
3. Sends the original zip and rar volumes to the **Windows Recycle Bin** once extraction succeeds — so you can right-click → Restore in Explorer if you ever need them back.

Each release ends with a one-line summary in the log:

```
[PASS] Some.Release-CORE -- 6 archives, 31.2 MB freed -> Recycle Bin (4.3s)
[FAIL] Other.Release-CORE -- rar:core.part1.rar (2 ok, archives kept, 1.1s)
[SKIP] Already.Extracted   -- no archives (0.0s)
```

Tested against patterns like:

- `cr-kpl01.zip ... cr-kpl48.zip` → `core.part01.rar ... core.part48.rar`
- `cr-fu381.zip cr-fu382.zip` → `core.part1.rar core.part2.rar`
- `ampuqkh1.zip ... ampuqkh5.zip` → `amped.part1.rar ... amped.part5.rar`
- `cr-qna4f.zip` → `core.rar`
- `lxp64p68.zip` → wrapper-zip-inside-zip

If extraction of any archive fails, no archives are deleted for that folder — the originals stay so you can investigate.

## Prerequisites

- Windows
- Python 3.10+ (only required if you don't build the `.exe`)
- A **real** `UnRAR.exe` or `7z.exe` somewhere on disk:
  - Install WinRAR (gives you `C:\Program Files\WinRAR\UnRAR.exe`)
  - Install real 7-Zip from <https://www.7-zip.org/> (gives you `C:\Program Files\7-Zip\7z.exe`)
  - Or grab the standalone UnRAR command-line from <https://www.rarlab.com/rar_add.htm> and drop `UnRAR.exe` next to `unpacker.py` (the script also looks in `.\bin\`)

The script auto-detects all the standard locations. Override with `--unrar <full-path>` if needed.

> **Don't use the WindowsApps `7z.exe` shim.** If `7z.exe` resolves to `C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\7z.exe`, that's a Microsoft Store install stub — invoking it returns nothing useful and every rar will fail with `Failed the read enough data: req=1048576 got=135`. The script intentionally skips this path now, but you'll need a real 7-Zip or WinRAR installed.

## Install

This project staged itself at `F:\Downloads\unpacker\`. Double-click `install.bat` to move it to `F:\unpacker\` (Explorer opens to the new location when it's done).

## Quick start (Python script)

```cmd
pip install -r requirements.txt
python unpacker.py --folder "F:\Downloads"
```

## Building the standalone .exe

Run this once on your Windows box:

```cmd
build.bat
```

That installs PyInstaller and produces `dist\unpacker.exe`. Then:

```cmd
dist\unpacker.exe --folder "F:\Downloads"
```

`run-unpacker.bat` is a convenience launcher — edit the `FOLDER` line at the top and double-click it. It uses `dist\unpacker.exe` if built, otherwise falls back to `python unpacker.py`.

## Options

| Flag | Default | What it does |
|------|---------|--------------|
| `-f`, `--folder` | `F:\Downloads` | Folder to watch |
| `-s`, `--stability` | `30` | Seconds a folder must be idle before processing — bump this if your downloads are slow so we don't extract while files are still arriving |
| `-i`, `--interval` | `5` | How often the worker checks for ready folders |
| `--unrar` | auto | Explicit path to `UnRAR.exe` / `7z.exe` |
| `--log` | `<script-dir>\unpacker.log` | Log file path (rotated at 2 MB, 3 backups) |
| `--once` | off | Scan existing subfolders once, process them, then exit |
| `--no-trash` | off | Permanently delete archives instead of sending them to the Recycle Bin |
| `--ignore-crc` | off | On rar CRC errors, retry with `UnRAR.exe -kb` (keep broken). Useful for releases that ship a corrupt companion file (e.g. a bad `Vovsoft.url`) — everything else still extracts. Requires `UnRAR.exe` / `Rar.exe`, not `7z.exe`. |

## Undo / recovery

Cleaned-up archives go to the Recycle Bin by default. To recover one:

1. Open Explorer → Recycle Bin
2. Sort by Date Deleted, find the archive(s)
3. Right-click → **Restore**

Files are restored to their original location inside the release subfolder. If you'd rather skip the Recycle Bin entirely (e.g. these archives are huge and you've verified the unpack works), pass `--no-trash`.

## Run at Windows startup

Easiest: drop a shortcut to `run-unpacker.bat` into your Startup folder.

```
Win+R → shell:startup → paste shortcut here
```

For headless background operation, schedule it via Task Scheduler with "Run whether user is logged on or not".

## Logs

Everything goes to both the console and `unpacker.log` next to the script/exe (rotated at 2 MB, 3 backups kept). Per-archive details (`zip ->`, `rar ->`, `trash`) are at INFO level; the one-line `[PASS]` / `[FAIL]` / `[SKIP]` summary is the easiest way to scan history.
