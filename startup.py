"""Start-up checks that run before the main window exists (v2 step 1.9).

- Only one copy of the app may run per data folder (a named Windows mutex).
- Moving old data out of the project folder never crashes the app: if it
  can't be done safely the user sees a message and can Retry or Cancel.

ctypes and the standard library only. With the environment variable
HYBRIDAI_NO_DIALOGS=1 (tests only) messages go to stderr instead of a box.
"""
import hashlib
import logging
import os
import sys

import config

log = logging.getLogger("hybridai")

ERROR_ALREADY_EXISTS = 183
_mutex_handles = []  # kept for the life of the process, never closed

ALREADY_RUNNING = ("Hybrid AI is already running. Switching to it.\n\n"
                   "If you can't see it, it may still be starting; "
                   "if it is stuck, end pythonw.exe in Task Manager.")


def mutex_name(data_dir):
    key = os.path.normcase(os.path.abspath(data_dir))
    return "Local\\HybridAI-" +hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def acquire_single(name):
    """True if this process now owns the mutex, False if another one does."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    ctypes.set_last_error(0)
    handle = k32.CreateMutexW(None, False, name)
    err = ctypes.get_last_error()
    if not handle:
        log.warning("couldn't create the single-copy mutex (error %s); starting anyway", err)
        return True
    if err == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)  # don't keep a reference to someone else's mutex
        return False
    _mutex_handles.append(handle)
    return True


def message_box(text, retry=False):
    """Show a message. Returns "ok", or "retry"/"cancel" when retry=True."""
    if os.environ.get("HYBRIDAI_NO_DIALOGS"):
        print(text, file=sys.stderr)
        return "cancel" if retry else "ok"
    if os.name != "nt":
        print(text, file=sys.stderr)
        return "cancel" if retry else "ok"
    import ctypes
    flags = 0x5 if retry else 0x0  # MB_RETRYCANCEL / MB_OK
    flags |= 0x30 if retry else 0x40  # warning / information icon
    flags |= 0x10000  # MB_SETFOREGROUND
    result = ctypes.windll.user32.MessageBoxW(None, text, config.WINDOW_TITLE, flags)
    return "retry" if result == 4 else ("ok" if result == 1 else "cancel")


def focus_existing():
    """Bring the first copy's window forward. Does nothing if it isn't found."""
    if os.name != "nt" or os.environ.get("HYBRIDAI_NO_DIALOGS"):
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW("TkTopLevel", config.WINDOW_TITLE)
        if hwnd:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
    except Exception:
        log.exception("couldn't bring the running window forward")


def ensure_single_copy():
    """Exit quietly (code 0) if another copy already runs for this data folder."""
    if acquire_single(mutex_name(config.DATA_DIR)):
        return
    message_box(ALREADY_RUNNING)
    focus_existing()
    sys.exit(0)


def migrate_or_exit(ask=message_box):
    """Run config.migrate_legacy(). If it can't be done safely, show why and let
    the user Retry; Cancel exits so an empty chats.db is never created over the
    old chats."""
    while True:
        try:
            return config.migrate_legacy()
        except config.MigrationStopped as stop:
            log.error("migration stopped: %s", stop.message)
            if ask(stop.message, retry=True) != "retry":
                sys.exit(0)
