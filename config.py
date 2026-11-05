"""Where the app keeps its private data, and moving old data there.

Personal data (chats, memory, settings, backups, API keys) lives OUTSIDE the
project folder, in the standard Windows place for an app's private data:

    %LOCALAPPDATA%\\HybridAI\\      e.g. C:\\Users\\you\\AppData\\Local\\HybridAI

That keeps it out of anything you share (a GitHub repo, a zip for a friend,
a folder connected to an AI tool). The project folder only holds code.

Set HYBRID_AI_DATA_DIR to use a different folder.
"""
import datetime
import logging
import os
import shutil
import sqlite3

log = logging.getLogger("hybridai")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "HybridAI"
WINDOW_TITLE = "Hybrid AI Assistant"  # shared with startup.py, which looks the window up by it


def _default_data_dir():
    root = os.getenv("LOCALAPPDATA")  # Windows
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".local", "share")  # elsewhere
    return os.path.join(root, APP_NAME)


DATA_DIR = os.path.abspath(os.getenv("HYBRID_AI_DATA_DIR") or _default_data_dir())
DB_PATH = os.path.join(DATA_DIR, "chats.db")
MEMORY_DB = os.path.join(DATA_DIR, "memory.db")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
ENV_PATH = os.path.join(DATA_DIR, ".env")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
PASTED_DIR = os.path.join(DATA_DIR, "pasted")  # images pasted with Ctrl+V

LEGACY_DB = os.path.join(BASE_DIR, "data", "chats.db")
LEGACY_ENV = os.path.join(BASE_DIR, ".env")


def ensure_dirs():
    for d in (DATA_DIR, BACKUP_DIR, PASTED_DIR):
        os.makedirs(d, exist_ok=True)


class MigrationStopped(Exception):
    """The old data could not be moved safely. Raised only while the old files
    are untouched and DB_PATH does not exist. `.message` is shown to the user."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _held_by_other(path):
    """True if another program has this file open without sharing it."""
    if os.name != "nt" or not os.path.exists(path):
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = k32.CreateFileW(path, 0x80000000, 0, None, 3, 0x80, None)  # GENERIC_READ, no sharing, OPEN_EXISTING
    if handle in (None, wintypes.HANDLE(-1).value):
        err = ctypes.get_last_error()
        if err in (32, 33):  # sharing / lock violation
            return True
        log.warning("couldn't test whether %s is in use (error %s)", path, err)
        return False
    k32.CloseHandle(handle)
    return False


def _backup_legacy(paths):
    """Copy the raw files to BACKUP_DIR/migration-<timestamp>/ and check sizes.
    Returns the folder. On any failure the partial folder is removed and
    OSError is raised."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(BACKUP_DIR, "migration-" + stamp)
    n = 1
    while os.path.exists(folder):  # a retry within the same second
        n += 1
        folder = os.path.join(BACKUP_DIR, f"migration-{stamp}-{n}")
    os.makedirs(folder)
    try:
        for p in paths:
            dst = os.path.join(folder, os.path.basename(p))
            shutil.copy2(p, dst)
            if os.path.getsize(dst) != os.path.getsize(p):
                raise OSError(f"backup of {p} has the wrong size")
    except OSError:
        log.exception("couldn't back up the old data before moving it")
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def _counts(con):
    out = {}
    for t in ("conversations", "turns"):
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return out


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        log.warning("couldn't remove %s", path)


def _migrate_chats(notes):
    """Returns True if the .env was backed up along with the chats."""
    tmp = DB_PATH + ".migrating"
    if os.path.exists(tmp):  # left by an earlier crash; only we create it
        _remove_quiet(tmp)
    sides = [LEGACY_DB + s for s in ("-wal", "-shm") if os.path.exists(LEGACY_DB + s)]
    files = [LEGACY_DB] + sides

    for f in files:
        if _held_by_other(f):
            raise MigrationStopped(
                f"Your old chats in {LEGACY_DB} are open in another program (an older Hybrid AI, "
                "a DB viewer, OneDrive). Close it and click Retry. Nothing was changed.")
    need = 2 * (sum(os.path.getsize(f) for f in files) + 10 * 1024 * 1024)
    try:
        free = shutil.disk_usage(DATA_DIR).free
    except OSError:
        log.exception("couldn't check free disk space")
        free = need
    if free < need:
        raise MigrationStopped(
            f"Not enough free disk space in {DATA_DIR} to move your old chats safely. "
            "Free some space and click Retry. Nothing was changed.")

    env_due = os.path.exists(LEGACY_ENV) and not os.path.exists(ENV_PATH)
    try:
        backup = _backup_legacy(files + ([LEGACY_ENV] if env_due else []))
    except OSError as exc:
        raise MigrationStopped(
            "Hybrid AI couldn't back up your old chats, so nothing was moved. "
            f"({exc}) Click Retry to try again.") from exc

    src = dst = None
    try:
        src = sqlite3.connect(LEGACY_DB)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("quick_check failed on the copy")
        if _counts(src) != _counts(dst):
            raise sqlite3.DatabaseError("row counts differ between the old chats and the copy")
    except (sqlite3.Error, OSError) as exc:
        log.exception("couldn't copy the old chats database")
        for con in (dst, src):
            if con is not None:
                con.close()
        _remove_quiet(tmp)
        raise MigrationStopped(
            f"Hybrid AI couldn't copy your old chats safely ({exc}). Nothing was moved. "
            "Click Retry to try again.") from exc
    dst.close()
    src.close()

    try:
        os.replace(tmp, DB_PATH)
    except OSError as exc:
        log.exception("couldn't put the migrated chats in place")
        _remove_quiet(tmp)
        raise MigrationStopped(
            f"Hybrid AI couldn't finish moving your old chats ({exc}). Nothing was moved. "
            "Click Retry to try again.") from exc

    leftover = []
    for f in files:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                log.exception("couldn't remove an old chats file")
                leftover.append(f)
    if leftover:
        notes.append(f"Chats moved to {DB_PATH}. Couldn't remove the old copy at {LEGACY_DB} (in use); "
                     f"you can delete it. A backup is in {backup}")
    else:
        try:
            os.rmdir(os.path.dirname(LEGACY_DB))  # only if now empty
        except OSError as _exc:
            log.warning("couldn't remove the old data folder: %s", _exc)
        notes.append(f"moved your chats to {DB_PATH} (backup of the old files: {backup})")
    return env_due


def _migrate_env(notes, backed_up):
    tmp = ENV_PATH + ".tmp"
    try:
        if not backed_up:
            _backup_legacy([LEGACY_ENV])
        shutil.copy2(LEGACY_ENV, tmp)
        os.replace(tmp, ENV_PATH)
    except OSError:
        log.exception("couldn't move the API keys")
        _remove_quiet(tmp)
        notes.append("API keys stay in the project folder this time.")
        return
    _remove_quiet(LEGACY_ENV)
    notes.append(f"moved your API keys (.env) to {ENV_PATH}")


def migrate_legacy():
    """Move data that older versions kept in the project folder. Runs on every
    start; does nothing once there's nothing left to move. Returns a list of
    human-readable notes for the log. Raises MigrationStopped when the chats
    cannot be moved safely (old files are then untouched)."""
    ensure_dirs()
    notes = []
    env_backed_up = False
    if os.path.exists(LEGACY_DB):
        if os.path.exists(DB_PATH):
            notes.append(f"old chats left in place: {LEGACY_DB} (a newer store already exists)")
        else:
            env_backed_up = _migrate_chats(notes)

    if os.path.exists(LEGACY_ENV):
        if os.path.exists(ENV_PATH):
            notes.append(f"API keys: using {ENV_PATH}; the old .env in the project folder can be deleted")
        else:
            _migrate_env(notes, env_backed_up)
    return notes


def load_env():
    """Read API keys: the private folder first, then (for old setups) the
    project folder. Values already set in the environment win."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (ENV_PATH, LEGACY_ENV):
        if os.path.exists(path):
            load_dotenv(path, override=False)
