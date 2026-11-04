"""Step 1.9: the legacy migration is safe. Temp folders only."""
import hashlib
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_core import env  # noqa: E402,F401,F811  (fixture)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture()
def legacy(env, monkeypatch):
    config = env["config"]
    proj = env["tmp"] / "project"
    (proj / "data").mkdir(parents=True)
    db = proj / "data" / "chats.db"
    monkeypatch.setattr(config, "LEGACY_DB", str(db))
    monkeypatch.setattr(config, "LEGACY_ENV", str(proj / ".env"))
    return {"db": db, "env": proj / ".env", "config": config, "tmp": env["tmp"]}


def _make_db(path, rows=3, keep_open=False):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE conversations (id TEXT)")
    con.execute("CREATE TABLE turns (conv_id TEXT)")
    for i in range(rows):
        con.execute("INSERT INTO conversations VALUES (?)", (str(i),))
    con.commit()
    if keep_open:
        return con
    con.close()


def _db_with_wal_only_row(db):
    """Leave a legacy chats.db whose last row lives only in the -wal file."""
    live_path = str(db) + ".live"
    live = _make_db(live_path, rows=1, keep_open=True)
    live.execute("INSERT INTO conversations VALUES ('wal-only')")
    live.commit()
    for suffix in ("", "-wal"):
        shutil.copy(live_path + suffix, str(db) + suffix)
    live.close()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(live_path + suffix):
            os.remove(live_path + suffix)


def _no_backups(config):
    return not any(n.startswith("migration-") for n in os.listdir(config.BACKUP_DIR))


def test_held_file_stops_migration_and_changes_nothing(legacy):
    config = legacy["config"]
    holder = _make_db(str(legacy["db"]), keep_open=True)
    try:
        before = _sha(legacy["db"])
        with pytest.raises(config.MigrationStopped) as e:
            config.migrate_legacy()
        assert "open in another program" in e.value.message
        assert _sha(legacy["db"]) == before
        assert not os.path.exists(config.DB_PATH)
        assert _no_backups(config)
    finally:
        holder.close()


def test_replace_failure_stops_and_cleans_up(legacy, monkeypatch):
    config = legacy["config"]
    _make_db(str(legacy["db"]))
    before = _sha(legacy["db"])

    def boom(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(config.os, "replace", boom)
    with pytest.raises(config.MigrationStopped):
        config.migrate_legacy()
    assert _sha(legacy["db"]) == before
    assert not os.path.exists(config.DB_PATH)
    assert not os.path.exists(config.DB_PATH + ".migrating")


def test_backup_failure_refuses_migration(legacy, monkeypatch):
    config = legacy["config"]
    _make_db(str(legacy["db"]))
    before = _sha(legacy["db"])

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(config.shutil, "copy2", boom)
    with pytest.raises(config.MigrationStopped) as e:
        config.migrate_legacy()
    assert "back up" in e.value.message
    assert _no_backups(config)
    assert _sha(legacy["db"]) == before and not os.path.exists(config.DB_PATH)


def test_backup_holds_raw_files_and_wal_rows_survive(legacy):
    config = legacy["config"]
    _db_with_wal_only_row(legacy["db"])
    assert os.path.exists(str(legacy["db"]) + "-wal")
    notes = config.migrate_legacy()
    assert any("moved your chats" in n for n in notes)
    folders = [n for n in os.listdir(config.BACKUP_DIR) if n.startswith("migration-")]
    assert len(folders) == 1
    bdir = os.path.join(config.BACKUP_DIR, folders[0])
    assert {"chats.db", "chats.db-wal"} <= set(os.listdir(bdir))
    for path in (os.path.join(bdir, "chats.db"), config.DB_PATH):
        con = sqlite3.connect(path)
        ids = {r[0] for r in con.execute("SELECT id FROM conversations")}
        con.close()
        assert "wal-only" in ids
    assert not legacy["db"].exists()


def test_stale_migrating_file_is_removed(legacy):
    config = legacy["config"]
    _make_db(str(legacy["db"]))
    config.ensure_dirs()
    with open(config.DB_PATH + ".migrating", "wb") as f:
        f.write(b"garbage")
    config.migrate_legacy()
    assert not os.path.exists(config.DB_PATH + ".migrating")
    con = sqlite3.connect(config.DB_PATH)
    assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 3
    con.close()


def test_old_file_removal_failure_is_a_note_not_an_error(legacy, monkeypatch):
    config = legacy["config"]
    _make_db(str(legacy["db"]))
    real_remove = os.remove
    old = str(legacy["db"])

    def fake_remove(p, *a, **k):
        if os.path.abspath(p) == os.path.abspath(old):
            raise PermissionError("in use")
        return real_remove(p, *a, **k)
    monkeypatch.setattr(config.os, "remove", fake_remove)
    notes = config.migrate_legacy()
    assert any("you can delete it" in n for n in notes)
    con = sqlite3.connect(config.DB_PATH)
    assert con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 3
    con.close()


def test_env_replace_failure_keeps_old_env(legacy, monkeypatch):
    config = legacy["config"]
    legacy["env"].write_text("GEMINI_API_KEY=abc\n")
    config.ensure_dirs()

    def boom(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(config.os, "replace", boom)
    notes = config.migrate_legacy()
    assert legacy["env"].exists()
    assert not os.path.exists(config.ENV_PATH) and not os.path.exists(config.ENV_PATH + ".tmp")
    assert any("stay in the project folder" in n for n in notes)


def test_env_is_backed_up_with_the_chats(legacy):
    config = legacy["config"]
    _make_db(str(legacy["db"]))
    legacy["env"].write_text("GEMINI_API_KEY=abc\n")
    config.migrate_legacy()
    folders = [n for n in os.listdir(config.BACKUP_DIR) if n.startswith("migration-")]
    assert len(folders) == 1
    assert ".env" in os.listdir(os.path.join(config.BACKUP_DIR, folders[0]))
    with open(config.ENV_PATH) as f:
        assert f.read().startswith("GEMINI_API_KEY")
