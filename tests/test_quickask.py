"""Tests for quickask.py (Phase 5) - the parts that don't need Windows."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import quickask as Q  # noqa: E402


def test_parse_hotkey():
    assert Q.parse_hotkey("ctrl+shift+space") == (0x0002 | 0x0004, 0x20)
    assert Q.parse_hotkey("Alt + Q") == (0x0001, ord("Q"))
    assert Q.parse_hotkey("win+f9") == (0x0008, 0x78)
    for bad in ("", "space", "ctrl+shift", "ctrl+banana"):
        with pytest.raises(ValueError):
            Q.parse_hotkey(bad)


def test_never_capture():
    assert Q.never_capture({"app": "KeePassXC.exe", "title": "db"}, Q.DEFAULT_NEVER)
    assert "private" in Q.never_capture({"app": "chrome.exe", "title": "New Incognito Tab - Chrome"}, [])
    assert Q.never_capture({"app": "chrome.exe", "title": "HDFC NetBanking"}, [])
    assert Q.never_capture({"app": "Code.exe", "title": "router.py - hybrid-ai"}, Q.DEFAULT_NEVER) == ""


def test_build_prompt():
    p = Q.build_prompt("", selection="def f(): pass", app="Code.exe", title="a.py", action="Explain")
    assert "<selection>" in p and "Explain this clearly" in p and "selected text" in p and "Code.exe" in p
    p = Q.build_prompt("what is this chart?", screen_text="Revenue 2025", app="chrome.exe")
    assert "<screen>" in p and "OCR" in p and p.endswith("what is this chart?")
    assert Q.build_prompt("", app="x.exe").endswith("anything I should know.")


def test_not_windows_is_safe():
    if Q.IS_WINDOWS:
        pytest.skip("Windows")
    hk = Q.HotkeyListener("ctrl+shift+space", lambda: None)
    assert hk.start() is False and "Windows" in hk.error
    assert Q.foreground()["hwnd"] is None and Q.ocr(None) is None and Q.clipboard_has_only_nontext() is False
