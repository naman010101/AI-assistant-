"""Tests for codeexplain.py (Phase 4)."""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def cx(tmp_path, monkeypatch):
    monkeypatch.setenv("HYBRID_AI_DATA_DIR", str(tmp_path / "data"))
    import config
    importlib.reload(config)
    import codeexplain
    importlib.reload(codeexplain)
    return codeexplain


@pytest.fixture()
def proj(tmp_path):
    root = tmp_path / "shop"
    (root / "app").mkdir(parents=True)
    (root / "venv" / "lib").mkdir(parents=True)
    (root / "app" / "cart.py").write_text(
        '"""Shopping cart."""\nfrom app.db import save_order\n\n\n'
        'class Cart:\n    """Holds items."""\n    def __init__(self):\n        self.items = []\n\n'
        '    def add(self, item, qty=1):\n        """Add an item."""\n        self.items.append((item, qty))\n\n'
        '    def checkout(self):\n        total = sum(q for _, q in self.items)\n        save_order(self.items)\n'
        '        return total\n')
    (root / "app" / "db.py").write_text('def save_order(items):\n    """Write the order to the database."""\n'
                                        '    print("saving", items)\n')
    (root / "web.js").write_text("export function render(cart) {\n  return cart.length;\n}\n"
                                 "const total = (a, b) => a + b;\nclass View {}\n")
    (root / "venv" / "lib" / "big.py").write_text("x = 1\n")
    (root / ".env").write_text("SECRET=1\n")
    (root / "broken.py").write_text("def oops(:\n")
    return str(root)


def test_scan_map(cx, proj):
    p = cx.Project(proj)
    m = p.scan()
    paths = [f["path"] for f in m["files"]]
    assert "app/cart.py" in paths and "web.js" in paths
    assert not any(x.startswith("venv") for x in paths) and ".env" not in paths
    cart = next(f for f in m["files"] if f["path"] == "app/cart.py")
    cls = cart["items"][0]
    assert cls["name"] == "Cart" and [x["name"] for x in cls["methods"]] == ["__init__", "add", "checkout"]
    assert "save_order" in cls["methods"][2]["calls"] and cart["doc"] == "Shopping cart."
    js = next(f for f in m["files"] if f["path"] == "web.js")
    assert {i["name"] for i in js["items"]} == {"render", "total", "View"}
    assert next(f for f in m["files"] if f["path"] == "broken.py")["error"].startswith("syntax error")
    text = cx.map_text(m)
    assert "class Cart" in text and ".add(self, item, qty) — Add an item." in text
    assert cx.callers(m, "save_order") == ["app/cart.py:Cart.checkout"]


def test_find_symbol_and_source(cx, proj):
    p = cx.Project(proj)
    m = p.scan()
    hits = p.find_symbol(m, "Cart.checkout")
    assert len(hits) == 1 and "save_order(self.items)" in p.source_of(*hits[0])
    prompt = cx.prompt_explain(m, p, *hits[0], depth="line")
    assert "called from" not in prompt and "line by line" in prompt
    prompt2 = cx.prompt_explain(m, p, *p.find_symbol(m, "save_order")[0])
    assert "called from: app/cart.py:Cart.checkout" in prompt2


def test_changes_and_journal(cx, proj):
    p = cx.Project(proj)
    assert p.changes() is None  # no review yet
    p.mark_reviewed()
    assert p.changes() == []
    with open(os.path.join(proj, "app", "db.py"), "a") as f:
        f.write("\n\ndef load_orders():\n    return []\n")
    os.remove(os.path.join(proj, "web.js"))
    with open(os.path.join(proj, "app", "new.py"), "w") as f:
        f.write("X = 1\n")
    ch = {c["path"]: c for c in p.changes()}
    assert ch["app/db.py"]["status"] == "modified" and ch["app/db.py"]["added"] == 4
    assert ch["web.js"]["status"] == "deleted" and ch["app/new.py"]["status"] == "added"
    prompt = cx.prompt_changes(p.scan(), list(ch.values()))
    assert "+def load_orders():" in prompt and "deleted: web.js" in prompt
    p.mark_reviewed(changes=list(ch.values()), explanation="added order loading")
    assert p.changes() == [] and p.journal()[0]["explanation"] == "added order loading"


def test_trace_and_error(cx, proj):
    p = cx.Project(proj)
    m = p.scan()
    prompt = cx.prompt_trace(m, p, "how does checkout save the order?")
    assert "Cart.checkout" in prompt and "save_order" in prompt
    tb = (f'Traceback (most recent call last):\n  File "{os.path.join(proj, "app", "cart.py")}", line 16, in checkout\n'
          f'    save_order(self.items)\nNameError: name x')
    e = cx.prompt_error(m, p, tb)
    assert "app/cart.py, around line 16" in e and "  16 >" in e


def test_blocked_filter(cx, proj):
    p = cx.Project(proj)
    files = p.files(blocked=lambda path: "db.py" in path)
    assert "app/db.py" not in files and "app/cart.py" in files


def test_relevant_with_embeddings_and_cache(cx, proj):
    calls = []

    def embed(texts, kind="document"):
        calls.append((kind, len(texts)))
        return [[1.0 if "save" in t.lower() or "order" in t.lower() else 0.0, 0.1] for t in texts]

    p = cx.Project(proj)
    m = p.scan()
    hits = cx.relevant(m, p, "how is an order written?", embed_fn=embed)
    assert hits[0][1]["name"] in ("save_order", "checkout")
    n_docs = sum(n for k, n in calls if k == "document")
    cx.relevant(m, p, "again", embed_fn=embed)
    assert sum(n for k, n in calls if k == "document") == n_docs  # vectors cached on disk
