"""Tests for log_setup.py (v2 step 0.2). Never touches the real data folder."""
import logging
import logging.handlers
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
import log_setup  # noqa: E402


@pytest.fixture
def logger(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    lg = logging.getLogger("hybridai")
    saved = list(lg.handlers)
    lg.handlers = []
    yield log_setup.setup()
    for h in lg.handlers:
        h.close()
    lg.handlers = saved


def _handlers(lg):
    return [h for h in lg.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]


def test_creates_file_and_rotation_settings(logger, tmp_path):
    logger.info("hello")
    path = tmp_path / "logs" / "app.log"
    assert path.exists()
    (h,) = _handlers(logger)
    assert h.maxBytes == 1_000_000 and h.backupCount == 5
    assert h.encoding == "utf-8"
    assert logger.name == "hybridai"


def test_idempotent(logger):
    assert log_setup.setup() is logger
    log_setup.setup()
    assert len(_handlers(logger)) == 1


def test_error_line_written(logger, tmp_path):
    try:
        raise ConnectionError("ollama gone")
    except ConnectionError:
        logger.error("boom", exc_info=True)
    text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "ERROR" in text and "boom" in text and "ollama gone" in text


def test_stream_failure_is_logged(logger, tmp_path, monkeypatch):
    import router

    def bad_stream(*a, **k):
        raise ConnectionError("ollama stopped mid-reply")

    monkeypatch.setattr(router, "_stream", bad_stream)
    monkeypatch.setattr(router, "prepare", lambda *a, **k: [])
    router.stream_chat([], "hi", mode="local-general")
    text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "model stream failed" in text and "ollama stopped mid-reply" in text


def test_setup_safe_survives_a_logging_failure(monkeypatch, capsys):
    import log_setup

    def boom():
        raise PermissionError("logs folder is locked")

    monkeypatch.setattr(log_setup, "setup", boom)
    assert log_setup.setup_safe() is None  # no exception escapes
    assert "file logging is off" in capsys.readouterr().err
