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


# ================================================================ step 1.6: vector index
import glob  # noqa: E402
import random  # noqa: E402
import sqlite3  # noqa: E402

import pytest  # noqa: E402

import vectors  # noqa: E402

OLD_SCHEMA = """
CREATE TABLE summaries (id TEXT PRIMARY KEY, conv_id TEXT, title TEXT NOT NULL, data TEXT NOT NULL,
    started REAL, created REAL NOT NULL, vector TEXT, model TEXT);
CREATE TABLE facts (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'user',
    pinned INTEGER NOT NULL DEFAULT 0, source TEXT, created REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active');
"""


def make_old_db(path, n=40, dim=16):
    """A memory.db as it was before 1.6 (no index tables). Every 5th summary has no vector."""
    rng = random.Random(5)
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    for i in range(n):
        vec = M.normalise([rng.gauss(0, 1) for _ in range(dim)])
        con.execute("INSERT INTO summaries VALUES (?, 'c', ?, ?, NULL, ?, ?, 'fake')",
                    (f"s{i}", f"Title {i}", json.dumps({"bullets": [f"b{i}"]}), 1000.0 + i,
                     json.dumps(vec) if i % 5 else None))
    con.execute("INSERT INTO facts VALUES ('f1', 'User likes tea', 'user', 0, NULL, 1, 'active')")
    con.commit()
    con.close()


def raw_vectors(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT id, vector FROM summaries ORDER BY id").fetchall()
    finally:
        con.close()


def tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        con.close()


def backups(tmp_path):
    return sorted(glob.glob(str(tmp_path / "backups" / "memory-before-1.6-*.db")))


def fake_embed16(texts, kind="document"):
    return [M.normalise([1.0] + [0.0] * 15) for _ in texts]


@pytest.fixture(params=vectors.available())
def backend(request, monkeypatch):
    monkeypatch.setenv(vectors.ENV_VAR, request.param)
    return request.param


def old_formula(mem, q, k, threshold):
    rows = [s for s in mem.summaries() if s["vector"]]
    scored = sorted(((M.dot(q, s["vector"]), s) for s in rows), key=lambda x: -x[0])
    return [(sim, s) for sim, s in scored[:k] if threshold is None or sim >= threshold]


def test_search_matches_old_formula_on_each_backend(tmp_path, backend):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    assert mem.backend == backend
    rng = random.Random(3)
    words = VOCAB + ["alpha", "beta", "gamma", "delta", "sigma", "omega"]
    for i in range(300):
        text = " ".join(rng.choice(words) for _ in range(6))
        mem.add_summary(f"c{i}", summary(f"S{i}", [text]), fake_embed, embed_model="fake")
    for query in ("pip venv windows", "graph bfs queue", "pizza cricket", "alpha beta omega exam"):
        q = fake_embed([query], kind="query")[0]
        for threshold in (None, 0.3):
            got = mem.search(query, fake_embed, k=5, threshold=threshold)
            want = old_formula(mem, q, 5, threshold)
            # float32 can reorder scores that tie to ~1e-7, so compare scores, and ids only where not tied
            assert len(got) == len(want)
            exact = {s["id"]: sim for sim, s in old_formula(mem, q, 10 ** 6, None)}
            assert all(abs(sim - exact[s["id"]]) < 1e-5 for sim, s in got)
            assert all(abs(a - b) < 1e-5 for (a, _), (b, _) in zip(got, want))
            assert [a for a, _ in got] == sorted((a for a, _ in got), reverse=True)
            cut = want[-1][0] if want else 0
            assert {s["id"] for sim, s in old_formula(mem, q, 10 ** 6, None) if sim > cut + 1e-5} \
                <= {s["id"] for _, s in got}


def test_index_follows_delete_update_and_embed_missing(tmp_path, backend):
    mem = M.MemoryStore(str(tmp_path / "m.db"))

    def broken(texts, kind="document"):
        raise ConnectionError("ollama offline")

    a = mem.add_summary("c1", summary("Pip", ["pip venv"]), fake_embed, embed_model="fake")
    b = mem.add_summary("c2", summary("BFS", ["bfs graph"]), fake_embed, embed_model="fake")
    c = mem.add_summary("c3", summary("Offline", ["cricket"]), broken)
    assert mem.vec.keys() == {a, b} and mem.counts() == (3, 2)
    mem.delete_summary(a)
    assert mem.vec.keys() == {b}
    # an update whose embedding fails drops the vector, so the summary can't match stale text
    mem.update_summary(b, "BFS", {"bullets": ["bfs"], "open_items": [], "tags": [], "facts": [], "files": []},
                       broken, "fake")
    assert mem.vec.keys() == set() and mem.counts() == (2, 0)
    assert mem.search("bfs graph", fake_embed) == []
    assert mem.embed_missing(fake_embed, "fake") == 2
    assert mem.vec.keys() == {b, c} and mem.counts() == (2, 2)
    assert mem.search("bfs graph", fake_embed)[0][1]["id"] == b


def test_embedding_length_change_rebuilds_index(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    a = mem.add_summary("c1", summary("A", ["pip"]), fake_embed, embed_model="fake")
    b = mem.add_summary("c2", summary("B", ["bfs"]), fake_embed, embed_model="fake")

    def wide(texts, kind="document"):
        return [[1.0] * 5 for _ in texts]

    mem.update_summary(b, "B", {"bullets": ["bfs"], "open_items": [], "tags": [], "facts": [], "files": []},
                       wide, "wide")
    assert mem.vec.dim == 5 and mem.vec.keys() == {b}      # a's old-length vector is left out
    assert mem.get_summary(a)["vector"] is not None         # but still stored (source of truth)
    assert mem.search("anything", wide, threshold=None)[0][1]["id"] == b


def test_migration_from_old_db(tmp_path, backend):
    p = str(tmp_path / "m.db")
    make_old_db(p)
    before = raw_vectors(p)
    mem = M.MemoryStore(p)
    made = backups(tmp_path)
    assert len(made) == 1 and not glob.glob(str(tmp_path / "backups" / "*.partial"))
    bk = sqlite3.connect(made[0])
    assert bk.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert bk.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 40
    assert bk.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    names = {r[0] for r in bk.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"vec_summaries", "vec_meta"} & names        # the backup is the OLD layout
    bk.close()
    n_vec = sum(1 for _, v in before if v)
    assert mem.vec.count() == n_vec == mem.counts()[1]
    assert raw_vectors(p) == before                          # JSON untouched
    mem.close()
    mem2 = M.MemoryStore(p)                                  # second open: no new backup
    assert len(backups(tmp_path)) == 1 and mem2.vec.count() == n_vec


def test_new_empty_db_needs_no_backup(tmp_path):
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    assert mem.vec is not None and not backups(tmp_path)


def test_failure_mid_fill_changes_nothing_then_next_open_works(tmp_path, monkeypatch):
    p = str(tmp_path / "m.db")
    make_old_db(p)
    before = raw_vectors(p)
    real = vectors.VectorStore.upsert
    calls = []

    def boom(self, key, vec):
        calls.append(key)
        if len(calls) == 5:
            raise RuntimeError("injected")
        return real(self, key, vec)

    monkeypatch.setattr(vectors.VectorStore, "upsert", boom)
    mem = M.MemoryStore(p)
    assert mem.vec is None and mem.backend == "python (no index)"
    assert "vec_meta" not in tables(p) and "vec_summaries" not in tables(p)
    assert raw_vectors(p) == before
    assert mem.search("x", fake_embed16, k=3, threshold=None)       # search still works
    mem.close()
    monkeypatch.setattr(vectors.VectorStore, "upsert", real)
    mem2 = M.MemoryStore(p)
    assert mem2.vec is not None and mem2.vec.count() == sum(1 for _, v in before if v)
    assert len(backups(tmp_path)) == 2                              # the re-run backs up again


def test_failed_backup_creates_no_tables_and_search_works(tmp_path):
    p = str(tmp_path / "m.db")
    make_old_db(p)
    before = raw_vectors(p)
    (tmp_path / "backups").write_text("a file where the folder should be")   # makedirs will fail
    mem = M.MemoryStore(p)
    assert mem.vec is None
    assert "vec_meta" not in tables(p) and "vec_summaries" not in tables(p)
    assert raw_vectors(p) == before
    hits = mem.search("anything", fake_embed16, k=3, threshold=None)
    assert len(hits) == 3 and all(s["vector"] for _, s in hits)
    mem.add_summary("cx", summary("New", ["new"]), fake_embed16, embed_model="fake")   # writes still work
    assert mem.counts() == (41, 33)


def test_sync_repairs_a_drifted_index(tmp_path):
    p = str(tmp_path / "m.db")
    make_old_db(p)
    mem = M.MemoryStore(p)
    full = mem.vec.count()
    mem._db.execute("DELETE FROM vec_summaries WHERE key IN ('s1', 's2')")
    mem._db.execute("INSERT INTO vec_summaries (key, v) VALUES ('ghost', x'00000000')")
    mem._db.commit()
    mem.close()
    mem2 = M.MemoryStore(p)
    assert mem2.vec.count() == full and "ghost" not in mem2.vec.keys()
    assert len(backups(tmp_path)) == 1


def test_neither_backend_installed_memory_still_works(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    mem = M.MemoryStore(str(tmp_path / "m.db"))
    assert mem.backend == "python"
    a = mem.add_summary("c1", summary("Pip", ["pip venv windows"]), fake_embed, embed_model="fake")
    assert mem.search("pip venv", fake_embed)[0][1]["id"] == a
