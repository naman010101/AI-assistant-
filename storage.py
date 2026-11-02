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
    updated  REAL NOT NULL,
    meta     TEXT              -- JSON: digest of older messages etc.
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
    extra    TEXT,             -- JSON: attachments shown under the message
    PRIMARY KEY (conv_id, seq)
);
"""


def _turn_from_row(row):
    t = {k: row[k] for k in row.keys() if k != "extra"}
    t["stopped"] = bool(row["stopped"])
    if row["extra"]:
        try:
            t.update(json.loads(row["extra"]))
        except ValueError:
            pass
    return t


def _extra(turn):
    extra = {k: turn[k] for k in ("attachments", "notes", "edits", "memory", "via") if turn.get(k)}
    return json.dumps(extra, ensure_ascii=False) if extra else None


def _strip_images(history):
    """Images are kept in memory while the app runs (so you can ask follow-up
    questions about them) but not written to disk: they'd make the database
    huge. Each image is replaced by a short note."""
    out = []
    for m in history:
        c = m.get("content")
        if isinstance(c, list):
            parts = []
            for part in c:
                if part.get("type") == "image_url":
                    parts.append({"type": "text", "text": "[an image was attached here; images aren't kept after a restart]"})
                else:
                    parts.append(part)
            m = {**m, "content": "\n\n".join(p["text"] for p in parts)}
        out.append(m)
    return out


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        # timeout: wait (instead of failing) if the other program is mid-write
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")  # lets another process read while we write
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(turns)")}
        if "extra" not in cols:  # databases made by the first version
            self._db.execute("ALTER TABLE turns ADD COLUMN extra TEXT")
        ccols = {r[1] for r in self._db.execute("PRAGMA table_info(conversations)")}
        if "meta" not in ccols:
            self._db.execute("ALTER TABLE conversations ADD COLUMN meta TEXT")
        self._db.commit()

    def load_all(self):
        """Every saved conversation, most recently used first."""
        with self._lock:
            convs = self._db.execute(
                "SELECT id, title, history, created, updated, meta FROM conversations "
                "ORDER BY updated DESC").fetchall()
            out = []
            for c in convs:
                turns = self._db.execute(
                    "SELECT role, text, route, meta, stopped, at, extra FROM turns "
                    "WHERE conv_id = ? ORDER BY seq", (c["id"],)).fetchall()
                out.append({
                    "id": c["id"], "title": c["title"], "created": c["created"],
                    "updated": c["updated"], "history": json.loads(c["history"]),
                    "meta": json.loads(c["meta"]) if c["meta"] else {},
                    "turns": [_turn_from_row(t) for t in turns],
                })
            return out

    def get(self, conv_id):
        """One conversation, or None."""
        for c in self.load_all():  # small data; keeps one code path for reading
            if c["id"] == conv_id:
                return c
        return None

    def list(self):
        """Titles only, most recently used first (for the phone's chat list)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.id, c.title, c.updated, "
                "(SELECT text FROM turns t WHERE t.conv_id = c.id ORDER BY seq DESC LIMIT 1) AS last "
                "FROM conversations c ORDER BY c.updated DESC").fetchall()
            return [dict(r) for r in rows]

    def last_change(self):
        """(newest update time, number of chats) — cheap way to notice that
        another program (the phone server) changed something."""
        with self._lock:
            r = self._db.execute("SELECT MAX(updated), COUNT(*) FROM conversations").fetchone()
            return (r[0] or 0.0, r[1])

    def save(self, conv_id, title, history, turns, created=None, meta=None):
        """Write one conversation (insert or replace). Conversations are small,
        so rewriting all its turns is simpler than tracking what changed."""
        now = time.time()
        with self._lock, self._db:  # `with self._db` = one transaction
            self._db.execute(
                "INSERT INTO conversations (id, title, history, created, updated, meta) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "title = excluded.title, history = excluded.history, updated = excluded.updated, "
                "meta = excluded.meta",
                (conv_id, title, json.dumps(_strip_images(history), ensure_ascii=False),
                 created or now, now, json.dumps(meta, ensure_ascii=False) if meta else None))
            self._db.execute("DELETE FROM turns WHERE conv_id = ?", (conv_id,))
            self._db.executemany(
                "INSERT INTO turns (conv_id, seq, role, text, route, meta, stopped, at, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(conv_id, i, t["role"], t["text"], t.get("route"), t.get("meta"),
                  int(bool(t.get("stopped"))), t.get("at"),
                  _extra(t)) for i, t in enumerate(turns)])

    def delete(self, conv_id):
        with self._lock, self._db:
            self._db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    def close(self):
        with self._lock:
            self._db.close()
