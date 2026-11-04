"""Tests for editor.py (Phase 2: proposed edits, checks, apply, undo)."""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def ed(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_AI_DATA_DIR", str(tmp_path / "data"))
    import config
    importlib.reload(config)
    import editor
    importlib.reload(editor)
    return editor


SRC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"


def _file(tmp_path, name="calc.py", text=SRC, newline="\n", enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.replace("\n", newline).encode(enc))
    return str(p)


def test_extract_edit_block(ed, tmp_path):
    path = _file(tmp_path)
    files = [{"name": "calc.py", "path": path, "sha": ed.sha_file(path)}]
    reply = ("I added a docstring.\n\n```edit:calc.py\ndef add(a, b):\n    \"\"\"Add.\"\"\"\n"
             "    return a + b\n```\nDone.")
    props = ed.extract(reply, files, "add docstrings")
    assert len(props) == 1 and props[0]["path"] == path and '"""Add."""' in props[0]["new_text"]
    assert props[0]["base_sha"] == files[0]["sha"] and props[0]["status"] == "pending"
    # language before edit: and odd spacing
    reply2 = "```python edit: calc.py\nx = 1\n```"
    assert ed.extract(reply2, files)[0]["new_text"] == "x = 1\n"


def test_extract_question_only_and_fallback(ed, tmp_path):
    path = _file(tmp_path)
    files = [{"name": "calc.py", "path": path}]
    # a question: an example snippet must NOT become an edit
    assert ed.extract("You call it like:\n```python\nadd(1, 2)\n```", files, "how do I use add?") == []
    # model ignored edit: format but rewrote the whole file after a change request
    rewritten = SRC.replace("return a - b", "return a - b  # subtract")
    reply = f"Here you go:\n```python\n{rewritten}```"
    props = ed.extract(reply, files, "add a comment to sub")
    assert len(props) == 1 and "# subtract" in props[0]["new_text"]
    # same rewrite but the user didn't ask for a change -> no edit
    assert ed.extract(reply, files, "what does this file do?") == []


def test_checks(ed, tmp_path):
    path = _file(tmp_path)
    sha = ed.sha_file(path)
    assert ed.checks(path, SRC, SRC.replace("a + b", "b + a"), sha) == []
    w = ed.checks(path, SRC, "def add(a, b)\n    return a + b\n", sha)
    assert any(level == "error" and "syntax error" in m for level, m in w)
    long_src = "\n".join(f"x{i} = {i}" for i in range(40)) + "\n"
    w = ed.checks(path, long_src, "x0 = 0\n# ... rest of the code unchanged\n")
    assert any("placeholder" in m for _, m in w)
    w = ed.checks(path, long_src, "x0 = 0\nx1 = 1\n")
    assert any("much shorter" in m for _, m in w)
    open(path, "a").write("# edited elsewhere\n")
    w = ed.checks(path, SRC, SRC, sha)
    assert any("changed on disk" in m for _, m in w) and any("identical" in m for _, m in w)
    jpath = _file(tmp_path, "c.json", '{"a": 1}\n')
    assert any("JSON" in m for _, m in ed.checks(jpath, '{"a": 1}\n', '{"a": 1,}\n'))


def test_diff_rows_and_stats(ed):
    old = "\n".join(f"line {i}" for i in range(1, 31)) + "\n"
    new = old.replace("line 15", "line fifteen").replace("line 30\n", "line 30\nline 31\n")
    rows = ed.diff_rows(old, new)
    tags = [r[0] for r in rows]
    assert "replace" in tags and "insert" in tags and "skip" in tags
    rep = next(r for r in rows if r[0] == "replace")
    assert rep[1] == 15 and rep[2] == "line 15" and rep[4] == "line fifteen"
    assert ed.stats(old, new) == (2, 1)
    # unchanged file: everything shown, nothing hidden
    assert all(r[0] == "equal" for r in ed.diff_rows(old, old))


def test_apply_keeps_format_and_undo(ed, tmp_path):
    path = _file(tmp_path, "win.py", SRC, newline="\r\n", enc="utf-8-sig")
    new = SRC.replace("a + b", "b + a")
    rec = ed.apply(path, new, conv_id="c1")
    raw = open(path, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw and b"b + a" in raw  # BOM + CRLF kept
    assert os.path.exists(rec["backup"]) and b"a + b" in open(rec["backup"], "rb").read()
    assert ed.recent_edits()[0]["id"] == rec["id"]
    ed.undo(rec)
    assert b"a + b" in open(path, "rb").read()
    assert ed.recent_edits()[0]["undone"] is True


def test_undo_refuses_if_changed_since(ed, tmp_path):
    path = _file(tmp_path)
    rec = ed.apply(path, SRC.replace("a - b", "b - a"))
    open(path, "a").write("# later change\n")
    with pytest.raises(ed.EditError, match="changed-since"):
        ed.undo(rec)
    ed.undo(rec, force=True)
    assert open(path).read() == SRC


def test_cp1252_file_stays_cp1252(ed, tmp_path):
    text = "# résumé notes\nx = 1\n"
    path = _file(tmp_path, "notes.py", text, enc="cp1252")
    ed.apply(path, text.replace("x = 1", "x = 2"))
    assert open(path, "rb").read().decode("cp1252") == "# résumé notes\nx = 2\n"


def _blocks(w):
    return [m for level, m in w if level == "block"]


def test_checks_blocks_privacy_placeholder(ed, tmp_path):
    path = _file(tmp_path)
    new = SRC.replace("a + b", "a + b + 0  # key [API_KEY_1]")
    b = _blocks(ed.checks(path, SRC, new))
    assert len(b) == 1 and "Not applied" in b[0] and "[API_KEY_1]" in b[0]
    assert "Ask again or edit by hand" in b[0]
    for ph in ("[EMAIL_2]", "[TOKEN_3]", "[PRIVATE_KEY_1]", "[SECRET_1]", "[PATH_12]"):
        assert _blocks(ed.checks(path, SRC, SRC + f"x = '{ph}'\n"))


def test_checks_blocks_lowercase_placeholder(ed, tmp_path):
    path = _file(tmp_path)
    b = _blocks(ed.checks(path, SRC, SRC + "x = '[api_key_1]'\n"))
    assert b and "[API_KEY_1]" in b[0]


def test_checks_ordinary_brackets_do_not_block(ed, tmp_path):
    path = _file(tmp_path)
    new = SRC + "a = [1]\nb = list[0]\nc = '[PATH]'\nd = '[EMAIL_x]'\n"
    assert _blocks(ed.checks(path, SRC, new)) == []


def test_checks_placeholder_already_in_old_does_not_block(ed, tmp_path):
    path = _file(tmp_path)
    old = SRC + "KEY = '[API_KEY_1]'\n"
    assert _blocks(ed.checks(path, old, old.replace("a + b", "b + a"))) == []
    assert _blocks(ed.checks(path, old, old.replace("[API_KEY_1]", "[api_key_1]"))) == []
    # a different, new placeholder still blocks
    assert _blocks(ed.checks(path, old, old + "T = '[TOKEN_1]'\n"))
