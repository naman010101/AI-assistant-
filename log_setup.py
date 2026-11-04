"""One app-wide logger, written to <private data folder>\\logs\\app.log.

Rotates at 1 MB and keeps 5 old files. Call setup() once at start-up; other
modules just use logging.getLogger("hybridai").
"""
import logging
import logging.handlers
import os
import sys

import config

LOGGER_NAME = "hybridai"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5
_TAG = "_hybridai_app_log"


def log_path():
    return os.path.join(config.DATA_DIR, "logs", "app.log")


def setup():
    """Attach the rotating file handler (once) and return the logger."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if any(getattr(h, _TAG, False) for h in logger.handlers):
        return logger
    path = log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    setattr(handler, _TAG, True)
    logger.addHandler(handler)
    return logger


def setup_safe():
    """setup(), but a logging problem (read-only folder, antivirus or OneDrive
    lock...) must never stop the app from starting: print one warning and carry
    on without file logging. Returns the logger, or None if it couldn't be set up."""
    try:
        return setup()
    except Exception as e:  # noqa: BLE001 - this is the whole point
        # print() does nothing if there is no console (pythonw), so no guard needed
        print(f"Hybrid AI: file logging is off ({type(e).__name__}: {e})", file=sys.stderr)
        return None
