"""Saves conversations to disk so they survive closing the app.

Everything lives in one SQLite file (data/chats.db by default). SQLite ships
with Python, so there's nothing to install, and the same file can later be
read by other parts of the project (e.g. a server the phone app talks to).

Three things are stored per conversation, on purpose (format version 2):
  chats          title, times, digest
  chat_messages  one row per message sent to the model (no error text or
                 "[Cloud unavailable]" notices), so the model's context is
                 restored exactly as it was
  chat_turns     one row per message the chat window shows, including route
                 and timing details

Version 1 (tables `conversations` and `turns`, history as one JSON blob) is
upgraded in place at start-up, after a verified backup. If the backup or the
upgrade fails, the app keeps working on the old layout ("version 1 mode").
"""
import json
import logging
import os
import shutil
import sqlite3
import threading
import time

log = logging.getLogger("hybridai")

SCHEMA_VERSION = 2

# The old (version 1) layout. Kept for databases that have not been upgraded.
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

# The version 2 layout (PRAGMA user_version = 2). New table names on purpose: an
# older app opening a v2 database sees an empty list instead of half a chat.
SCHEMA_V2 = [
    """CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL,
        updated REAL NOT NULL, meta TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated DESC)",
    """CREATE TABLE IF NOT EXISTS chat_turns (
        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, route TEXT, meta TEXT,
        stopped INTEGER NOT NULL DEFAULT 0, at REAL, extra TEXT,
        PRIMARY KEY (chat_id, seq))""",
    """CREATE TABLE IF NOT EXISTS chat_messages (
        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY (chat_id, seq))""",
]

TURN_COLS = "role, text, route, meta, stopped, at, extra"

# Tests only: called as hook(stage, n) while migrating (to simulate a crash or an error).
_migration_hook = None


class MigrationSkipped(Exception):
    """The old data changed after the backup was taken; try again next start."""


def _turn_from_row(row):
    t = {k: row[k] for k in row.keys() if k not in ("extra", "chat_id")}
    t["stopped"] = bool(row["stopped"])
    if row["extra"]:
        try:
            t.update(json.loads(row["extra"]))
        except ValueError:
            log.exception("couldn't parse the extra data of a saved message")
    return t


def _extra(turn):
    extra = {k: turn[k] for k in ("attachments", "notes", "edits", "memory", "via", "masked") if turn.get(k)}
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


def _signature(con):
    r = con.execute("SELECT COUNT(*), MAX(updated) FROM conversations").fetchone()
    return (r[0], r[1])


def _detect_version(con):
    """0 = empty database, 1 = old layout, 2 = current. Raises for a newer one."""
    uv = con.execute("PRAGMA user_version").fetchone()[0]
    if uv > SCHEMA_VERSION:
        raise RuntimeError(f"chats.db is from a newer version of Hybrid AI (format {uv})")
    if uv == SCHEMA_VERSION:
        return 2
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return 1 if "conversations" in names else 0


def _backup(path, backup_dir):
    """Copy chats.db (including what is still in its WAL) into backup_dir and check
    the copy. Returns the backup's path. On any problem raises, leaving no partial file."""
    os.makedirs(backup_dir, exist_ok=True)
    size = os.path.getsize(path)
    if os.path.exists(path + "-wal"):
        size += os.path.getsize(path + "-wal")
    free = shutil.disk_usage(backup_dir).free
    need = 3 * size + 100 * 1024 * 1024
    if free < need:
        raise OSError(f"not enough free disk space: {free // 2**20} MB free, {need // 2**20} MB needed")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = os.path.join(backup_dir, f"chats-v1-{stamp}.db")
    n = 1
    while os.path.exists(final):
        n += 1
        final = os.path.join(backup_dir, f"chats-v1-{stamp}-{n}.db")
    partial = final + ".partial"
    src = dst = None
    try:
        src = sqlite3.connect(path, timeout=10)
        dst = sqlite3.connect(partial)
        src.backup(dst)
        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise OSError("the backup copy failed its integrity check")
        for table in ("conversations", "turns"):
            a = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            b = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if a != b:
                raise OSError(f"the backup copy has {b} rows in {table}, expected {a}")
        dst.close()
        dst = None
        src.close()
        src = None
        os.replace(partial, final)
        return final
    except BaseException:
        for con in (dst, src):
            if con is not None:
                try:
                    con.close()
                except sqlite3.Error:
                    log.exception("couldn't close a connection after a failed backup")
        try:
            if os.path.exists(partial):
                os.remove(partial)
        except OSError:
            log.exception("couldn't remove the unfinished backup %s", partial)
        raise


def _remove_stale_partials(backup_dir):
    if not os.path.isdir(backup_dir):
        return
    for name in os.listdir(backup_dir):
        if name.startswith("chats-v1-") and name.endswith(".partial"):
            try:
                os.remove(os.path.join(backup_dir, name))
            except OSError:
                log.exception("couldn't remove the stale backup file %s", name)


def _migrate_v1(path, expect):
    """Convert the old layout to version 2 in place, in ONE transaction. `expect` is
    the (count, newest update) of the data that was backed up. Returns a stats dict,
    or raises MigrationSkipped. Any other error rolls back and is re-raised."""
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    chat = None
    try:
        con.execute("PRAGMA foreign_keys = OFF")  # so DROP TABLE conversations doesn't cascade
        con.execute("BEGIN IMMEDIATE")
        try:
            if con.execute("PRAGMA user_version").fetchone()[0] != 0 or _detect_version(con) != 1:
                raise MigrationSkipped("another copy already upgraded the chats")
            if _signature(con) != tuple(expect):
                raise MigrationSkipped("chats changed while the backup was made")
            cols = {r[1] for r in con.execute("PRAGMA table_info(turns)")}
            if "extra" not in cols:  # databases made by the first version
                con.execute("ALTER TABLE turns ADD COLUMN extra TEXT")
            ccols = {r[1] for r in con.execute("PRAGMA table_info(conversations)")}
            if "meta" not in ccols:
                con.execute("ALTER TABLE conversations ADD COLUMN meta TEXT")
            for stmt in SCHEMA_V2:
                con.execute(stmt)
            con.execute("INSERT INTO chats (id, title, created, updated, meta) "
                        "SELECT id, title, created, updated, meta FROM conversations")
            n_old = con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            con.execute(
                "INSERT INTO chat_turns (chat_id, seq, role, text, route, meta, stopped, at, extra) "
                "SELECT t.conv_id, t.seq, t.role, t.text, t.route, t.meta, t.stopped, t.at, t.extra "
                "FROM turns t JOIN conversations c ON c.id = t.conv_id")
            n_turns = con.execute("SELECT COUNT(*) FROM chat_turns").fetchone()[0]
            ids = [r[0] for r in con.execute("SELECT id FROM conversations")]
            n_msgs = 0
            for k, chat in enumerate(ids):
                raw = con.execute("SELECT history FROM conversations WHERE id = ?", (chat,)).fetchone()[0]
                history = json.loads(raw)
                if not isinstance(history, list):
                    raise ValueError("history is not a list")
                con.executemany(
                    "INSERT INTO chat_messages (chat_id, seq, role, body) VALUES (?, ?, ?, ?)",
                    [(chat, i, m.get("role", ""), json.dumps(m, ensure_ascii=False))
                     for i, m in enumerate(_strip_images(history))])
                n_msgs += len(history)
                if _migration_hook:
                    _migration_hook("messages", k)
            chat = None
            con.execute("DROP TABLE turns")
            con.execute("DROP TABLE conversations")
            bad = con.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise sqlite3.IntegrityError(f"foreign key check failed ({len(bad)} problems)")
            con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            con.execute("COMMIT")
        except BaseException as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            if not isinstance(e, MigrationSkipped):
                log.exception("chat storage upgrade failed%s", f" (chat {chat})" if chat else "")
            raise
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            log.exception("couldn't checkpoint after the chat storage upgrade (harmless)")
        return {"chats": len(ids), "turns": n_turns, "messages": n_msgs, "orphans": n_old - n_turns}
    finally:
        con.close()


def _diff_rows(old, new):
    """old: {seq: row}, new: [row, ...] -> [(seq, row)] that must be written."""
    return [(i, r) for i, r in enumerate(new) if old.get(i) != r]


class Store:
    def __init__(self, path, upgrade=True, backup_dir=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.upgrade_note = ""
        self.backup_path = None
        self._lock = threading.Lock()
        # timeout: wait (instead of failing) if the other program is mid-write
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        try:
            self._db.row_factory = sqlite3.Row
            self.version = _detect_version(self._db)  # raises for a newer format; writes nothing
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")  # lets another process read while we write
            self._db.execute("PRAGMA synchronous = NORMAL")
            if self.version == 1 and upgrade:
                self._upgrade(backup_dir or os.path.join(os.path.dirname(path), "backups"))
            if self.version == 0:
                self._create_v2()
            if self.version == 1:
                self._v1_open()
        except BaseException:
            self._db.close()
            raise

    # ---------- opening / upgrading ----------
    def _create_v2(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for stmt in SCHEMA_V2:
                    self._db.execute(stmt)
                self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise
        self.version = 2

    def _upgrade(self, backup_dir):
        """Back up, then migrate. Any failure leaves the old data as it was (version 1)."""
        t0 = time.time()
        try:
            _remove_stale_partials(backup_dir)
            log.info("chat storage upgrade: backing up %s", self.path)
            backup = _backup(self.path, backup_dir)
        except Exception as e:
            log.exception("chat storage upgrade postponed: backup failed")
            self.upgrade_note = (f"Chat storage upgrade postponed: couldn't make a backup ({e}). "
                                 "Your chats work as before.")
            return
        self.backup_path = backup
        try:
            bcon = sqlite3.connect(backup)
            try:
                expect = _signature(bcon)
            finally:
                bcon.close()
            log.info("chat storage upgrade: migrating (backup %s)", backup)
            stats = _migrate_v1(self.path, expect)
        except MigrationSkipped as e:
            log.warning("chat storage upgrade skipped: %s", e)
            self.upgrade_note = f"Chat storage upgrade postponed: {e}. It will be tried again at the next start."
        except Exception as e:  # already logged with the chat id by _migrate_v1
            self.upgrade_note = (f"Chat storage upgrade failed ({e}); nothing was changed. "
                                 f"Your chats work as before. Backup: {backup}")
        else:
            self.upgrade_note = (f"Chat storage upgraded: {stats['chats']} chats, {stats['messages']} messages "
                                 f"in {time.time() - t0:.1f} s. Backup of the old data: {backup}")
            if stats["orphans"]:
                self.upgrade_note += f" ({stats['orphans']} orphan turns dropped)"
            log.info(self.upgrade_note)
        self.version = _detect_version(self._db)

    def _v1_open(self):
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(turns)")}
        if "extra" not in cols:  # databases made by the first version
            self._db.execute("ALTER TABLE turns ADD COLUMN extra TEXT")
        ccols = {r[1] for r in self._db.execute("PRAGMA table_info(conversations)")}
        if "meta" not in ccols:
            self._db.execute("ALTER TABLE conversations ADD COLUMN meta TEXT")
        self._db.commit()

    # ---------- public API ----------
    def load_all(self):
        """Every saved conversation, most recently used first."""
        if self.version == 1:
            return self._v1_load_all()
        with self._lock:
            chats = self._db.execute(
                "SELECT id, title, created, updated, meta FROM chats ORDER BY updated DESC").fetchall()
            turns, msgs = {}, {}
            for r in self._db.execute(f"SELECT chat_id, {TURN_COLS} FROM chat_turns ORDER BY chat_id, seq"):
                turns.setdefault(r["chat_id"], []).append(_turn_from_row(r))
            for r in self._db.execute("SELECT chat_id, body FROM chat_messages ORDER BY chat_id, seq"):
                msgs.setdefault(r["chat_id"], []).append(json.loads(r["body"]))
            return [{"id": c["id"], "title": c["title"], "created": c["created"],
                     "updated": c["updated"], "history": msgs.get(c["id"], []),
                     "meta": json.loads(c["meta"]) if c["meta"] else {},
                     "turns": turns.get(c["id"], [])} for c in chats]

    def get(self, conv_id):
        """One conversation, or None."""
        if self.version == 1:
            return self._v1_get(conv_id)
        with self._lock:
            c = self._db.execute(
                "SELECT id, title, created, updated, meta FROM chats WHERE id = ?", (conv_id,)).fetchone()
            if c is None:
                return None
            turns = self._db.execute(
                f"SELECT {TURN_COLS} FROM chat_turns WHERE chat_id = ? ORDER BY seq", (conv_id,)).fetchall()
            msgs = self._db.execute(
                "SELECT body FROM chat_messages WHERE chat_id = ? ORDER BY seq", (conv_id,)).fetchall()
            return {"id": c["id"], "title": c["title"], "created": c["created"],
                    "updated": c["updated"], "history": [json.loads(m["body"]) for m in msgs],
                    "meta": json.loads(c["meta"]) if c["meta"] else {},
                    "turns": [_turn_from_row(t) for t in turns]}

    def list(self):
        """Titles only, most recently used first (for the phone's chat list)."""
        if self.version == 1:
            sql = ("SELECT c.id, c.title, c.updated, "
                   "(SELECT text FROM turns t WHERE t.conv_id = c.id ORDER BY seq DESC LIMIT 1) AS last "
                   "FROM conversations c ORDER BY c.updated DESC")
        else:
            sql = ("SELECT c.id, c.title, c.updated, "
                   "(SELECT text FROM chat_turns t WHERE t.chat_id = c.id ORDER BY seq DESC LIMIT 1) AS last "
                   "FROM chats c ORDER BY c.updated DESC")
        with self._lock:
            return [dict(r) for r in self._db.execute(sql).fetchall()]

    def last_change(self):
        """(newest update time, number of chats) — cheap way to notice that
        another program (the phone server) changed something."""
        table = "conversations" if self.version == 1 else "chats"
        with self._lock:
            r = self._db.execute(f"SELECT MAX(updated), COUNT(*) FROM {table}").fetchone()
            return (r[0] or 0.0, r[1])

    def save(self, conv_id, title, history, turns, created=None, meta=None):
        """Write one conversation (insert or update). Only rows that changed are
        written; afterwards the database matches the given lists exactly."""
        if self.version == 1:
            return self._v1_save(conv_id, title, history, turns, created, meta)
        now = time.time()
        bodies = [(m.get("role", ""), json.dumps(m, ensure_ascii=False)) for m in _strip_images(history)]
        trows = [(t["role"], t["text"], t.get("route"), t.get("meta"), int(bool(t.get("stopped"))),
                  t.get("at"), _extra(t)) for t in turns]
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")  # the reads below must be inside the write lock
            try:
                self._db.execute(
                    "INSERT INTO chats (id, title, created, updated, meta) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
                    "updated = excluded.updated, meta = excluded.meta",
                    (conv_id, title, created or now, now,
                     json.dumps(meta, ensure_ascii=False) if meta else None))
                old_t = {r[0]: tuple(r[1:]) for r in self._db.execute(
                    f"SELECT seq, {TURN_COLS} FROM chat_turns WHERE chat_id = ?", (conv_id,))}
                self._db.executemany(
                    "INSERT INTO chat_turns (chat_id, seq, role, text, route, meta, stopped, at, extra) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id, seq) DO UPDATE SET "
                    "role = excluded.role, text = excluded.text, route = excluded.route, "
                    "meta = excluded.meta, stopped = excluded.stopped, at = excluded.at, "
                    "extra = excluded.extra",
                    [(conv_id, i, *r) for i, r in _diff_rows(old_t, trows)])
                if any(s >= len(trows) for s in old_t):
                    self._db.execute("DELETE FROM chat_turns WHERE chat_id = ? AND seq >= ?",
                                     (conv_id, len(trows)))
                old_m = {r[0]: tuple(r[1:]) for r in self._db.execute(
                    "SELECT seq, role, body FROM chat_messages WHERE chat_id = ?", (conv_id,))}
                self._db.executemany(
                    "INSERT INTO chat_messages (chat_id, seq, role, body) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(chat_id, seq) DO UPDATE SET role = excluded.role, body = excluded.body",
                    [(conv_id, i, *r) for i, r in _diff_rows(old_m, bodies)])
                if any(s >= len(bodies) for s in old_m):
                    self._db.execute("DELETE FROM chat_messages WHERE chat_id = ? AND seq >= ?",
                                     (conv_id, len(bodies)))
                self._db.commit()
            except BaseException:
                self._db.rollback()
                raise

    def delete(self, conv_id):
        with self._lock, self._db:
            if self.version == 1:
                self._db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            else:  # the foreign keys remove its turns and messages
                self._db.execute("DELETE FROM chats WHERE id = ?", (conv_id,))

    def close(self):
        with self._lock:
            self._db.close()

    # ---------- version 1 (fallback when the upgrade could not run) ----------
    def _v1_load_all(self):
        with self._lock:
            convs = self._db.execute(
                "SELECT id, title, history, created, updated, meta FROM conversations "
                "ORDER BY updated DESC").fetchall()
            return [self._v1_build(c) for c in convs]

    def _v1_build(self, c):
        turns = self._db.execute(
            "SELECT role, text, route, meta, stopped, at, extra FROM turns "
            "WHERE conv_id = ? ORDER BY seq", (c["id"],)).fetchall()
        return {
            "id": c["id"], "title": c["title"], "created": c["created"],
            "updated": c["updated"], "history": json.loads(c["history"]),
            "meta": json.loads(c["meta"]) if c["meta"] else {},
            "turns": [_turn_from_row(t) for t in turns],
        }

    def _v1_get(self, conv_id):
        with self._lock:
            c = self._db.execute(
                "SELECT id, title, history, created, updated, meta FROM conversations WHERE id = ?",
                (conv_id,)).fetchone()
            return self._v1_build(c) if c else None

    def _v1_save(self, conv_id, title, history, turns, created, meta):
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
