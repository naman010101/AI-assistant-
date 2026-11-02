"""Memory: short summaries of past sessions, searched for context as you write.

    mem = MemoryStore(config.MEMORY_DB)
    summary = summarize(turns)                       # local model -> dict
    mem.add_summary(conv_id, summary, embed_fn)      # store + embedding
    hits = mem.search("how did I set up the venv?", embed_fn)
    system_text = context_block(hits, mem.user_facts())

How it works
  1. When a session ends, the local model writes a summary as JSON:
     title, bullet points, facts about the user, open items, files, tags.
  2. The summary is turned into an embedding (nomic-embed-text, with the
     "search_document:" prefix that model expects for stored text).
  3. Each new message is embedded with "search_query:" and compared to every
     summary by cosine similarity. The closest few above a threshold are
     added to the prompt. Facts about the user are always added (short list).

Everything here stays on the laptop: summaries are written by the local
model, stored in memory.db in the private data folder, and only sent to a
cloud model if you switch that on in Settings.
"""
import json
import math
import re
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries (
    id        TEXT PRIMARY KEY,
    conv_id   TEXT,
    title     TEXT NOT NULL,
    data      TEXT NOT NULL,      -- JSON: bullets, facts, open_items, files, tags
    started   REAL,               -- when the session began
    created   REAL NOT NULL,      -- when it was summarised
    vector    TEXT,               -- JSON list of floats (NULL until embedded)
    model     TEXT                -- embedding model the vector came from
);
CREATE TABLE IF NOT EXISTS facts (
    id        TEXT PRIMARY KEY,
    text      TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'user',   -- user | project
    pinned    INTEGER NOT NULL DEFAULT 0,     -- always included
    source    TEXT,                           -- summary id it came from (NULL = added by hand)
    created   REAL NOT NULL,
    status    TEXT NOT NULL DEFAULT 'active'  -- pending (waiting for your OK) | active
);
"""

USER_FACTS_ALWAYS = 15      # newest facts about the user always included
RECENT_FOR_RECALL = 3       # "do you remember…?" questions get the latest sessions

# Questions about the past itself ("do you remember…", "what did we do last
# time") have no topic of their own, so similarity search can't find anything.
# For these, the most recent sessions are added.
RECALL = re.compile(
    r"\b(remember|recall|memory|memories|forgot|forget|last (time|session|chat|conversation)|"
    r"previous(ly)?|earlier|before|yesterday|past (session|chat|conversation)s?|"
    r"we (talked|discussed|did|worked|spoke|were)|you (said|told|suggested)|i (told|said|mentioned)|"
    r"what do you know about me|who am i|my name|about me)\b", re.I)


def is_recall_question(text):
    return bool(RECALL.search(text or ""))
MIN_CHARS_TO_SUMMARISE = 30  # a chat that's only "hi" isn't worth remembering


# ================================================================ store
class MemoryStore:
    def __init__(self, path):
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(facts)")}
        if "status" not in cols:  # facts saved by the first version count as confirmed
            self._db.execute("ALTER TABLE facts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        self._db.commit()

    # ---------- summaries ----------
    def add_summary(self, conv_id, summary, embed_fn=None, started=None, embed_model=None):
        sid = uuid.uuid4().hex
        vec = None
        if embed_fn:
            try:
                vec = embed_fn([summary_text(summary)], kind="document")[0]
            except Exception:
                vec = None  # embedded later by embed_missing()
        data = {k: summary.get(k, []) for k in ("bullets", "facts", "open_items", "files", "tags")}
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO summaries (id, conv_id, title, data, started, created, vector, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sid, conv_id, summary.get("title") or "Untitled session",
                 json.dumps(data, ensure_ascii=False), started, time.time(),
                 json.dumps(vec) if vec else None, embed_model if vec else None))
            # Facts the summariser pulled out wait for your OK: a wrong "fact"
            # would otherwise be sent with every message from now on.
            for fact in summary.get("facts", []):
                self._add_fact_locked(fact, "user", source=sid, status="pending")
            for fact in summary.get("project_facts", []):
                self._add_fact_locked(fact, "project", source=sid, status="pending")
        return sid

    def summaries(self, limit=None):
        q = "SELECT * FROM summaries ORDER BY created DESC" + (f" LIMIT {int(limit)}" if limit else "")
        with self._lock:
            return [self._row(r) for r in self._db.execute(q).fetchall()]

    def get_summary(self, sid):
        with self._lock:
            r = self._db.execute("SELECT * FROM summaries WHERE id = ?", (sid,)).fetchone()
        return self._row(r) if r else None

    def update_summary(self, sid, title, data, embed_fn=None, embed_model=None):
        vec = None
        if embed_fn:
            try:
                vec = embed_fn([summary_text({"title": title, **data})], kind="document")[0]
            except Exception:
                vec = None
        with self._lock, self._db:
            self._db.execute("UPDATE summaries SET title = ?, data = ?, vector = ?, model = ? WHERE id = ?",
                             (title, json.dumps(data, ensure_ascii=False),
                              json.dumps(vec) if vec else None, embed_model if vec else None, sid))

    def delete_summary(self, sid, with_facts=True):
        with self._lock, self._db:
            self._db.execute("DELETE FROM summaries WHERE id = ?", (sid,))
            if with_facts:
                self._db.execute("DELETE FROM facts WHERE source = ? AND pinned = 0", (sid,))

    def embed_missing(self, embed_fn, embed_model=None):
        """Embed summaries saved while Ollama's embedding model was unavailable."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM summaries WHERE vector IS NULL OR model IS NOT ?",
                                    (embed_model,)).fetchall()
        done = 0
        for r in rows:
            s = self._row(r)
            vec = embed_fn([summary_text(s)], kind="document")[0]
            with self._lock, self._db:
                self._db.execute("UPDATE summaries SET vector = ?, model = ? WHERE id = ?",
                                 (json.dumps(vec), embed_model, s["id"]))
            done += 1
        return done

    @staticmethod
    def _row(r):
        d = json.loads(r["data"])
        return {"id": r["id"], "conv_id": r["conv_id"], "title": r["title"], "started": r["started"],
                "created": r["created"], "vector": json.loads(r["vector"]) if r["vector"] else None,
                **{k: d.get(k, []) for k in ("bullets", "facts", "open_items", "files", "tags")}}

    # ---------- facts ----------
    def _add_fact_locked(self, text, kind="user", source=None, pinned=False, status="active"):
        text = re.sub(r"\s+", " ", str(text)).strip().rstrip(".")
        if not text or len(text) > 300:
            return None
        key = text.lower()
        for r in self._db.execute("SELECT id, text, status FROM facts WHERE kind = ?", (kind,)):
            if r["text"].lower() == key:
                if status == "active" and r["status"] != "active":  # added by hand = confirmed
                    self._db.execute("UPDATE facts SET status = 'active', pinned = ? WHERE id = ?",
                                     (int(pinned), r["id"]))
                return r["id"]  # already known
        fid = uuid.uuid4().hex
        self._db.execute("INSERT INTO facts (id, text, kind, pinned, source, created, status) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (fid, text, kind, int(pinned), source, time.time(), status))
        return fid

    def add_fact(self, text, kind="user", pinned=True):
        with self._lock, self._db:
            return self._add_fact_locked(text, kind, None, pinned)

    def facts(self, status=None):
        q = "SELECT * FROM facts" + (" WHERE status = ?" if status else "") + \
            " ORDER BY pinned DESC, created DESC"
        with self._lock:
            return [dict(r) | {"pinned": bool(r["pinned"])} for r in
                    self._db.execute(q, (status,) if status else ()).fetchall()]

    def pending_facts(self, source=None):
        return [f for f in self.facts("pending") if source is None or f["source"] == source]

    def confirm_fact(self, fid):
        with self._lock, self._db:
            self._db.execute("UPDATE facts SET status = 'active' WHERE id = ?", (fid,))

    def set_pinned(self, fid, pinned):
        with self._lock, self._db:
            self._db.execute("UPDATE facts SET pinned = ? WHERE id = ?", (int(pinned), fid))

    def update_fact(self, fid, text):
        with self._lock, self._db:
            self._db.execute("UPDATE facts SET text = ? WHERE id = ?", (text.strip(), fid))

    def delete_fact(self, fid):
        with self._lock, self._db:
            self._db.execute("DELETE FROM facts WHERE id = ?", (fid,))

    def user_facts(self):
        """Facts always sent: every pinned fact + the newest facts about the user."""
        with self._lock:
            pinned = self._db.execute("SELECT text FROM facts WHERE pinned = 1 AND status = 'active' "
                                      "ORDER BY created").fetchall()
            recent = self._db.execute("SELECT text FROM facts WHERE pinned = 0 AND kind = 'user' "
                                      "AND status = 'active' ORDER BY created DESC LIMIT ?",
                                      (USER_FACTS_ALWAYS,)).fetchall()
        return [r["text"] for r in pinned] + [r["text"] for r in recent]

    # ---------- search ----------
    def recent(self, n=RECENT_FOR_RECALL):
        return self.summaries(limit=n)

    def search(self, query, embed_fn, k=3, threshold=0.55):
        """Closest summaries to `query`: list of (similarity, summary).
        threshold=None returns the top k whatever their similarity."""
        rows = [s for s in self.summaries() if s["vector"]]
        if not rows or not query.strip():
            return []
        q = embed_fn([query], kind="query")[0]
        scored = sorted(((dot(q, s["vector"]), s) for s in rows), key=lambda x: -x[0])
        return [(sim, s) for sim, s in scored[:k] if threshold is None or sim >= threshold]

    def close(self):
        with self._lock:
            self._db.close()


# ================================================================ text helpers
def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def normalise(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def summary_text(s):
    """The text that represents a summary in search."""
    parts = [s.get("title", "")]
    parts += s.get("bullets", []) + s.get("facts", []) + s.get("open_items", [])
    if s.get("files"):
        parts.append("Files: " + ", ".join(s["files"]))
    if s.get("tags"):
        parts.append("Tags: " + ", ".join(s["tags"]))
    return "\n".join(p for p in parts if p)


def worth_summarising(turns):
    user_text = " ".join(t.get("text", "") for t in turns if t.get("role") == "user")
    has_files = any(t.get("attachments") for t in turns)
    return has_files or len(user_text.strip()) >= MIN_CHARS_TO_SUMMARISE


def transcript(turns, max_chars=16000):
    """The session as plain text for the summariser. Long sessions keep the
    start and the end (where decisions usually are)."""
    lines = []
    for t in turns:
        role = t.get("role")
        if role == "user":
            files = ", ".join(a.get("name", "") for a in t.get("attachments") or [])
            lines.append("USER: " + (t.get("text") or "") + (f"  [attached: {files}]" if files else ""))
        elif role == "assistant":
            text = re.sub(r"```edit:[^\n]*\n.*?```", "[proposed a new version of a file]",
                          t.get("text") or "", flags=re.S)
            lines.append("ASSISTANT: " + text)
            for e in t.get("edits") or []:
                lines.append(f"[edit to {e['name']}: {e['status']}]")
        elif role == "note":
            lines.append("NOTE: " + (t.get("text") or ""))
    text = "\n\n".join(lines)
    if len(text) > max_chars:
        head, tail = text[: max_chars // 3], text[-(2 * max_chars) // 3:]
        text = head + "\n\n[… middle of the session left out …]\n\n" + tail
    return text


SUMMARY_PROMPT = """Summarise this chat between a user and their AI assistant so the assistant can remember it in future sessions.

Reply with ONLY a JSON object with these keys:
  "title":         a short title, at most 8 words
  "bullets":       3 to 8 short bullet points: what was discussed, decided or done
  "facts":         lasting facts about the USER (name, studies, skills, preferences, setup); [] if none
  "project_facts": lasting facts about the user's projects or code; [] if none
  "open_items":    things left unfinished or to do next; [] if none
  "files":         names of files that were discussed; [] if none
  "tags":          2 to 5 lowercase topic words

Rules: write facts as plain statements ("User's name is Naman"). Only include facts the user actually said or that clearly happened. Do not invent anything.

CHAT:
"""


def parse_summary(text):
    """Get the JSON object out of a model reply, tolerating extra text and
    small format slips. Returns a dict with all keys, or None."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    raw = m.group(0)
    for candidate in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):  # drop trailing commas
        try:
            data = json.loads(candidate)
            break
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return None

    def strings(v):
        if isinstance(v, str):
            v = [v]
        return [re.sub(r"^[-*•]\s*", "", str(x)).strip() for x in (v or []) if str(x).strip()]

    out = {"title": str(data.get("title") or "").strip()[:80] or "Untitled session"}
    for key in ("bullets", "facts", "project_facts", "open_items", "files", "tags"):
        out[key] = strings(data.get(key))[:12]
    out["tags"] = [t.lower() for t in out["tags"]]
    if not out["bullets"]:
        return None
    return out


def summarize(turns, complete_fn):
    """complete_fn(prompt) -> model reply text. Returns a summary dict.
    Raises RuntimeError if the model's reply can't be used."""
    reply = complete_fn(SUMMARY_PROMPT + transcript(turns))
    summary = parse_summary(reply)
    if summary is None:
        raise RuntimeError("the model's summary wasn't valid JSON")
    return summary


def context_block(hits, facts):
    """The system text that carries memory into a new message."""
    if not hits and not facts:
        return None
    parts = ["You have a memory of earlier sessions with this user, kept by the app on their laptop. "
             "The notes below come from it. They are true and about this user. If the user asks what "
             "you remember, who they are, or about earlier sessions, answer from these notes: do NOT "
             "say that you can't remember or that each conversation starts fresh. Otherwise use the "
             "notes only where they help, and never invent details beyond them."]
    if facts:
        parts.append("What you know about the user:\n" + "\n".join(f"- {f}" for f in facts))
    for sim, s in hits:
        when = time.strftime("%d %b %Y", time.localtime(s.get("started") or s["created"]))
        body = "\n".join(f"- {b}" for b in s.get("bullets", []))
        if s.get("open_items"):
            body += "\nLeft open: " + "; ".join(s["open_items"])
        parts.append(f"Earlier session “{s['title']}” ({when}):\n{body}")
    return "\n\n".join(parts)


# ================================================================ Ollama calls
def ollama_embedder(model=None):
    """embed(texts, kind='document'|'query') using Ollama, with nomic's task
    prefixes. Returns normalised vectors."""
    import classifier
    model = model or classifier.EMBED_MODEL

    def embed(texts, kind="document"):
        prefix = ("search_query: " if kind == "query" else "search_document: ") if "nomic" in model else ""
        data = classifier._post("/api/embed", {"model": model, "input": [prefix + t for t in texts],
                                               "keep_alive": "30m"}, 60)
        return [normalise(v) for v in data["embeddings"]]

    embed.model = model
    return embed


def ollama_completer(model_id="llama3.1:8b", json_mode=True):
    """complete(prompt) -> text from a local model (JSON output by default)."""
    import classifier

    def complete(prompt):
        data = classifier._post("/api/chat", {
            "model": model_id, "stream": False, **({"format": "json"} if json_mode else {}),
            "options": {"temperature": 0.2},
            "messages": [{"role": "user", "content": prompt}],
        }, 300)
        return data["message"]["content"]

    return complete
