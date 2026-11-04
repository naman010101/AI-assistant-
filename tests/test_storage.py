"""Step 1.5: chat storage as rows, index, migration with a verified backup.
Every database here lives under tmp_path; the real chats.db is never opened."""
import glob
import json
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import storage  # noqa: E402
from storage import Store  # noqa: E402

V1_FIRST = """CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL,
    history TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE turns (conv_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, route TEXT, meta TEXT, stopped INTEGER NOT NULL DEFAULT 0, at REAL,
    PRIMARY KEY (conv_id, seq));"""


def make_v1(path, first_version=False):
    """A version-1 database with varied chats, built with the v1 schema directly."""
    con = sqlite3.connect(path)
    if first_version:
        con.executescript(V1_FIRST)
    else:
        con.executescript(storage.SCHEMA)
    rows = []
    hist = [{"role": "system", "content": "Be brief."},
            {"role": "user", "content": "look \U0001F600 café"},
            {"role": "assistant", "content": "[an image was attached here; images aren't kept after a restart]"}]
    turns = [("user", "look \U0001F600 café", None, None, 0, None, None),
             ("assistant", "a cat", "local-vision", "1.2s", 1, 5.5, None),
             ("note", "Saved to memory", None, None, 0, 6.0, None)]
    extras = [json.dumps({"attachments": [{"name": "a.png", "kind": "image"}], "notes": ["n"],
                          "edits": [{"path": "x", "status": "applied"}], "memory": ["m"], "via": "phone"}),
              None, None]
    chats = [("c1", "Cats", hist, turns, extras, json.dumps({"digest": "earlier stuff"}), 100.0, 300.0),
             ("c2", "Empty", [], [], [], None, 101.0, 200.0),
             ("c3", "Plain", [{"role": "user", "content": "hi"}],
              [("user", "hi", None, None, 0, 1.0, None)], [None], None, 102.0, 250.0)]
    for cid, title, h, ts, ex, meta, created, updated in chats:
        if first_version:
            con.execute("INSERT INTO conversations VALUES (?,?,?,?,?)",
                        (cid, title, json.dumps(h), created, updated))
            for i, t in enumerate(ts):
                con.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?)", (cid, i, *t[:6]))
        else:
            con.execute("INSERT INTO conversations VALUES (?,?,?,?,?,?)",
                        (cid, title, json.dumps(h, ensure_ascii=False), created, updated, meta))
            for i, (t, e) in enumerate(zip(ts, ex)):
                con.execute("INSERT INTO turns VALUES (?,?,?,?,?,?,?,?,?)", (cid, i, *t[:6], e))
    # an orphan turn (v1 had no foreign key on old databases)
    if first_version:
        con.execute("INSERT INTO turns VALUES ('ghost', 0, 'user', 'orphan', NULL, NULL, 0, 1)")
    else:
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("INSERT INTO turns VALUES ('ghost', 0, 'user', 'orphan', NULL, NULL, 0, 1, NULL)")
    con.commit()
    con.close()
    return rows


def v1_reference(path):
    """What the old code returns, via a v1-mode Store."""
    s = Store(path, upgrade=False)
    assert s.version == 1
    out = s.load_all()
    s.close()
    return out


def tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def user_version(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def backups(tmp_path):
    return sorted(glob.glob(str(tmp_path / "backups" / "chats-v1-*.db")))


@pytest.mark.parametrize("first_version", [False, True])
def test_round_trip_migration(tmp_path, first_version):
    p = str(tmp_path / "chats.db")
    make_v1(p, first_version)
    ref = v1_reference(p)
    assert len(ref) == 3
    s = Store(p)
    try:
        assert s.version == 2 and "Backup" in s.upgrade_note
        assert s.load_all() == ref
        for c in ref:
            assert s.get(c["id"]) == c
        assert s.get("nope") is None
    finally:
        s.close()
    assert user_version(p) == 2
    assert tables(p) >= {"chats", "chat_turns", "chat_messages"}
    assert not ({"conversations", "turns"} & tables(p))
    b = backups(tmp_path)
    assert len(b) == 1
    old = sqlite3.connect(b[0])
    assert old.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 3
    assert old.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 5  # 4 real + 1 orphan
    old.close()
    con = sqlite3.connect(p)
    assert con.execute("SELECT COUNT(*) FROM chat_turns WHERE chat_id = 'ghost'").fetchone()[0] == 0
    con.close()


def test_running_twice_makes_no_second_backup(tmp_path):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    s = Store(p)
    first = s.load_all()
    s.close()
    s = Store(p)
    assert s.version == 2 and s.upgrade_note == ""
    assert s.load_all() == first
    s.close()
    assert len(backups(tmp_path)) == 1


CRASH = (
    "import os, sys; sys.path.insert(0, sys.argv[1]); import storage\n"
    "def hook(stage, n):\n"
    "    if n == 1: os._exit(1)\n"
    "storage._migration_hook = hook\n"
    "storage.Store(sys.argv[2])\n"
)


def _kill_tree(proc):
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
    proc.kill()


def test_crash_mid_migration_leaves_old_data_and_reruns(tmp_path):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    before = v1_reference(p)
    proc = subprocess.Popen([sys.executable, "-c", CRASH, ROOT, p])
    try:
        assert proc.wait(timeout=60) == 1
    finally:
        _kill_tree(proc)
        proc.wait(timeout=10)
    assert user_version(p) == 0
    assert {"conversations", "turns"} <= tables(p) and "chats" not in tables(p)
    assert len(backups(tmp_path)) == 1
    s = Store(p)
    try:
        assert s.version == 2 and s.load_all() == before
    finally:
        s.close()
    assert len(backups(tmp_path)) == 2


def test_error_mid_migration_rolls_back_to_v1_mode(tmp_path, monkeypatch, caplog):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    before = v1_reference(p)

    def boom(stage, n):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(storage, "_migration_hook", boom)
    s = Store(p)
    try:
        assert s.version == 1 and "failed" in s.upgrade_note and "Backup" in s.upgrade_note
        assert s.load_all() == before
        s.save("new", "t", [{"role": "user", "content": "x"}], [{"role": "user", "text": "x"}])
        assert s.get("new")["title"] == "t"
    finally:
        s.close()
    assert user_version(p) == 0 and "chats" not in tables(p)
    assert "chat storage upgrade failed" in caplog.text


def test_backup_failure_refuses_migration_low_space(tmp_path, monkeypatch):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    monkeypatch.setattr(storage.shutil, "disk_usage",
                        lambda d: type("U", (), {"free": 1024, "total": 1, "used": 0})())
    s = Store(p)
    try:
        _assert_postponed(s, p, tmp_path)
    finally:
        s.close()


def test_backup_failure_refuses_migration_oserror(tmp_path, monkeypatch):
    p = str(tmp_path / "chats.db")
    make_v1(p)

    def broken(path, backup_dir):
        raise OSError("disk exploded")
    monkeypatch.setattr(storage, "_backup", broken)
    s = Store(p)
    try:
        _assert_postponed(s, p, tmp_path)
        assert "disk exploded" in s.upgrade_note
    finally:
        s.close()


def _assert_postponed(s, p, tmp_path):
    assert s.version == 1 and "backup" in s.upgrade_note.lower()
    assert "chats" not in tables(p) and user_version(p) == 0
    assert not glob.glob(str(tmp_path / "backups" / "*.partial"))
    assert not backups(tmp_path)
    s.save("z", "t", [], [])
    assert s.get("z") is not None


def test_backup_failure_midway_leaves_no_partial(tmp_path, monkeypatch):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    real = sqlite3.connect

    class Bad(sqlite3.Connection):
        def backup(self, *a, **k):
            raise sqlite3.OperationalError("copy failed")
    monkeypatch.setattr(storage.sqlite3, "connect",
                        lambda *a, **k: real(*a, **{**k, "factory": Bad}))
    s = Store(p)
    try:
        assert s.version == 1 and "copy failed" in s.upgrade_note
    finally:
        s.close()
    assert not glob.glob(str(tmp_path / "backups" / "*"))


def test_stale_partial_is_removed(tmp_path):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    (tmp_path / "backups").mkdir()
    stale = tmp_path / "backups" / "chats-v1-20200101-000000.db.partial"
    stale.write_text("junk")
    Store(p).close()
    assert not stale.exists()


def test_newer_format_raises_and_writes_nothing(tmp_path):
    p = str(tmp_path / "chats.db")
    con = sqlite3.connect(p)
    con.execute("PRAGMA user_version = 9")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError):
        Store(p)
    assert tables(p) == set() and user_version(p) == 9


def _chat(n_turns, tag=""):
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}{tag}"} for i in range(n_turns)]
    turns = [{"role": h["role"], "text": h["content"], "at": float(i)} for i, h in enumerate(hist)]
    return hist, turns


def test_incremental_saves(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    try:
        hist, turns = _chat(50)
        s.save("a", "t", hist, turns, created=1.0)
        h2, t2 = _chat(52)
        h2[:50] = hist
        t2[:50] = turns
        before = s._db.total_changes
        s.save("a", "t", h2, t2, created=1.0)
        assert s._db.total_changes - before <= 5
        assert s.get("a")["created"] == 1.0 and len(s.get("a")["history"]) == 52
        # an edit-status change touches one turn row (+ the chat row's timestamp)
        t3 = [dict(t) for t in t2]
        t3[10]["edits"] = [{"path": "x", "status": "applied"}]
        before = s._db.total_changes
        s.save("a", "t", h2, t3, created=1.0)
        assert s._db.total_changes - before == 2
        assert s.get("a")["turns"][10]["edits"][0]["status"] == "applied"
        # shortened chat loses its tail
        s.save("a", "t", h2[:5], t3[:5], created=1.0)
        c = s.get("a")
        assert len(c["history"]) == 5 and len(c["turns"]) == 5
        n = s._db.execute("SELECT (SELECT COUNT(*) FROM chat_turns), (SELECT COUNT(*) FROM chat_messages)").fetchone()
        assert tuple(n) == (5, 5)
    finally:
        s.close()


def test_images_still_stripped_and_unicode(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    try:
        hist = [{"role": "user", "content": [{"type": "text", "text": "hi \U0001F600"},
                                             {"type": "image_url", "image_url": {"url": "data:AAAA"}}]}]
        s.save("a", "t", hist, [{"role": "user", "text": "hi"}], meta={"digest": "d"})
        c = s.get("a")
        assert "data:" not in json.dumps(c["history"]) and "\U0001F600" in c["history"][0]["content"]
        assert c["meta"] == {"digest": "d"}
        assert s.list()[0]["last"] == "hi" and s.last_change()[1] == 1
    finally:
        s.close()


def test_phone_connection_stays_v1_and_delete_cascades(tmp_path):
    p = str(tmp_path / "chats.db")
    make_v1(p)
    s = Store(p, upgrade=False)
    assert s.version == 1 and not backups(tmp_path) and not (tmp_path / "backups").exists()
    assert s.get("c1")["title"] == "Cats" and s.get("zzz") is None
    s.close()
    assert user_version(p) == 0
    v2 = Store(str(tmp_path / "v2.db"), upgrade=False)
    try:
        hist, turns = _chat(4)
        v2.save("a", "t", hist, turns)
        v2.delete("a")
        n = v2._db.execute("SELECT (SELECT COUNT(*) FROM chat_turns), (SELECT COUNT(*) FROM chat_messages)").fetchone()
        assert tuple(n) == (0, 0) and v2.get("a") is None
    finally:
        v2.close()


def _synthetic_v1(path, n):
    con = sqlite3.connect(path)
    con.executescript(storage.SCHEMA)
    for i in range(n):
        hist, turns = _chat(20, f" chat {i} " + "x" * 400)
        con.execute("INSERT INTO conversations VALUES (?,?,?,?,?,NULL)",
                    (f"id{i}", f"Chat {i}", json.dumps(hist), 1000.0 + i, 2000.0 + i))
        con.executemany("INSERT INTO turns VALUES (?,?,?,?,NULL,NULL,0,?,NULL)",
                        [(f"id{i}", j, t["role"], t["text"], t["at"]) for j, t in enumerate(turns)])
    con.commit()
    con.close()


def _median_get_ms(s, n):
    ids = [f"id{random.randrange(n)}" for _ in range(20)]
    times = []
    for cid in ids:
        t0 = time.perf_counter()
        assert s.get(cid) is not None
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def test_speed_2000_chats(tmp_path, capsys):
    n = 2000
    # fresh v2 database
    s = Store(str(tmp_path / "fresh.db"))
    for i in range(n):
        hist, turns = _chat(20, f" chat {i} " + "x" * 400)
        s.save(f"id{i}", f"Chat {i}", hist, turns)
    fresh = _median_get_ms(s, n)
    s.close()
    # migrated v1 database
    p = str(tmp_path / "old.db")
    _synthetic_v1(p, n)
    t0 = time.perf_counter()
    s = Store(p)
    took = time.perf_counter() - t0
    assert s.version == 2
    migrated = _median_get_ms(s, n)
    s.close()
    # the old code, for comparison
    q = str(tmp_path / "old2.db")
    _synthetic_v1(q, 200)
    s1 = Store(q, upgrade=False)
    v1_ms = _median_get_ms(s1, 200)
    s1.close()
    with capsys.disabled():
        print(f"\n[1.5 timing] get median: fresh v2 {fresh:.2f} ms, migrated {migrated:.2f} ms, "
              f"v1 direct lookup at 200 chats {v1_ms:.2f} ms; migrating {n} chats incl. backup {took:.2f} s")
    assert fresh < 50 and migrated < 50
