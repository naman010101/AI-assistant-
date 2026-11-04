"""Step 0.3 check: no `except ...: pass` is left in the project code."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_bare_pass_in_except():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in ("venv", ".claude", "tests") or path.name.endswith("_backup.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and all(isinstance(s, ast.Pass) for s in node.body):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, "except followed by bare pass: " + ", ".join(offenders)
