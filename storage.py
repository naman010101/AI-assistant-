"""Saves conversations to disk so they survive closing the app.

Everything lives in one SQLite file (data/chats.db by default). SQLite ships
with Python, so there's nothing to install, and the same file can later be
read by other parts of the project (e.g. a server the phone app talks to).

Two things are stored per conversation, on purpose:
  history  the exact message list sent to the model (no error text or
           "[Cloud unavailable]" notices), so the model's context is restored
           exactly as it was
  turns    what the chat window shows, including route and timing details
"""
import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id       TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    history  TEXT NOT NULL DEFAULT '[]',
    created  REAL NOT NULL,
    updated  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    conv_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq      INTEGER NOT NULL,
    role     TEXT NOT NULL,
    text     TEXT NOT NULL,
    route    TEXT,
    meta     TEXT,
    stopped  INTEGER NOT NULL DEFAULT 0,
    at       REAL,
    PRIMARY KEY (conv_id, seq)
);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")  # lets another process read while we write
        self._db.executescript(SCHEMA)
        self._db.commit()

    def load_all(self):
        """Every saved conversation, most recently used first."""
        with self._lock:
            convs = self._db.execute(
                "SELECT id, title, history, created, updated FROM conversations "
                "ORDER BY updated DESC").fetchall()
            out = []
            for c in convs:
                turns = self._db.execute(
                    "SELECT role, text, route, meta, stopped, at FROM turns "
                    "WHERE conv_id = ? ORDER BY seq", (c["id"],)).fetchall()
                out.append({
                    "id": c["id"], "title": c["title"], "created": c["created"],
                    "updated": c["updated"], "history": json.loads(c["history"]),
                    "turns": [{k: t[k] for k in t.keys()} | {"stopped": bool(t["stopped"])}
                              for t in turns],
                })
            return out

    def save(self, conv_id, title, history, turns, created=None):
        """Write one conversation (insert or replace). Conversations are small,
        so rewriting all its turns is simpler than tracking what changed."""
        now = time.time()
        with self._lock, self._db:  # `with self._db` = one transaction
            self._db.execute(
                "INSERT INTO conversations (id, title, history, created, updated) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "title = excluded.title, history = excluded.history, updated = excluded.updated",
                (conv_id, title, json.dumps(history, ensure_ascii=False), created or now, now))
            self._db.execute("DELETE FROM turns WHERE conv_id = ?", (conv_id,))
            self._db.executemany(
                "INSERT INTO turns (conv_id, seq, role, text, route, meta, stopped, at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(conv_id, i, t["role"], t["text"], t.get("route"), t.get("meta"),
                  int(bool(t.get("stopped"))), t.get("at")) for i, t in enumerate(turns)])

    def delete(self, conv_id):
        with self._lock, self._db:
            self._db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    def close(self):
        with self._lock:
            self._db.close()
