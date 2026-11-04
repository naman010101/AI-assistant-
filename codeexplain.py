"""Code explainer: understand a project, especially code an AI wrote for you.

    proj = Project(root)                  # a folder you added in the Code window
    pmap = proj.scan()                    # files, classes, functions, calls
    text = map_text(pmap)                 # compact outline for the model
    ch = proj.changes()                   # what changed since you last reviewed
    proj.mark_reviewed(note)              # save a snapshot + journal entry

Nothing here calls a model. It prepares the material (project map, diffs,
the source of the relevant functions) and app.py starts a chat with it, so
the explanation streams like any reply and can be followed up.

Python is understood properly with the `ast` module (classes, functions,
arguments, docstrings, who-calls-what). JavaScript/TypeScript, Java, C, C++,
C#, Go and Rust get a lighter outline from patterns.
"""
import ast
import difflib
import hashlib
import json
import logging
import os
import re
import time

import config

log = logging.getLogger("hybridai")

SKIP_DIRS = {".git", ".hg", ".svn", "venv", ".venv", "env", "node_modules", "__pycache__", ".idea",
             ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache", ".cache", "site-packages",
             ".next", "target", "bin", "obj", ".gradle", ".ipynb_checkpoints"}
CODE_EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".java": "java", ".c": "c", ".h": "c",
            ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust",
            ".kt": "kotlin", ".swift": "swift", ".rb": "ruby", ".php": "php", ".sql": "sql",
            ".html": "html", ".css": "css", ".ipynb": "notebook", ".md": "markdown", ".bat": "batch",
            ".ps1": "powershell", ".sh": "shell", ".toml": "config", ".yaml": "config", ".yml": "config",
            ".json": "config", ".txt": "text"}
MAX_FILE_BYTES = 300_000
MAX_FILES = 1500
SECRETISH = re.compile(r"(^\.env|\.pem$|\.key$|id_rsa|credentials|secret|token\.json$|\.db$|\.sqlite)", re.I)


def _project_dir(root):
    pid = hashlib.sha1(os.path.normcase(os.path.abspath(root)).encode()).hexdigest()[:12]
    return os.path.join(config.DATA_DIR, "projects", pid)


# ================================================================ outline
def outline_python(text):
    """Classes, functions and calls of one Python file."""
    tree = ast.parse(text)
    items = []

    def doc(node):
        d = ast.get_docstring(node) or ""
        return d.strip().splitlines()[0][:120] if d.strip() else ""

    def calls(node):
        names = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name):
                    names.add(f.id)
                elif isinstance(f, ast.Attribute):
                    names.add(f.attr)
        return sorted(names)

    def args(fn):
        a = [x.arg for x in fn.args.args]
        if fn.args.vararg:
            a.append("*" + fn.args.vararg.arg)
        if fn.args.kwarg:
            a.append("**" + fn.args.kwarg.arg)
        return a

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append({"kind": "function", "name": node.name, "args": args(node), "doc": doc(node),
                          "line": node.lineno, "end": node.end_lineno, "calls": calls(node)})
        elif isinstance(node, ast.ClassDef):
            methods = []
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({"kind": "method", "name": sub.name, "args": args(sub), "doc": doc(sub),
                                    "line": sub.lineno, "end": sub.end_lineno, "calls": calls(sub)})
            items.append({"kind": "class", "name": node.name, "doc": doc(node), "line": node.lineno,
                          "end": node.end_lineno, "methods": methods,
                          "bases": [ast.unparse(b) for b in node.bases]})
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(("." * node.level) + node.module)
    return {"items": items, "imports": sorted(set(imports)), "doc": doc(tree)}


_PATTERNS = {
    "javascript": [r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)",
                   r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)",
                   r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"],
    "java": [r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?(?:abstract\s+)?(?:class|interface|enum|record)\s+(\w+)",
             r"^\s*(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],\s]+\s+(\w+)\s*\([^;]*\)\s*(?:throws [\w.,\s]+)?\{?\s*$"],
    "c": [r"^[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$",
          r"^\s*(?:typedef\s+)?struct\s+(\w+)"],
    "go": [r"^func\s+(?:\([^)]*\)\s*)?(\w+)", r"^type\s+(\w+)\s+(?:struct|interface)"],
    "rust": [r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl)\s+(\w+)"],
}
_PATTERNS["typescript"] = _PATTERNS["javascript"]
_PATTERNS["cpp"] = _PATTERNS["c"] + [r"^\s*class\s+(\w+)"]
_PATTERNS["csharp"] = _PATTERNS["java"]
_PATTERNS["kotlin"] = [r"^\s*(?:\w+\s+)*fun\s+(\w+)", r"^\s*(?:\w+\s+)*class\s+(\w+)"]
_KEYWORDS = {"if", "for", "while", "switch", "return", "catch", "sizeof", "else", "do", "main"} - {"main"}


def outline_pattern(text, lang):
    items = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in _PATTERNS.get(lang, []):
            m = re.match(pat, line)
            if m and m.group(1) not in _KEYWORDS:
                kind = "class" if re.search(r"\b(class|struct|interface|enum|type|trait|record)\b", line) \
                    else "function"
                items.append({"kind": kind, "name": m.group(1), "line": i, "doc": "", "calls": []})
                break
    return {"items": items, "imports": [], "doc": ""}


# ================================================================ project
class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.name = os.path.basename(self.root.rstrip("\\/")) or self.root
        self.dir = _project_dir(self.root)

    # ---------- files ----------
    def files(self, blocked=None):
        """Source files (relative paths). `blocked(path) -> reason` filters secrets."""
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                ext = os.path.splitext(name)[1].lower()
                if ext not in CODE_EXT or SECRETISH.search(name):
                    continue
                full = os.path.join(dirpath, name)
                if blocked and blocked(full):
                    continue
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                out.append(os.path.relpath(full, self.root).replace("\\", "/"))
                if len(out) >= MAX_FILES:
                    return out
        return out

    def read(self, rel):
        with open(os.path.join(self.root, rel), "rb") as f:
            raw = f.read()
        for enc in ("utf-8-sig", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    # ---------- map ----------
    def scan(self, blocked=None):
        files = []
        for rel in self.files(blocked):
            lang = CODE_EXT[os.path.splitext(rel)[1].lower()]
            try:
                text = self.read(rel)
            except OSError:
                continue
            entry = {"path": rel, "lang": lang, "lines": text.count("\n") + 1}
            if lang == "python":
                try:
                    entry.update(outline_python(text))
                except SyntaxError as e:
                    entry.update({"items": [], "imports": [], "doc": "", "error": f"syntax error line {e.lineno}"})
            elif lang in _PATTERNS:
                entry.update(outline_pattern(text, lang))
            else:
                entry.update({"items": [], "imports": [], "doc": ""})
            files.append(entry)
        defined = {}
        for f in files:
            for it in f.get("items", []):
                defined.setdefault(it["name"], []).append(f["path"])
                for m in it.get("methods", []):
                    defined.setdefault(m["name"], []).append(f["path"])
        return {"root": self.root, "name": self.name, "files": files, "defined": defined,
                "scanned": time.time()}

    def find_symbol(self, pmap, name):
        """[(path, item)] for a function/class/method called `name`."""
        out = []
        for f in pmap["files"]:
            for it in f.get("items", []):
                if it["name"] == name:
                    out.append((f["path"], it))
                for m in it.get("methods", []):
                    if m["name"] == name or f"{it['name']}.{m['name']}" == name:
                        out.append((f["path"], {**m, "class": it["name"]}))
        return out

    def source_of(self, rel, item):
        lines = self.read(rel).splitlines()
        end = item.get("end") or min(len(lines), item["line"] + 40)
        return "\n".join(lines[item["line"] - 1:end])

    # ---------- changes since last review ----------
    def _snapshot_path(self):
        return os.path.join(self.dir, "snapshot.json")

    def has_snapshot(self):
        return os.path.exists(self._snapshot_path())

    def snapshot(self, blocked=None):
        snap = {}
        for rel in self.files(blocked):
            try:
                snap[rel] = self.read(rel)
            except OSError as _exc:
                log.warning("couldn't read a file for the project snapshot: %s", _exc)
        return snap

    def changes(self, blocked=None):
        """Changes since the last review: list of dicts (path, status, added,
        removed, diff). Without a snapshot yet, returns None."""
        if not self.has_snapshot():
            return None
        with open(self._snapshot_path(), encoding="utf-8") as f:
            old = json.load(f)["files"]
        new = self.snapshot(blocked)
        out = []
        for rel in sorted(set(old) | set(new)):
            a, b = old.get(rel), new.get(rel)
            if a == b:
                continue
            status = "added" if a is None else "deleted" if b is None else "modified"
            diff = list(difflib.unified_diff((a or "").splitlines(), (b or "").splitlines(),
                                             f"a/{rel}", f"b/{rel}", n=3, lineterm=""))
            added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            out.append({"path": rel, "status": status, "added": added, "removed": removed,
                        "diff": "\n".join(diff)})
        return out

    def mark_reviewed(self, blocked=None, changes=None, explanation=None):
        """Save the current state as reviewed and add a journal entry."""
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._snapshot_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"root": self.root, "time": time.time(), "files": self.snapshot(blocked)}, f)
        os.replace(tmp, self._snapshot_path())
        if changes:
            self.add_journal(changes, explanation)

    # ---------- journal ----------
    def add_journal(self, changes, explanation=None):
        os.makedirs(self.dir, exist_ok=True)
        entry = {"time": time.time(), "files": [{k: c[k] for k in ("path", "status", "added", "removed")}
                                                 for c in changes], "explanation": explanation or ""}
        with open(os.path.join(self.dir, "journal.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def journal(self):
        p = os.path.join(self.dir, "journal.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return list(reversed([json.loads(l) for l in f if l.strip()]))


# ================================================================ text for the model
def map_text(pmap, max_chars=12000):
    """The project as a compact outline."""
    lines = [f"Project: {pmap['name']} ({len(pmap['files'])} files)"]
    for f in pmap["files"]:
        head = f"\n{f['path']}  [{f['lang']}, {f['lines']} lines]"
        if f.get("doc"):
            head += f" — {f['doc']}"
        if f.get("error"):
            head += f" ({f['error']})"
        lines.append(head)
        local_imports = [i for i in f.get("imports", []) if i.split(".")[0] in pmap["defined"] or i.startswith(".")
                         or any(x["path"].startswith(i.replace(".", "/")) for x in pmap["files"])]
        if local_imports:
            lines.append(f"  imports: {', '.join(local_imports)}")
        for it in f.get("items", []):
            if it["kind"] == "class":
                lines.append(f"  class {it['name']}" + (f"({', '.join(it.get('bases', []))})" if it.get("bases") else "")
                             + (f" — {it['doc']}" if it.get("doc") else ""))
                for m in it.get("methods", []):
                    lines.append(f"    .{m['name']}({', '.join(m['args'])})" + (f" — {m['doc']}" if m.get("doc") else ""))
            else:
                args = f"({', '.join(it.get('args', []))})" if "args" in it else "()"
                lines.append(f"  {it['name']}{args}" + (f" — {it['doc']}" if it.get("doc") else ""))
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (outline cut to fit; ask about a specific file for more)"
    return text


def callers(pmap, name):
    """Functions/methods in the project that call `name`."""
    out = []
    for f in pmap["files"]:
        for it in f.get("items", []):
            if name in it.get("calls", []):
                out.append(f"{f['path']}:{it['name']}")
            for m in it.get("methods", []):
                if name in m.get("calls", []):
                    out.append(f"{f['path']}:{it['name']}.{m['name']}")
    return out


_WORD = re.compile(r"[a-z]{3,}")
_STOP = {"how", "does", "what", "where", "when", "why", "the", "and", "for", "with", "this", "that",
         "work", "works", "happen", "happens", "code", "function", "file", "project", "from", "into", "are"}


def _stem(w):
    for suf in ("ing", "ed", "es", "s", "er"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w[:5]


def _units(pmap, project):
    """Every function/method with its source: [(path, item, body)]."""
    out = []
    for f in pmap["files"]:
        if not f.get("items"):
            continue
        try:
            src_lines = project.read(f["path"]).splitlines()
        except OSError:
            continue
        units = []
        for it in f["items"]:
            units += [{**m, "class": it["name"]} for m in it.get("methods", [])] if it["kind"] == "class" else [it]
        for it in units:
            end = it.get("end") or min(len(src_lines), it["line"] + 30)
            out.append((f["path"], it, "\n".join(src_lines[it["line"] - 1:end])))
    return out


def _keyword_score(question, it, body):
    q = {_stem(w) for w in _WORD.findall(question.lower()) if w not in _STOP}
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", it["name"]).lower().replace("_", " ")
    name_words = {_stem(w) for w in re.findall(r"[a-z]{3,}", name)}
    words = {_stem(w) for w in _WORD.findall((it.get("doc", "") + " " + body).lower())}
    return 3 * len(q & name_words) + len(q & words)


def _vectors(project, units, embed_fn):
    """Embeddings of every unit, cached on disk by content."""
    path = os.path.join(project.dir, "vectors.json")
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    keys, todo = [], []
    for rel, it, body in units:
        text = f"{rel} {it.get('class', '')}.{it['name']}: {it.get('doc', '')}\n{body[:1500]}"
        k = hashlib.sha1(text.encode("utf-8")).hexdigest()
        keys.append(k)
        if k not in cache:
            todo.append((k, text))
    for i in range(0, len(todo), 32):
        batch = todo[i:i + 32]
        for (k, _), v in zip(batch, embed_fn([t for _, t in batch], kind="document")):
            cache[k] = v
    if todo:
        os.makedirs(project.dir, exist_ok=True)
        keep = set(keys)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in cache.items() if k in keep}, f)
    return [cache[k] for k in keys]


def relevant(pmap, project, question, k=6, embed_fn=None):
    """The functions most related to a question: by meaning (embeddings) when
    available, plus word overlap. Tests rank lower unless asked about.
    Returns [(path, item, source)]."""
    units = _units(pmap, project)
    if not units:
        return []
    sims = None
    if embed_fn:
        try:
            vecs = _vectors(project, units, embed_fn)
            q = embed_fn([question], kind="query")[0]
            sims = [sum(a * b for a, b in zip(q, v)) for v in vecs]
        except Exception:
            sims = None
    wants_tests = "test" in question.lower()
    scored = []
    for i, (rel, it, body) in enumerate(units):
        kw = _keyword_score(question, it, body)
        score = (sims[i] + 0.03 * kw) if sims is not None else kw
        if not wants_tests and (rel.startswith("tests/") or "/test_" in rel or rel.startswith("test_")):
            score *= 0.8 if sims is not None else 0.5
        if score > 0:
            scored.append((score, rel, it, body))
    scored.sort(key=lambda x: -x[0])
    return [(p, it, body) for _, p, it, body in scored[:k]]


def changes_text(changes, max_chars=14000):
    if not changes:
        return "No changes."
    head = "\n".join(f"- {c['status']}: {c['path']} (+{c['added']} −{c['removed']})" for c in changes)
    budget = max_chars - len(head)
    per = max(800, budget // max(len(changes), 1))
    diffs = []
    for c in changes:
        d = c["diff"]
        if len(d) > per:
            d = d[:per] + f"\n… (diff of {c['path']} cut; {len(c['diff']) - per} more characters)"
        diffs.append(d)
    return "Changed files:\n" + head + "\n\nDiffs:\n" + "\n\n".join(diffs)


def budget_chars(context_tokens):
    """Characters a code prompt may use: about half the window (the rest is
    for memory notes, the reply and follow-ups)."""
    return int(context_tokens * 3.5 * 0.5)


DEPTH = {
    "summary": "Explain it in a short paragraph: what it does and how it fits into the project.",
    "walkthrough": "Walk through it step by step: what each part does, in order, and why. Mention the "
                   "inputs, outputs and anything surprising.",
    "line": "Explain it line by line (group trivial lines). For each Python/programming concept used, "
            "add one sentence on what it is, as if teaching a student.",
}


def prompt_project(pmap, chars=14000):
    return ("Here is the outline of my project. Give me an overview: what the project does, the main "
            "parts and how they connect (which file calls which), where execution starts, and the "
            "3 files I should read first to understand it.\n\n" + map_text(pmap, chars - 400))


def prompt_changes(pmap, changes, chars=14000):
    return ("These changes were made to my project, probably by an AI coding tool, since I last reviewed it. "
            "Explain them so I fully understand what happened: for each file, what changed and why it "
            "matters; how the changes connect to each other; anything that looks risky, unfinished or "
            "inconsistent; and what I should test.\n\nProject outline:\n" + map_text(pmap, chars // 4) +
            "\n\n" + changes_text(changes, chars * 3 // 4 - 500))


def prompt_explain(pmap, project, rel, item=None, depth="walkthrough", chars=14000):
    room = chars - chars // 6 - 500
    if item is None:
        src = project.read(rel)
        if len(src) > room:
            src = src[:room] + "\n… (file cut to fit; ask about a specific function for the rest)"
        what = f"the file {rel}"
    else:
        src = project.source_of(rel, item)
        if len(src) > room:
            src = src[:room] + "\n… (cut to fit)"
        name = f"{item['class']}.{item['name']}" if item.get("class") else item["name"]
        what = f"`{name}` in {rel} (lines {item['line']}–{item.get('end') or '?'})"
        who = callers(pmap, item["name"])
        if who:
            what += f". It is called from: {', '.join(who[:8])}"
    return (f"Explain {what}. {DEPTH.get(depth, DEPTH['walkthrough'])}\n\n"
            f"```{CODE_EXT.get(os.path.splitext(rel)[1].lower(), '')}\n{src}\n```\n\n"
            f"Project outline for context:\n{map_text(pmap, chars // 6)}")


def prompt_trace(pmap, project, question, embed_fn=None, chars=14000):
    hits = relevant(pmap, project, question, embed_fn=embed_fn)
    per = max(600, (chars * 2 // 3) // max(len(hits), 1))
    parts = [f"In my project, {question.strip()}\n\nTrace the path through the code: start where it begins "
             f"(user action or entry point), then each function it goes through, in order, with the file "
             f"name, and what each step does. Only use the code shown; if a step isn't shown, say so."]
    for rel, it, body in hits:
        name = f"{it['class']}.{it['name']}" if it.get("class") else it["name"]
        if len(body) > per:
            body = body[:per] + "\n…"
        parts.append(f"{rel} — {name} (line {it['line']}):\n```\n{body}\n```")
    parts.append("Project outline:\n" + map_text(pmap, chars // 4))
    return "\n\n".join(parts)


def prompt_quiz(pmap, project, chars=14000):
    hits = relevant(pmap, project, " ".join(f["path"] for f in pmap["files"][:20]) + " main run start save load",
                    k=4)
    code = "\n\n".join(f"{rel} — {it['name']}:\n```\n{body[:chars // 8]}\n```" for rel, it, body in hits)
    return ("Quiz me on my own project to check I really understand it (like a viva or an interview). "
            "Ask ONE question at a time, wait for my answer, tell me what was right or missing, then ask "
            "the next. Mix questions about the overall design, how specific functions work, and 'what "
            "would happen if…'. 5 questions in total, then give me a score and what to revise.\n\n"
            "Project outline:\n" + map_text(pmap, chars // 2) + "\n\nSome of the code:\n" + code)


def prompt_error(pmap, project, traceback_text):
    frames = re.findall(r'File "([^"]+)", line (\d+), in (\S+)', traceback_text)
    shown = []
    for path, line, func in frames[-4:]:
        try:
            rel = os.path.relpath(path, project.root).replace("\\", "/")
        except ValueError:
            continue
        if rel.startswith(".."):
            continue
        try:
            lines = project.read(rel).splitlines()
        except OSError:
            continue
        n = int(line)
        snippet = "\n".join(f"{i:>4}{' >' if i == n else '  '} {lines[i - 1]}"
                            for i in range(max(1, n - 6), min(len(lines), n + 4) + 1))
        shown.append(f"{rel}, around line {n} (in {func}):\n```\n{snippet}\n```")
    return ("I got this error in my project. Explain what it means in plain words, which line in MY code "
            "causes it and why, and how to fix it.\n\n```\n" + traceback_text.strip()[-6000:] + "\n```\n\n"
            + ("\n\n".join(shown) if shown else "(the files in the traceback aren't in this project)"))
