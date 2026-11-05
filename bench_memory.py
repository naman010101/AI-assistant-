"""Manual timing for memory search (step 1.6). Not a pytest test (timings are flaky on CI).

    venv\\Scripts\\python bench_memory.py [--n 1000] [--dim 768] [--dir FOLDER]

Works in a fresh temporary folder (or --dir, which must be empty or not exist) with random
vectors. It never opens the real %LOCALAPPDATA%\\HybridAI data folder.
"""
import argparse
import json
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import memory  # noqa: E402
import vectors  # noqa: E402

OLD_SCHEMA = """
CREATE TABLE summaries (id TEXT PRIMARY KEY, conv_id TEXT, title TEXT NOT NULL, data TEXT NOT NULL,
    started REAL, created REAL NOT NULL, vector TEXT, model TEXT);
CREATE TABLE facts (id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'user',
    pinned INTEGER NOT NULL DEFAULT 0, source TEXT, created REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active');
"""


def make_old_db(path, n, dim, seed=7):
    rng = random.Random(seed)
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    for i in range(n):
        vec = memory.normalise([rng.gauss(0, 1) for _ in range(dim)])
        con.execute("INSERT INTO summaries VALUES (?, 'c', ?, ?, NULL, ?, ?, 'bench')",
                    (f"s{i}", f"Title {i}", json.dumps({"bullets": [f"point {i}"] * 4}), 1000.0 + i,
                     json.dumps(vec)))
    con.commit()
    con.close()


def old_search(mem, q, k=3, threshold=None):
    """The search as it was before 1.6: load and parse every row, dot product in Python."""
    rows = [s for s in mem.summaries() if s["vector"]]
    scored = sorted(((memory.dot(q, s["vector"]), s) for s in rows), key=lambda x: -x[0])
    return [(sim, s) for sim, s in scored[:k] if threshold is None or sim >= threshold]


def timed(fn, runs):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), min(times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--dir", default=None)
    args = ap.parse_args()
    base = args.dir or tempfile.mkdtemp(prefix="hybridai-bench-")
    if os.path.exists(base) and os.listdir(base):
        sys.exit(f"{base} is not empty; give an empty or new folder")
    real = os.path.join(os.environ.get("LOCALAPPDATA", "?"), "HybridAI").lower()
    if os.path.abspath(base).lower().startswith(real):
        sys.exit("refusing to run inside the real data folder")
    os.makedirs(base, exist_ok=True)
    print(f"{args.n} summaries x {args.dim} dims in {base}\n")

    rng = random.Random(11)
    q = memory.normalise([rng.gauss(0, 1) for _ in range(args.dim)])
    embed = lambda texts, kind="document": [q for _ in texts]  # noqa: E731

    # the old search, on a database that has no index (never opened by MemoryStore)
    old_path = os.path.join(base, "old", "memory.db")
    os.makedirs(os.path.dirname(old_path))
    make_old_db(old_path, args.n, args.dim)
    probe = sqlite3.connect(old_path)
    probe.row_factory = sqlite3.Row

    class OldView:        # summaries() as MemoryStore did it, without touching the schema
        def summaries(self):
            return [memory.MemoryStore._row(r) for r in probe.execute("SELECT * FROM summaries ORDER BY created DESC")]
    med, best = timed(lambda: old_search(OldView(), q), 5)
    print(f"old memory.search (JSON + python loop): median {med:7.1f} ms   best {best:7.1f} ms")
    probe.close()

    for i, backend in enumerate(vectors.available()):
        path = os.path.join(base, backend, "memory.db")
        os.makedirs(os.path.dirname(path))
        make_old_db(path, args.n, args.dim)
        os.environ[vectors.ENV_VAR] = backend
        t0 = time.perf_counter()
        mem = memory.MemoryStore(path)       # backup + build the index
        mig = time.perf_counter() - t0
        assert mem.backend == backend, mem.backend
        mem.search("warm up", embed, k=3, threshold=None)   # first call builds numpy/python caches
        med, best = timed(lambda: mem.search("bench query", embed, k=3, threshold=None), 20)
        print(f"new search, {backend:10s}: median {med:7.2f} ms   best {best:7.2f} ms   "
              f"(one-time migration incl. backup: {mig:.2f} s)")
        mem.close()
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
