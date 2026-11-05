"""Tests for vectors.py (step 1.6). Every backend that works here is tested; a missing one
makes no test case (it is not skipped). All databases live in tmp_path."""
import os
import random
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import memory as M  # noqa: E402
import vectors  # noqa: E402

BACKENDS = vectors.available()


def rand_vecs(n, dim, seed=1):
    rng = random.Random(seed)
    return [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(n)]


def store(tmp_path, backend, name="t"):
    conn = sqlite3.connect(str(tmp_path / "v.db"))
    return conn, vectors.VectorStore(conn, name, backend=backend)


def test_python_is_always_available():
    assert "python" in BACKENDS


@pytest.mark.parametrize("backend", BACKENDS)
def test_matches_float64_brute_force(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    assert vs.backend == backend
    vecs = rand_vecs(300, 32)
    with conn:
        for i, v in enumerate(vecs):
            vs.upsert(f"k{i}", v)
    q = rand_vecs(1, 32, seed=99)[0]
    want = sorted(((M.dot(M.normalise(q), M.normalise(v)), f"k{i}") for i, v in enumerate(vecs)),
                  key=lambda t: -t[0])[:10]
    got = vs.search(q, 10)
    assert [k for k, _ in got] == [k for _, k in want]
    assert all(abs(s - w) < 1e-5 for (_, s), (w, _) in zip(got, want))


@pytest.mark.parametrize("backend", BACKENDS)
def test_top_k_then_threshold(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    with conn:
        vs.upsert("a", [1, 0, 0])
        vs.upsert("b", [1, 1, 0])
        vs.upsert("c", [0.1, 1, 0])
        vs.upsert("d", [0, 0, 1])
    q = [1, 0, 0]
    assert [k for k, _ in vs.search(q, 3)] == ["a", "b", "c"]            # threshold None keeps all k
    assert [k for k, _ in vs.search(q, 3, threshold=0.5)] == ["a", "b"]
    assert vs.search(q, 1, threshold=0.5)[0][0] == "a"
    assert vs.search(q, 2, threshold=1.5) == []
    assert vs.search(q, 0) == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_ties_go_to_newest(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    with conn:
        for key in ("old", "mid", "new"):
            vs.upsert(key, [1, 0])
    assert [k for k, _ in vs.search([1, 0], 3)] == ["new", "mid", "old"]
    assert [k for k, _ in vs.search([1, 0], 1)] == ["new"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_upsert_delete_count_keys(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    with conn:
        vs.upsert("a", [1, 0])
        vs.upsert("b", [0, 1])
    assert vs.search([1, 0], 1)[0][0] == "a"
    with conn:
        vs.upsert("a", [0, 1])      # replaces
        vs.upsert("b", [1, 0])
    assert vs.count() == 2 and vs.keys() == {"a", "b"}
    assert vs.search([1, 0], 1)[0][0] == "b"
    with conn:
        vs.delete("b")
        vs.delete("nope")           # unknown key is fine
    assert vs.keys() == {"a"} and vs.search([1, 0], 5)[0][0] == "a"


@pytest.mark.parametrize("backend", BACKENDS)
def test_dimension_change_needs_reset(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    with conn:
        vs.upsert("a", [1, 0, 0])
    assert vs.dim == 3
    with pytest.raises(vectors.DimensionMismatch):
        vs.upsert("b", [1, 0])
    with pytest.raises(vectors.DimensionMismatch):
        vs.search([1, 0], 1)
    with conn:
        vs.reset(2)
        vs.upsert("b", [1, 0])
    assert vs.dim == 2 and vs.keys() == {"b"} and vs.search([1, 0], 3)[0][0] == "b"


@pytest.mark.parametrize("backend", BACKENDS)
def test_rollback_leaves_cache_correct(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    with conn:
        vs.upsert("a", [1, 0])
    try:
        with conn:
            vs.upsert("ghost", [1, 0])
            vs.delete("a")
            raise RuntimeError("fails after writing")
    except RuntimeError:
        vs.invalidate()
    assert vs.keys() == {"a"}
    assert [k for k, _ in vs.search([1, 0], 5)] == ["a"]


@pytest.mark.parametrize("name", ["", "Bad", "a b", "x;drop", "a1", "a-b", "../x"])
def test_bad_names_rejected(tmp_path, name):
    conn = sqlite3.connect(str(tmp_path / "v.db"))
    with pytest.raises(ValueError):
        vectors.VectorStore(conn, name, backend="python")
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] == 0


def test_same_file_readable_by_other_backends(tmp_path):
    """One storage format: what one backend wrote, another reads."""
    conn = sqlite3.connect(str(tmp_path / "v.db"))
    first = vectors.VectorStore(conn, "t", backend="python")
    with conn:
        first.upsert("a", [1, 0])
        first.upsert("b", [0, 1])
    for b in BACKENDS:
        other = vectors.VectorStore(conn, "t", backend=b)
        assert other.search([0, 1], 1)[0][0] == "b"


def test_neither_installed_falls_back_to_python(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    assert vectors.pick() == "python"
    assert vectors.available() == ["python"]
    conn, vs = store(tmp_path, None)
    assert vs.backend == "python"
    with conn:
        vs.upsert("a", [1, 0])
        vs.upsert("b", [0, 1])
    assert vs.search([0.1, 1], 1)[0][0] == "b"


def test_env_var_picks_backend(tmp_path, monkeypatch):
    monkeypatch.setenv(vectors.ENV_VAR, "python")
    assert vectors.pick() == "python"
    monkeypatch.setenv(vectors.ENV_VAR, "nonsense")
    assert vectors.pick() == BACKENDS[0]
    assert vectors.pick("python") == "python"       # explicit argument wins


def test_unloadable_backend_is_logged_and_skipped(tmp_path, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    with caplog.at_level("WARNING", logger="hybridai"):
        assert vectors.pick("sqlite_vec") in ("numpy", "python")
    assert "sqlite_vec is not usable" in caplog.text


@pytest.mark.parametrize("backend", [b for b in BACKENDS if b == "sqlite_vec"])
def test_sqlite_vec_load_extension_is_off_after_loading(tmp_path, backend):
    conn, vs = store(tmp_path, backend)
    assert vs.backend == "sqlite_vec"
    with pytest.raises(sqlite3.Error):
        conn.load_extension("does-not-exist")
    assert conn.execute("SELECT vec_version()").fetchone()[0].startswith("v")
