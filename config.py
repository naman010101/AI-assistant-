"""Where the app keeps its private data, and moving old data there.

Personal data (chats, memory, settings, backups, API keys) lives OUTSIDE the
project folder, in the standard Windows place for an app's private data:

    %LOCALAPPDATA%\\HybridAI\\      e.g. C:\\Users\\you\\AppData\\Local\\HybridAI

That keeps it out of anything you share (a GitHub repo, a zip for a friend,
a folder connected to an AI tool). The project folder only holds code.

Set HYBRID_AI_DATA_DIR to use a different folder.
"""
import logging
import os
import shutil
import sqlite3

log = logging.getLogger("hybridai")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "HybridAI"


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


def migrate_legacy():
    """Move data that older versions kept in the project folder. Runs on every
    start; does nothing once there's nothing left to move. Returns a list of
    human-readable notes for the log."""
    ensure_dirs()
    notes = []

    # chats.db (+ its -wal/-shm side files). Fold the WAL into the main file
    # first so a single file carries everything.
    if os.path.exists(LEGACY_DB):
        if os.path.exists(DB_PATH):
            notes.append(f"old chats left in place: {LEGACY_DB} (a newer store already exists)")
        else:
            try:
                con = sqlite3.connect(LEGACY_DB)
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.close()
            except sqlite3.Error:
                log.exception("couldn't checkpoint the old chats database before moving it")
            shutil.move(LEGACY_DB, DB_PATH)
            for side in ("-wal", "-shm"):
                if os.path.exists(LEGACY_DB + side):
                    try:
                        os.remove(LEGACY_DB + side)
                    except OSError:
                        log.exception("couldn't remove an old chats side file")
            try:
                os.rmdir(os.path.dirname(LEGACY_DB))  # only if now empty
            except OSError as _exc:
                log.warning("couldn't remove the old data folder: %s", _exc)
            notes.append(f"moved your chats to {DB_PATH}")

    # .env (API keys)
    if os.path.exists(LEGACY_ENV):
        if os.path.exists(ENV_PATH):
            notes.append(f"API keys: using {ENV_PATH}; the old .env in the project folder can be deleted")
        else:
            shutil.move(LEGACY_ENV, ENV_PATH)
            notes.append(f"moved your API keys (.env) to {ENV_PATH}")
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
