"""Tests for memory.py (Phase 3) with a fake model and fake embeddings."""
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import memory as M  # noqa: E402

VOCAB = ["venv", "pip", "python", "windows", "graph", "bfs", "queue", "resume", "internship",
         "pdf", "memory", "name", "naman", "router", "embedding", "cricket", "pizza", "exam", "dsa"]


def fake_embed(texts, kind="document"):
    """Bag-of-words over a small vocabulary: similar topics -> similar vectors."""
    out = []
    for t in texts:
        words = re.findall(r"[a-z]+", t.lower())
        v = [float(sum(w.startswith(k) for w in words)) for k in VOCAB] + [0.05]
        out.append(M.normalise(v))
    return out


fake_embed.model = "fake"


def summary(title, bullets, facts=(), tags=()):
    return {"title": title, "bullets": list(bullets), "facts": list(facts), "project_facts": [],
            "open_items": [], "files": [], "tags": list(tags)}


def test_parse_summary_tolerant():
    reply = 'Sure! Here it is:\n{"title": "Fixing pip", "bullets": ["- pip.exe broke after moving venv",' \
            ' "used python -m pip"], "facts": "User is on Windows", "tags": ["Pip", "venv"],}\nDone'
    s = M.parse_summary(reply)
    assert s["title"] == "Fixing pip"
    assert s["bullets"] == ["pip.exe broke after moving venv", "used python -m pip"]
    assert s["facts"] == ["User is on Windows"] and s["tags"] == ["pip", "venv"]
    assert s["open_items"] == [] and s["files"] == []
    assert M.parse_summary("no json here") is None
    assert M.parse_summary('{"title": "x", "bullets": []}') is None  # nothing to remember


def test_store_search_and_threshold(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    a = mem.add_summary("c1", summary("Fixing pip in a moved venv",
                                      ["pip.exe failed after the venv moved", "python -m pip works on windows"]),
                        fake_embed, started=1000, embed_model="fake")
    mem.add_summary("c2", summary("BFS with a queue", ["wrote bfs for a graph", "used a deque queue"]),
                    fake_embed, embed_model="fake")
    mem.add_summary("c3", summary("Internship resume", ["tailored the resume for an internship"]),
                    fake_embed, embed_model="fake")
    hits = mem.search("pip install fails in my venv on windows", fake_embed, k=3, threshold=0.5)
    assert hits and hits[0][1]["id"] == a and len(hits) == 1
    assert mem.search("best pizza toppings", fake_embed, threshold=0.5) == []
    assert mem.get_summary(a)["started"] == 1000


def test_facts_dedupe_pin_and_delete(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    sid = mem.add_summary("c1", summary("Intro", ["said hello"], facts=["User's name is Naman", "Studies DSA"]),
                          fake_embed, embed_model="fake")
    mem.add_summary("c2", summary("Again", ["more"], facts=["user's name is Naman."]), fake_embed,
                    embed_model="fake")
    texts = [f["text"] for f in mem.facts()]
    assert texts.count("User's name is Naman") == 1 and len(texts) == 2  # duplicate ignored
    pinned = mem.add_fact("Prefers short answers", pinned=True)
    assert mem.user_facts()[0] == "Prefers short answers"
    # deleting a summary removes its unpinned facts, but not pinned or hand-added ones
    mem.delete_summary(sid)
    left = {f["text"] for f in mem.facts()}
    assert left == {"Prefers short answers"}
    mem.delete_fact(pinned)
    assert mem.facts() == []


def test_embed_missing(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))

    def broken(texts, kind="document"):
        raise ConnectionError("ollama offline")

    sid = mem.add_summary("c1", summary("BFS", ["bfs graph queue"]), broken)
    assert mem.get_summary(sid)["vector"] is None  # saved anyway
    assert mem.search("bfs", fake_embed) == []
    assert mem.embed_missing(fake_embed, "fake") == 1
    assert mem.search("bfs on a graph", fake_embed)[0][1]["id"] == sid
    assert mem.embed_missing(fake_embed, "fake") == 0


def test_update_summary_reembeds(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    sid = mem.add_summary("c1", summary("Old", ["cricket"]), fake_embed, embed_model="fake")
    mem.update_summary(sid, "Exam plan", {"bullets": ["dsa exam plan"], "open_items": [], "tags": [],
                                          "facts": [], "files": []}, fake_embed, "fake")
    assert mem.search("dsa exam", fake_embed)[0][1]["title"] == "Exam plan"


def test_transcript_and_worth():
    turns = [{"role": "user", "text": "fix my bfs", "attachments": [{"name": "algo.py"}]},
             {"role": "assistant", "text": "Here:\n```edit:algo.py\nx=1\n```",
              "edits": [{"name": "algo.py", "status": "applied"}]},
             {"role": "note", "text": "Continuing from X"}]
    t = M.transcript(turns)
    assert "[attached: algo.py]" in t and "[proposed a new version of a file]" in t
    assert "x=1" not in t and "[edit to algo.py: applied]" in t and "NOTE: Continuing" in t
    assert M.worth_summarising(turns)
    assert not M.worth_summarising([{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello!"}])
    long = [{"role": "user", "text": "a" * 30000}]
    assert "middle of the session left out" in M.transcript(long, max_chars=9000)


def test_summarize_and_context_block():
    def complete(prompt):
        assert "CHAT:" in prompt and "USER: what's a venv" in prompt
        return json.dumps({"title": "Venv basics", "bullets": ["explained venvs"], "facts": ["User uses Windows"],
                           "project_facts": [], "open_items": ["try python -m pip"], "files": [], "tags": ["venv"]})
    s = M.summarize([{"role": "user", "text": "what's a venv and why use one?"}], complete)
    assert s["title"] == "Venv basics"
    block = M.context_block([(0.8, {**s, "id": "x", "created": 0, "started": 0})], ["User's name is Naman"])
    assert "User's name is Naman" in block and "Venv basics" in block and "Left open: try python -m pip" in block
    assert M.context_block([], []) is None


def test_recall_questions():
    for q in ("do u remember our past session?", "what's my name?", "what did we do last time",
              "you told me something earlier", "what do you know about me"):
        assert M.is_recall_question(q), q
    for q in ("write a bfs in python", "what is the capital of Australia?"):
        assert not M.is_recall_question(q), q


def test_context_block_says_it_has_memory():
    block = M.context_block([], ["User's name is Naman"])
    assert "do NOT" in block and "can't remember" in block and "User's name is Naman" in block


def test_settings_upgrade_threshold(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("HYBRID_AI_DATA_DIR", str(tmp_path))
    import config
    importlib.reload(config)
    import settings
    importlib.reload(settings)
    (tmp_path / "settings.json").write_text(json.dumps({"memory": {"threshold": 0.55}, "access": {"folders": []}}))
    s = settings.Settings()
    assert s.get("memory")["threshold"] == 0.5 and s.get("settings_version") == 2
    s.set("memory", {**s.get("memory"), "threshold": 0.62})
    assert settings.Settings().get("memory")["threshold"] == 0.62  # user's own value kept


def test_facts_need_confirmation(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    sid = mem.add_summary("c1", summary("Intro", ["hello"], facts=["User's name is Naman", "User lives on Mars"]),
                          fake_embed, embed_model="fake")
    assert mem.user_facts() == []  # nothing used before you confirm
    pend = {f["text"]: f["id"] for f in mem.pending_facts(source=sid)}
    assert set(pend) == {"User's name is Naman", "User lives on Mars"}
    mem.confirm_fact(pend["User's name is Naman"])
    mem.delete_fact(pend["User lives on Mars"])
    assert mem.user_facts() == ["User's name is Naman"] and mem.pending_facts() == []
    # adding by hand a fact that's pending confirms it
    sid2 = mem.add_summary("c2", summary("More", ["x"], facts=["Studies data science"]), fake_embed)
    mem.add_fact("studies data science", pinned=True)
    assert "Studies data science" in mem.user_facts() and mem.pending_facts() == []


def test_old_facts_table_upgraded(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("""CREATE TABLE facts (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'user',
        pinned INTEGER NOT NULL DEFAULT 0, source TEXT, created REAL NOT NULL);
        INSERT INTO facts VALUES ('f1', 'User is on Windows', 'user', 0, NULL, 1);""")
    con.commit(); con.close()
    mem = M.MemoryStore(str(p))
    assert mem.user_facts() == ["User is on Windows"]  # existing facts stay in use
