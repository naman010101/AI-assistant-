"""Vector index for similarity search (step 1.6).

    vs = VectorStore(conn, "summaries")      # uses the caller's connection
    vs.upsert("id1", [0.1, 0.2, ...])
    vs.search(query_vec, k=3, threshold=0.5) -> [(key, cosine score)]

One table layout for every backend: vec_<name>(n INTEGER PRIMARY KEY, key TEXT
UNIQUE, v BLOB) with float32 little-endian blobs, plus vec_meta(name, dim, built).
The backends differ only in how they score:

    sqlite_vec  the scalar vec_distance_cosine() inside SQLite
    numpy       a float32 matrix held in memory, M @ q
    python      array('f') rows and a plain loop (always works)

The index is DERIVED data: whoever owns the vectors keeps the real copy and can
rebuild this at any time. VectorStore does not lock and does not commit: the
caller holds its own lock and writes inside its own transaction.
"""
import logging
import math
import os
import re
import sqlite3
import sys
import time
from array import array

log = logging.getLogger("hybridai")

BACKENDS = ("sqlite_vec", "numpy", "python")   # preference order
ENV_VAR = "HYBRID_AI_VECTORS"
_NAME = re.compile(r"[a-z_]+")


class DimensionMismatch(ValueError):
    """A vector's length differs from the index's."""


# ---------------------------------------------------------------- packing
def _normalised(vec):
    v = array("f", vec)
    n = math.sqrt(sum(x * x for x in v))
    if n > 0:
        v = array("f", (x / n for x in v))
    return v


def pack(vec):
    """float32 little-endian bytes of the normalised vector."""
    v = _normalised(vec)
    if sys.byteorder == "big":
        v.byteswap()
    return v.tobytes()


def unpack(blob):
    v = array("f")
    v.frombytes(blob)
    if sys.byteorder == "big":
        v.byteswap()
    return v


# ---------------------------------------------------------------- backends
def _load_sqlite_vec(conn):
    """Load the sqlite-vec extension into conn; loading is switched off again at once."""
    import sqlite_vec
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
    conn.execute("SELECT vec_version()").fetchone()


def _check_numpy():
    import numpy  # noqa: F401


def _usable(name, conn=None):
    """True if the backend imports (and, for sqlite_vec, loads into conn). Logs why not."""
    own = None
    try:
        if name == "sqlite_vec":
            if conn is None:
                own = conn = sqlite3.connect(":memory:")
            _load_sqlite_vec(conn)
        elif name == "numpy":
            _check_numpy()
        return True
    except Exception as e:
        log.warning("vector backend %s is not usable: %s: %s", name, type(e).__name__, e)
        return False
    finally:
        if own is not None:
            own.close()


def available():
    """Backends that import and load here. Always includes 'python'."""
    return [b for b in BACKENDS if b == "python" or _usable(b)]


def pick(preferred=None, conn=None):
    """The backend to use: `preferred`, else $HYBRID_AI_VECTORS, else the best that works.
    One that fails is logged and the next is tried. sqlite_vec is loaded into `conn` if given."""
    want = preferred or os.environ.get(ENV_VAR, "").strip().lower() or None
    order = ([want] if want in BACKENDS else []) + [b for b in BACKENDS if b != want]
    if want and want not in BACKENDS:
        log.warning("%s=%r is not a known backend (use one of %s)", ENV_VAR, want, ", ".join(BACKENDS))
    for b in order:
        if b == "python" or _usable(b, conn):
            return b
    return "python"


# ---------------------------------------------------------------- store
class VectorStore:
    def __init__(self, conn, name, backend=None):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError(f"bad index name {name!r}: use lower-case letters and underscores")
        self.conn = conn
        self.name = name
        self.table = f"vec_{name}"
        self.backend = pick(backend, conn)
        self._dirty = True
        self._keys = []
        self._mat = None      # numpy matrix, or list of array('f') for the python backend
        conn.execute(f"CREATE TABLE IF NOT EXISTS {self.table} "
                     "(n INTEGER PRIMARY KEY, key TEXT UNIQUE, v BLOB)")
        conn.execute("CREATE TABLE IF NOT EXISTS vec_meta (name TEXT PRIMARY KEY, dim INTEGER, built REAL)")
        conn.execute("INSERT OR IGNORE INTO vec_meta (name, dim, built) VALUES (?, NULL, ?)",
                     (name, time.time()))
        log.info("vector index %s uses the %s backend", name, self.backend)

    @property
    def dim(self):
        r = self.conn.execute("SELECT dim FROM vec_meta WHERE name = ?", (self.name,)).fetchone()
        return r[0] if r else None

    def _set_dim(self, dim):
        self.conn.execute("UPDATE vec_meta SET dim = ? WHERE name = ?", (dim, self.name))

    def invalidate(self):
        """Forget the in-memory copy (call after a rolled-back write)."""
        self._dirty = True
        self._keys, self._mat = [], None

    # ----- writes (inside the caller's transaction)
    def upsert(self, key, vec):
        if len(vec) == 0:
            raise ValueError("empty vector")
        dim = self.dim
        if dim is None:
            self._set_dim(len(vec))
        elif len(vec) != dim:
            raise DimensionMismatch(f"vector has {len(vec)} dimensions, index {self.name} has {dim}")
        self._dirty = True
        self.conn.execute(f"INSERT INTO {self.table} (key, v) VALUES (?, ?) "
                          "ON CONFLICT(key) DO UPDATE SET v = excluded.v", (key, pack(vec)))

    def delete(self, key):
        self._dirty = True
        self.conn.execute(f"DELETE FROM {self.table} WHERE key = ?", (key,))

    def reset(self, dim):
        """Empty the index and set its dimension (the owner then re-adds what fits)."""
        self._dirty = True
        self.conn.execute(f"DELETE FROM {self.table}")
        self._set_dim(dim)
        self.invalidate()

    # ----- reads
    def keys(self):
        return {r[0] for r in self.conn.execute(f"SELECT key FROM {self.table}")}

    def count(self):
        return self.conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]

    def search(self, vec, k, threshold=None):
        """Top k keys by cosine similarity, THEN those with score >= threshold (None = keep all)."""
        dim = self.dim
        if len(vec) == 0 or (dim is not None and len(vec) != dim):
            raise DimensionMismatch(f"query has {len(vec)} dimensions, index {self.name} has {dim}")
        if k <= 0 or dim is None:
            return []
        if self.backend == "sqlite_vec":
            out = self._search_sql(vec, k)
        else:
            if self._dirty:
                self._rebuild()
            if not self._keys:
                return []
            out = self._search_numpy(vec, k) if self.backend == "numpy" else self._search_python(vec, k)
        return [(key, s) for key, s in out if threshold is None or s >= threshold]

    def _search_sql(self, vec, k):
        rows = self.conn.execute(
            f"SELECT key, COALESCE(1 - vec_distance_cosine(v, ?), 0) s FROM {self.table} "
            "ORDER BY s DESC, n DESC LIMIT ?", (pack(vec), int(k))).fetchall()
        return [(r[0], float(r[1])) for r in rows]

    def _rebuild(self):
        rows = self.conn.execute(f"SELECT key, v FROM {self.table} ORDER BY n").fetchall()
        self._keys = [r[0] for r in rows]
        if self.backend == "numpy":
            import numpy as np
            dim = self.dim
            self._mat = (np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").reshape(len(rows), dim)
                         if rows else None)
        else:
            self._mat = [unpack(r[1]) for r in rows]
        self._dirty = False

    def _search_numpy(self, vec, k):
        import numpy as np
        q = np.frombuffer(pack(vec), dtype="<f4")
        scores = self._mat @ q
        count = len(scores)
        if k < count:   # find the k-th best score, keep everything tied with it, then order
            cut = np.partition(scores, count - k)[count - k]
            cand = np.nonzero(scores >= cut)[0]
        else:
            cand = np.arange(count)
        order = cand[np.lexsort((-cand, -scores[cand]))][:k]   # best score first, newest first on ties
        return [(self._keys[i], float(scores[i])) for i in order]

    def _search_python(self, vec, k):
        q = _normalised(vec)
        scored = [(sum(a * b for a, b in zip(q, row)), i) for i, row in enumerate(self._mat)]
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [(self._keys[i], float(s)) for s, i in scored[:k]]
