"""Tests for the non-UI parts: data folder + migration, settings, access gate,
attachments, storage. Run from the project folder:

    venv\\Scripts\\python -m pytest tests -q
"""
import base64
import importlib
import io
import json
import os
import shutil
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fake home, project folder and data folder for each test."""
    home = tmp_path / "home"
    for d in ("Desktop", "Documents", "Downloads", ".ssh", "Other"):
        (home / d).mkdir(parents=True)
    data = tmp_path / "appdata" / "HybridAI"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HYBRID_AI_DATA_DIR", str(data))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    import config
    importlib.reload(config)
    import settings
    importlib.reload(settings)
    import access
    importlib.reload(access)
    return {"home": home, "data": data, "tmp": tmp_path, "config": config,
            "settings": settings, "access": access}


# ---------------------------------------------------------------- config
def test_migration_moves_chats_and_env(env, monkeypatch):
    config = env["config"]
    proj = env["tmp"] / "project"
    (proj / "data").mkdir(parents=True)
    legacy_db = proj / "data" / "chats.db"
    live_db = env["tmp"] / "live" / "chats.db"  # the "running app" copy; never moved
    live_db.parent.mkdir()
    con = sqlite3.connect(live_db)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit()
    # Snapshot the files while the connection is open, as a crash would leave
    # them: the row is then only in the -wal file. Then close the connection:
    # Windows cannot move a file that is still open, and the real start-up
    # (app.py) runs migrate_legacy before anything opens the database.
    for suffix in ("", "-wal"):
        shutil.copy(str(live_db) + suffix, str(legacy_db) + suffix)
    con.close()
    (proj / ".env").write_text("GEMINI_API_KEY=abc\n")
    monkeypatch.setattr(config, "LEGACY_DB", str(legacy_db))
    monkeypatch.setattr(config, "LEGACY_ENV", str(proj / ".env"))

    notes = config.migrate_legacy()
    assert any("moved your chats" in n for n in notes)
    assert any("API keys" in n for n in notes)
    assert not legacy_db.exists() and not (proj / ".env").exists()
    assert not (proj / "data").exists()  # empty folder removed
    moved = sqlite3.connect(config.DB_PATH)
    assert moved.execute("SELECT x FROM t").fetchone() == (42,)  # WAL content kept
    assert open(config.ENV_PATH).read().startswith("GEMINI_API_KEY")
    assert config.migrate_legacy() == []  # second run: nothing to do


def test_migration_never_overwrites(env, monkeypatch):
    config = env["config"]
    config.ensure_dirs()
    open(config.ENV_PATH, "w").write("NEW=1")
    old = env["tmp"] / ".env"
    old.write_text("OLD=1")
    monkeypatch.setattr(config, "LEGACY_ENV", str(old))
    monkeypatch.setattr(config, "LEGACY_DB", str(env["tmp"] / "nope.db"))
    notes = config.migrate_legacy()
    assert open(config.ENV_PATH).read() == "NEW=1" and old.exists()
    assert "can be deleted" in notes[0]


# ---------------------------------------------------------------- settings
def test_settings_defaults_and_persist(env):
    s = env["settings"].Settings()
    folders = s.access["folders"]
    assert [os.path.basename(f) for f in folders] == ["Desktop", "Documents", "Downloads"]
    s.set("model", "local-coder")
    s.set_access("read_files", False)
    s2 = env["settings"].Settings()
    assert s2.get("model") == "local-coder" and s2.access["read_files"] is False
    assert s2.get("context_tokens") == 8192  # default still there


def test_settings_survive_corrupt_file(env):
    config = env["config"]
    config.ensure_dirs()
    open(config.SETTINGS_PATH, "w").write("{not json")
    s = env["settings"].Settings()
    assert s.get("cloud_files") == "ask"


# ---------------------------------------------------------------- access
def _gate(env):
    s = env["settings"].Settings()
    return env["access"].AccessGate(s), s


def test_access_rules(env):
    gate, s = _gate(env)
    home = env["home"]
    ok_file = home / "Documents" / "notes.txt"
    ok_file.write_text("hi")
    assert gate.check(str(ok_file)) == (True, "")

    outside = home / "Other" / "x.txt"
    outside.write_text("x")
    ok, reason = gate.check(str(outside))
    assert not ok and gate.needs_prompt(reason)

    # secrets are blocked even inside an allowed folder
    for name in (".env", "server.pem", "id_rsa", "credentials.json", "client_secret_123.json"):
        f = home / "Documents" / name
        f.write_text("secret")
        ok, reason = gate.check(str(f))
        assert not ok and not gate.needs_prompt(reason), name
    key = home / ".ssh" / "config"
    key.write_text("x")
    assert gate.check(str(key))[0] is False

    # the app's own data folder is blocked even if someone allows it
    env["config"].ensure_dirs()
    db = env["data"] / "chats.db"
    db.write_text("x")
    gate.allow_always(str(db))
    assert gate.check(str(db))[0] is False


def test_access_traversal_and_switch(env):
    gate, s = _gate(env)
    home = env["home"]
    (home / "Other" / "y.txt").write_text("y")
    sneaky = os.path.join(str(home / "Documents"), "..", "Other", "y.txt")
    assert gate.needs_prompt(gate.check(sneaky)[1])  # ".." doesn't escape the rules
    (home / "Documents" / "a.txt").write_text("a")
    s.set_access("read_files", False)
    ok, reason = gate.check(str(home / "Documents" / "a.txt"))
    assert not ok and "turned off" in reason


def test_access_allow_once_and_always(env):
    gate, s = _gate(env)
    f = env["home"] / "Other" / "z.txt"
    f.write_text("z")
    gate.allow_once(str(f))
    assert gate.check(str(f))[0]
    gate2 = env["access"].AccessGate(env["settings"].Settings())
    assert not gate2.check(str(f))[0]  # "once" is forgotten on restart
    gate.allow_always(str(f))
    gate3 = env["access"].AccessGate(env["settings"].Settings())
    assert gate3.check(str(f))[0]
    gate3.remove_folder(str(env["home"] / "Other"))
    assert not gate3.check(str(f))[0]


# ---------------------------------------------------------------- attachments
import attachments as A  # noqa: E402


def test_text_and_code(tmp_path):
    p = tmp_path / "hello.py"
    p.write_text("def hi():\n    return 1\n", encoding="utf-8")
    a = A.load_file(str(p))
    assert a.kind == "code" and "def hi" in a.text and a.detail == "3 lines"
    w = tmp_path / "win.txt"
    w.write_bytes("caf\u00e9 r\u00e9sum\u00e9".encode("cp1252"))
    assert "résumé" in A.load_file(str(w)).text
    u16 = tmp_path / "u16.txt"
    u16.write_bytes("hello utf16".encode("utf-16"))
    assert A.load_file(str(u16)).text == "hello utf16"


def test_binary_and_unsupported(tmp_path):
    b = tmp_path / "x.bin"
    b.write_bytes(b"\x00\x01\x02" * 100)
    with pytest.raises(A.AttachError):
        A.load_file(str(b))
    d = tmp_path / "old.doc"
    d.write_bytes(b"x")
    with pytest.raises(A.AttachError, match="Save it as .docx"):
        A.load_file(str(d))


def test_pdf(tmp_path):
    pytest.importorskip("reportlab", reason="reportlab is only used to build this test PDF and is not in requirements.txt")
    from reportlab.pdfgen import canvas
    p = tmp_path / "notes.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(72, 720, "Dynamic programming notes page one")
    c.showPage()
    c.drawString(72, 720, "Second page about memoization")
    c.save()
    a = A.load_file(str(p))
    assert a.kind == "pdf" and a.detail == "2 pages"
    assert "page 1" in a.text and "memoization" in a.text


def test_docx_xlsx_notebook(tmp_path):
    import docx
    import openpyxl
    d = docx.Document()
    d.add_heading("Resume", level=1)
    d.add_paragraph("Python, PyTorch, SQL")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Skill", "Level"
    d.save(tmp_path / "cv.docx")
    a = A.load_file(str(tmp_path / "cv.docx"))
    assert "# Resume" in a.text and "PyTorch" in a.text and "Skill | Level" in a.text

    wb = openpyxl.Workbook()
    wb.active.append(["name", "marks"])
    wb.active.append(["DSA", 91])
    wb.save(tmp_path / "m.xlsx")
    a = A.load_file(str(tmp_path / "m.xlsx"))
    assert "DSA,91" in a.text and a.detail == "1 sheet"

    nb = {"cells": [{"cell_type": "markdown", "source": ["# Title"]},
                    {"cell_type": "code", "source": ["import numpy as np\n", "x = 1"]}]}
    (tmp_path / "n.ipynb").write_text(json.dumps(nb))
    a = A.load_file(str(tmp_path / "n.ipynb"))
    assert "[cell 2: code]" in a.text and "import numpy" in a.text


def test_images(tmp_path):
    from PIL import Image
    img = Image.new("RGB", (3200, 1800), "red")
    a = A.load_image(img)
    assert a.is_image and a.detail == "3200×1800" and a.image_url.startswith("data:image/jpeg;base64,")
    small = Image.open(io.BytesIO(base64.b64decode(a.image_url.split(",", 1)[1])))
    assert max(small.size) == A.IMAGE_MAX_SIDE  # shrunk
    rgba = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    rgba.save(tmp_path / "t.png")
    a = A.load_file(str(tmp_path / "t.png"))
    assert a.image_url.startswith("data:image/png") and a.path


def test_find_paths_with_spaces(tmp_path):
    folder = tmp_path / "NAMAN SALUJA" / "My Docs"
    folder.mkdir(parents=True)
    f = folder / "dsa notes.pdf"
    f.write_text("x")
    g = tmp_path / "a.py"
    g.write_text("x")
    if os.name != "nt":  # find_paths looks for drive letters; fake one on Linux
        pytest.skip("drive-letter paths are Windows-only; covered in test_find_paths_windows_style")
    text = f"explain {f} and also \"{g}\" please"
    assert A.find_paths(text) == [str(g), str(f)] or set(A.find_paths(text)) == {str(f), str(g)}


def test_find_paths_windows_style(monkeypatch):
    existing = {r"C:\Users\NAMAN SALUJA\Documents\dsa notes.pdf", r"D:\code\main.py"}
    monkeypatch.setattr(A.os.path, "isfile", lambda p: p in existing)
    text = (r"explain C:\Users\NAMAN SALUJA\Documents\dsa notes.pdf in simple words, "
            r"then check 'D:\code\main.py'. Not C:\nope\x.txt")
    assert set(A.find_paths(text)) == existing


def test_build_message_budget_and_images():
    big = A.Attachment(name="big.txt", kind="text", text="x" * 100_000)
    small = A.Attachment(name="small.py", kind="code", text="print(1)")
    content, notes = A.build_message("what is this?", [big, small], context_tokens=8192)
    assert isinstance(content, str)
    assert "print(1)" in content and 'note="cut: first' in content
    assert len(content) < 8192 * 0.6 * 3.5 + 1000
    assert len(notes) == 1 and "big.txt" in notes[0]
    img = A.Attachment(name="s.png", kind="image", image_url="data:image/png;base64,AAAA")
    parts, notes = A.build_message("", [img])
    assert parts[0]["text"].endswith("Describe this image.")
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


# ---------------------------------------------------------------- storage
def test_storage_images_stripped_and_extra(tmp_path):
    from storage import Store
    s = Store(str(tmp_path / "c.db"))
    hist = [{"role": "user", "content": [{"type": "text", "text": "look"},
                                         {"type": "image_url", "image_url": {"url": "data:..."}}]},
            {"role": "assistant", "content": "a cat"}]
    turns = [{"role": "user", "text": "look", "attachments": [{"name": "s.png", "kind": "image"}],
              "notes": ["n1"]},
             {"role": "assistant", "text": "a cat", "route": "local-vision", "meta": "m"}]
    s.save("c1", "t", hist, turns)
    c = s.get("c1")
    assert isinstance(c["history"][0]["content"], str) and "data:" not in json.dumps(c["history"])
    assert c["turns"][0]["attachments"][0]["name"] == "s.png" and c["turns"][0]["notes"] == ["n1"]
    assert "attachments" not in c["turns"][1]


def test_storage_upgrades_old_db(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("""CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL,
        history TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE turns (conv_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
        text TEXT NOT NULL, route TEXT, meta TEXT, stopped INTEGER NOT NULL DEFAULT 0, at REAL,
        PRIMARY KEY (conv_id, seq));
        INSERT INTO conversations VALUES ('a', 'old chat', '[]', 1, 1);
        INSERT INTO turns VALUES ('a', 0, 'user', 'hi', NULL, NULL, 0, 1);""")
    con.commit()
    con.close()
    from storage import Store
    s = Store(str(p))
    assert s.get("a")["turns"][0]["text"] == "hi"


# ---------------------------------------------------------------- router
def test_router_manual_and_text_only(monkeypatch):
    try:
        import openai  # noqa: F401
    except ImportError:  # not installed in this environment: a stand-in is enough here
        import types
        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=lambda **k: object()))
    import router
    assert router.route("hello")[0] == "local-general"
    assert router.route("x", model_override="cloud-gemini") == ("cloud-gemini", "Selected manually")
    hist = [{"role": "user", "content": [{"type": "text", "text": "q"},
                                         {"type": "image_url", "image_url": {"url": "data:x"}}]}]
    assert isinstance(router.for_model("local-general", hist)[0]["content"], str)
    assert router.for_model("local-vision", hist) is hist
