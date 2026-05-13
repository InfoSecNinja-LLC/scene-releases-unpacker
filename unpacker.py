"""
Scene Release Unpacker
----------------------
Watches a folder for new subfolders containing scene-style archives
(numbered .zip volumes that wrap multi-part .rar sets, or single-zip
installer wrappers). When a subfolder has been quiet for a while, it:

  1. Extracts every .zip into the same subfolder (iteratively, so a zip
     containing another zip gets unpacked too).
  2. Extracts the resulting .rar set (rarfile auto-handles all volumes
     once it has the first one).
  3. Sends the original zips and rar volumes to the Windows Recycle Bin
     once extraction succeeds (use --no-trash to delete permanently).

Tested patterns:
  - cr-kpl01.zip ... cr-kpl48.zip  -> core.part01.rar ... core.part48.rar
  - cr-fu381.zip cr-fu382.zip      -> core.part1.rar core.part2.rar
  - ampuqkh1.zip ... ampuqkh5.zip  -> amped.part1.rar ... amped.part5.rar
  - cr-qna4f.zip                   -> core.rar
  - lxp64p68.zip                   -> AnyBurnPro(64-bit).zip + nfo

Requires UnRAR.exe (from WinRAR or rarlab.com) or 7z.exe somewhere on
disk. The script auto-detects the usual install locations or you can
pass --unrar <path>.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    import rarfile
except ImportError:
    print("Missing dependency 'rarfile'. Install with: pip install rarfile",
          file=sys.stderr)
    sys.exit(2)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    print("Missing dependency 'watchdog'. Install with: pip install watchdog",
          file=sys.stderr)
    sys.exit(2)

try:
    from send2trash import send2trash
except ImportError:
    print("Missing dependency 'send2trash'. Install with: pip install send2trash",
          file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Defaults (override on the command line)
# --------------------------------------------------------------------------- #
DEFAULT_WATCH_FOLDER = r"F:\Downloads"
DEFAULT_STABILITY_SECONDS = 30   # folder must be quiet this long before we touch it
DEFAULT_SCAN_INTERVAL = 5        # how often the worker thread checks readiness
DEFAULT_LOG_NAME = "unpacker.log"
MAX_NESTED_PASSES = 5            # cap on iterative zip/rar extraction passes

# Patterns scene archives use
RE_PART_RAR = re.compile(r"\.part(\d+)\.rar$", re.IGNORECASE)
RE_OLDSTYLE_VOL = re.compile(r"\.r\d{2,3}$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# unrar/7z auto-detection
# --------------------------------------------------------------------------- #
# Microsoft Store install stubs that LOOK like real exes but actually do
# nothing (or open a Store install page) when invoked. They live under
# %LOCALAPPDATA%\Microsoft\WindowsApps and break extraction silently — the
# tool returns ~135 bytes of nothing and rarfile reports "Failed the read
# enough data". We never want to pick one of these.
SHIM_PATH_FRAGMENT = os.path.join("WindowsApps").lower()


def _looks_like_shim(path: str) -> bool:
    return SHIM_PATH_FRAGMENT in path.lower()


def _functional_check(tool_path: str) -> bool:
    """
    Run `tool --help` (or `7z`) very briefly. The Store stubs print almost
    nothing or fail to start; real tools print multi-line help. Returns True
    if the output looks like a real RAR/7z tool. Best-effort only — failure
    here is non-fatal at the autodetect stage but used to break ties.
    """
    try:
        # Common across UnRAR.exe / 7z.exe: bare invocation prints help.
        result = subprocess.run(
            [tool_path],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        blob = (result.stdout or "") + (result.stderr or "")
        # Real UnRAR/7z help is several KB. Stubs return ~0–200 bytes.
        if len(blob) < 400:
            return False
        lower = blob.lower()
        return any(needle in lower for needle in ("rar", "7-zip", "7zip"))
    except Exception:
        return False


def autodetect_unrar(explicit: str | None = None,
                     extra_search_dirs: list[Path] | None = None) -> str | None:
    """
    Return a path to a working UnRAR.exe / 7z.exe, or None.

    Resolution order:
      1. Explicit --unrar path (always honored, even if it's a shim).
      2. UnRAR.exe / 7z.exe sitting next to the script or in ./bin.
      3. WinRAR's UnRAR.exe in Program Files.
      4. Real 7-Zip in Program Files.
      5. Anything else on PATH that ISN'T a WindowsApps shim and that
         passes a quick functional check.
    """
    if explicit:
        return explicit

    extra_search_dirs = extra_search_dirs or []
    portable_candidates: list[str] = []
    for d in extra_search_dirs:
        for name in ("UnRAR.exe", "unrar.exe", "Rar.exe", "rar.exe", "7z.exe"):
            portable_candidates.append(str(d / name))

    install_candidates = [
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        r"C:\Program Files\WinRAR\Rar.exe",
        r"C:\Program Files (x86)\WinRAR\Rar.exe",
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]

    # Direct file-on-disk checks first (most reliable; no PATH ambiguity).
    for c in portable_candidates + install_candidates:
        if os.path.isfile(c) and not _looks_like_shim(c):
            return c

    # Fall back to PATH-resolved names, skipping shims and verifying.
    path_names = ["UnRAR.exe", "unrar.exe", "unrar", "7z.exe", "7z"]
    for name in path_names:
        found = shutil.which(name)
        if not found:
            continue
        if _looks_like_shim(found):
            logging.getLogger(__name__).debug(
                "Skipping WindowsApps shim at %s", found)
            continue
        if _functional_check(found):
            return found

    return None


# --------------------------------------------------------------------------- #
# Filesystem helpers
# --------------------------------------------------------------------------- #
def folder_last_mtime(folder: Path) -> float:
    """Most recent mtime across files in folder (recursive). 0 if empty/error."""
    latest = 0.0
    try:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                try:
                    mt = os.path.getmtime(os.path.join(root, f))
                    if mt > latest:
                        latest = mt
                except OSError:
                    pass
    except OSError:
        pass
    return latest


def find_zips(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.zip"))


def find_rar_starts(folder: Path) -> list[Path]:
    """
    Pick the volume that should be passed to the extractor for each rar set.

    - foo.partN.rar / foo.partNN.rar / foo.partNNN.rar  -> N == 1 only
    - plain foo.rar                                     -> always (even if .r00 follows)
    - foo.r00, foo.r01, ...                             -> ignored (companions)
    """
    starts: list[Path] = []
    for p in folder.rglob("*.rar"):
        m = RE_PART_RAR.search(p.name)
        if m:
            if int(m.group(1)) == 1:
                starts.append(p)
        else:
            starts.append(p)
    return sorted(starts)


def companion_rar_volumes(start: Path) -> list[Path]:
    """All files in `start.parent` that belong to the same rar set as `start`."""
    folder = start.parent
    start_lower = start.name.lower()
    members = [start]

    m = RE_PART_RAR.search(start_lower)
    if m:
        stem = start_lower[: m.start()]
        sib_re = re.compile(re.escape(stem) + r"\.part\d+\.rar$", re.IGNORECASE)
        for sib in folder.iterdir():
            if sib.name.lower() == start_lower:
                continue
            if sib_re.match(sib.name.lower()):
                members.append(sib)
    else:
        stem = start_lower[:-4]
        sib_re = re.compile(re.escape(stem) + r"\.r\d{2,3}$", re.IGNORECASE)
        for sib in folder.iterdir():
            if sib.name.lower() == start_lower:
                continue
            if sib_re.match(sib.name.lower()):
                members.append(sib)

    return members


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract_zip(zip_path: Path) -> bool:
    log = logging.getLogger(__name__)
    dest = zip_path.parent
    log.info("  zip  -> %s", zip_path.name)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(dest)
        return True
    except Exception as e:
        log.error("  zip FAILED %s: %s", zip_path.name, e)
        return False


def _looks_like_unrar(tool_path: str) -> bool:
    name = os.path.basename(tool_path).lower()
    return name in ("unrar.exe", "unrar", "rar.exe", "rar")


def extract_rar_keep_broken(rar_path: Path, tool_path: str) -> bool:
    """
    Last-ditch fallback: invoke unrar.exe directly with -kb (keep broken)
    and -y (yes-to-all). This succeeds even when one inner file fails CRC,
    which is the case for several Vovsoft releases that ship a corrupt
    Vovsoft.url file. Only meaningful for unrar.exe / rar.exe; for 7z we
    bail out and let the caller report the original error.
    """
    log = logging.getLogger(__name__)
    if not _looks_like_unrar(tool_path):
        return False
    dest = rar_path.parent
    cmd = [tool_path, "x", "-kb", "-o+", "-y", "-p-",
           str(rar_path), str(dest) + os.sep]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log.error("  rar FAILED (-kb) %s: %s", rar_path.name, e)
        return False
    # unrar returns 0 on success, 1 on warnings (CRC errors -- still useful
    # because -kb keeps what extracted), other codes are real errors.
    if result.returncode in (0, 1):
        if result.returncode == 1:
            log.warning("  rar  -> %s: extracted with CRC warnings (-kb)",
                        rar_path.name)
        return True
    log.error("  rar FAILED (-kb) %s: rc=%d %s",
              rar_path.name, result.returncode,
              (result.stderr or result.stdout or "").strip()[:200])
    return False


def extract_rar(rar_path: Path, ignore_crc: bool = False,
                tool_path: str | None = None) -> bool:
    log = logging.getLogger(__name__)
    dest = rar_path.parent
    log.info("  rar  -> %s", rar_path.name)
    try:
        with rarfile.RarFile(rar_path) as rf:
            rf.extractall(dest)
        return True
    except rarfile.NeedFirstVolume as e:
        log.error("  rar FAILED %s: not first volume (%s)", rar_path.name, e)
        return False
    except rarfile.BadRarFile as e:
        msg = str(e)
        if ignore_crc and "CRC" in msg and tool_path and _looks_like_unrar(tool_path):
            log.warning("  rar CRC error on %s -- retrying with -kb",
                        rar_path.name)
            return extract_rar_keep_broken(rar_path, tool_path)
        log.error("  rar FAILED %s: %s", rar_path.name, e)
        return False
    except Exception as e:
        log.error("  rar FAILED %s: %s", rar_path.name, e)
        return False


def human_bytes(n: int) -> str:
    """Render a byte count like 1.4 MB / 832 KB / 7.1 GB."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_delete(paths: list[Path], use_trash: bool = True) -> int:
    """
    Remove the given files. With use_trash=True (default) they go to the
    Windows Recycle Bin so they're recoverable from Explorer. Returns the
    total bytes removed (sum of file sizes).
    """
    log = logging.getLogger(__name__)
    bytes_freed = 0
    verb = "trash" if use_trash else "del"
    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        try:
            if use_trash:
                send2trash(str(p))
            else:
                p.unlink()
            bytes_freed += size
            log.info("  %s %s (%s)", verb, p.name, human_bytes(size))
        except Exception as e:
            log.warning("  %s FAILED %s: %s", verb, p.name, e)
    return bytes_freed


# --------------------------------------------------------------------------- #
# Folder processor
# --------------------------------------------------------------------------- #
def process_folder(folder: Path, use_trash: bool = True,
                   ignore_crc: bool = False,
                   tool_path: str | None = None) -> bool:
    """Returns True if the folder finished cleanly (no archive errors)."""
    log = logging.getLogger(__name__)
    log.info("Processing: %s", folder)

    started = time.monotonic()
    had_archives = False
    any_failure = False
    failure_reason: str | None = None
    bytes_freed = 0

    # ---- ZIP pass: iterate so wrapper-zip-inside-zip also gets unpacked -----
    extracted_zips: list[Path] = []
    for _ in range(MAX_NESTED_PASSES):
        zips = [z for z in find_zips(folder) if z not in extracted_zips]
        if not zips:
            break
        had_archives = True
        progress = False
        for z in zips:
            if extract_zip(z):
                extracted_zips.append(z)
                progress = True
            else:
                any_failure = True
                failure_reason = failure_reason or f"zip:{z.name}"
        if not progress:
            break

    # ---- RAR pass: rarfile auto-handles all volumes given the first ---------
    extracted_rars: list[Path] = []
    for _ in range(MAX_NESTED_PASSES):
        rars = [r for r in find_rar_starts(folder) if r not in extracted_rars]
        if not rars:
            break
        had_archives = True
        progress = False
        for r in rars:
            if extract_rar(r, ignore_crc=ignore_crc, tool_path=tool_path):
                extracted_rars.append(r)
                progress = True
            else:
                any_failure = True
                failure_reason = failure_reason or f"rar:{r.name}"
        if not progress:
            break

    # ---- Cleanup: only if the whole chain succeeded -------------------------
    if not any_failure:
        if extracted_zips:
            bytes_freed += safe_delete(extracted_zips, use_trash=use_trash)
        if extracted_rars:
            all_volumes: list[Path] = []
            for start in extracted_rars:
                all_volumes.extend(companion_rar_volumes(start))
            seen: set[Path] = set()
            unique = [p for p in all_volumes if not (p in seen or seen.add(p))]
            bytes_freed += safe_delete(unique, use_trash=use_trash)

    duration = time.monotonic() - started
    n_archives = len(extracted_zips) + len(extracted_rars)

    if not had_archives:
        log.info("[SKIP] %s -- no archives (%.1fs)", folder.name, duration)
        return True
    if any_failure:
        log.warning(
            "[FAIL] %s -- %s (%d ok, archives kept, %.1fs)",
            folder.name, failure_reason or "see above", n_archives, duration,
        )
        return False
    log.info(
        "[PASS] %s -- %d archives, %s freed%s (%.1fs)",
        folder.name,
        n_archives,
        human_bytes(bytes_freed),
        " -> Recycle Bin" if use_trash else "",
        duration,
    )
    return True


# --------------------------------------------------------------------------- #
# Watcher
# --------------------------------------------------------------------------- #
class WatcherState:
    """Shared state between the filesystem listener and the worker thread."""
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict[Path, float] = {}
        self.processed: set[Path] = set()
        self.stop = threading.Event()


class FolderEventHandler(FileSystemEventHandler):
    def __init__(self, watch_root: Path, state: WatcherState) -> None:
        self.watch_root = watch_root.resolve()
        self.state = state

    def _bump(self, path_str: str) -> None:
        try:
            p = Path(path_str).resolve()
        except OSError:
            return
        try:
            rel = p.relative_to(self.watch_root)
        except ValueError:
            return
        if not rel.parts:
            return
        top = self.watch_root / rel.parts[0]
        if not top.is_dir():
            return
        with self.state.lock:
            if top in self.state.processed:
                return
            self.state.pending[top] = time.time()

    def on_created(self, event):  self._bump(event.src_path)
    def on_modified(self, event): self._bump(event.src_path)
    def on_moved(self, event):    self._bump(event.dest_path)


def stable_processor(state: WatcherState, stability: float, interval: float,
                     use_trash: bool, ignore_crc: bool,
                     tool_path: str | None) -> None:
    log = logging.getLogger(__name__)
    while not state.stop.is_set():
        if state.stop.wait(interval):
            return
        now = time.time()
        ready: list[Path] = []
        with state.lock:
            for folder, last_event in list(state.pending.items()):
                actual_last = max(last_event, folder_last_mtime(folder))
                if now - actual_last >= stability:
                    if folder.exists() and folder.is_dir():
                        ready.append(folder)
                    state.pending.pop(folder, None)
        for folder in ready:
            try:
                process_folder(folder, use_trash=use_trash,
                               ignore_crc=ignore_crc, tool_path=tool_path)
            except Exception:
                log.exception("Unhandled error processing %s", folder)
            finally:
                with state.lock:
                    state.processed.add(folder)


def initial_scan(watch_root: Path, state: WatcherState) -> None:
    """Queue every existing top-level subfolder so they get processed once stable."""
    for child in watch_root.iterdir():
        if child.is_dir():
            with state.lock:
                state.pending[child.resolve()] = (
                    folder_last_mtime(child) or time.time()
                )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def setup_logging(log_path: Path) -> None:
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [
        RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3,
                            encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def script_dir() -> Path:
    """Folder the .py / .exe lives in (works under PyInstaller too)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-unpack scene releases.")
    parser.add_argument("-f", "--folder", default=DEFAULT_WATCH_FOLDER,
                        help=f"Folder to watch (default: {DEFAULT_WATCH_FOLDER})")
    parser.add_argument("-s", "--stability", type=int,
                        default=DEFAULT_STABILITY_SECONDS,
                        help="Seconds a folder must be quiet before processing "
                             f"(default: {DEFAULT_STABILITY_SECONDS})")
    parser.add_argument("-i", "--interval", type=int,
                        default=DEFAULT_SCAN_INTERVAL,
                        help=f"Worker check interval in seconds "
                             f"(default: {DEFAULT_SCAN_INTERVAL})")
    parser.add_argument("--unrar", default=None,
                        help="Path to UnRAR.exe / 7z.exe (auto-detected if omitted)")
    parser.add_argument("--log", default=None,
                        help="Path to log file (default: <script-dir>/unpacker.log)")
    parser.add_argument("--once", action="store_true",
                        help="Process every existing subfolder once and exit "
                             "(no watching)")
    parser.add_argument("--no-trash", action="store_true",
                        help="Permanently delete archives instead of sending "
                             "them to the Recycle Bin (Recycle Bin is the "
                             "default so deletions are recoverable)")
    parser.add_argument("--ignore-crc", action="store_true",
                        help="On rar CRC errors, retry with UnRAR.exe -kb "
                             "(keep broken). Useful for releases that ship a "
                             "corrupt companion file (e.g. Vovsoft.url) -- "
                             "everything else still extracts. Requires "
                             "UnRAR.exe / Rar.exe (not 7z.exe).")
    args = parser.parse_args()
    use_trash = not args.no_trash
    ignore_crc = args.ignore_crc

    log_path = Path(args.log) if args.log else script_dir() / DEFAULT_LOG_NAME
    setup_logging(log_path)
    log = logging.getLogger(__name__)

    watch_root = Path(args.folder)
    if not watch_root.is_dir():
        log.error("Watch folder does not exist: %s", watch_root)
        return 1

    here = script_dir()
    unrar = autodetect_unrar(args.unrar,
                             extra_search_dirs=[here, here / "bin"])
    if not unrar:
        log.error("Could not find a working UnRAR.exe or 7z.exe.")
        log.error("Heads up: the 7z.exe under "
                  "%%LOCALAPPDATA%%\\Microsoft\\WindowsApps is a Microsoft "
                  "Store install stub and is intentionally skipped.")
        log.error("Fix one of:")
        log.error("  * Install WinRAR (gives you UnRAR.exe in Program Files)")
        log.error("  * Install real 7-Zip from https://www.7-zip.org/")
        log.error("  * Drop UnRAR.exe next to this script (or in .\\bin\\)")
        log.error("  * Pass --unrar <full-path>")
        return 1
    if _looks_like_shim(unrar):
        log.warning("Configured tool is a WindowsApps shim and will likely "
                    "fail: %s", unrar)
    rarfile.UNRAR_TOOL = unrar
    log.info("Using rar tool: %s", unrar)
    log.info("Watching: %s", watch_root)
    log.info("Stability window: %ds   Check interval: %ds",
             args.stability, args.interval)
    log.info("Cleanup mode: %s",
             "Recycle Bin (recoverable)" if use_trash else "permanent delete")
    if ignore_crc:
        if _looks_like_unrar(unrar):
            log.info("CRC errors will be retried with -kb (keep broken)")
        else:
            log.warning("--ignore-crc requested but %s is not unrar/rar -- "
                        "the retry path will be skipped", os.path.basename(unrar))
    log.info("Log file: %s", log_path)

    state = WatcherState()
    initial_scan(watch_root, state)

    if args.once:
        with state.lock:
            ready = list(state.pending.keys())
            state.pending.clear()
        for folder in ready:
            try:
                process_folder(folder, use_trash=use_trash,
                               ignore_crc=ignore_crc, tool_path=unrar)
            except Exception:
                log.exception("Unhandled error processing %s", folder)
        return 0

    handler = FolderEventHandler(watch_root, state)
    observer = Observer()
    observer.schedule(handler, str(watch_root), recursive=True)
    observer.start()

    worker = threading.Thread(
        target=stable_processor,
        args=(state, args.stability, args.interval, use_trash,
              ignore_crc, unrar),
        daemon=True,
    )
    worker.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        state.stop.set()
        observer.stop()
        observer.join(timeout=5)
        worker.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
