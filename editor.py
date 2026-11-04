"""Editing files the model proposes changes to — always with your Apply first.

The flow
  1. A text/code file is attached (whole, not cut) and editing is allowed.
  2. The model is told: when asked to change one of these files, reply with
     the COMPLETE new file in a ```edit:<name> code block (`instructions`).
  3. When the reply is done, `extract` finds those blocks (or, as a fallback,
     a code block that is clearly a modified copy of the file).
  4. The app shows a diff (`diff_rows`) with warnings (`checks`).
  5. Apply → `apply`: backup first, then write (same encoding and line
     endings as before). Undo → `undo` puts the backup back.

Nothing in this module touches the UI, so it's all testable.
"""
import ast
import datetime as _dt
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import time

import config

import privacy

log = logging.getLogger("hybridai")

EDITABLE_KINDS = {"code", "text"}
LOG_NAME = "edits.jsonl"

CHANGE_WORDS = re.compile(
    r"\b(change|fix|add|rewrite|refactor|rename|update|remove|delete|modify|edit|convert|"
    r"improve|clean|optimi[sz]e|replace|insert|implement|correct|format|comment|document)\b", re.I)
PLACEHOLDER = re.compile(
    r"(\.\.\.\s*(rest|remaining|existing|other|same)|"
    r"(rest|remainder) of (the )?(code|file|function|class)|"
    r"(code|everything) (above|below) (is )?unchanged|"
    r"\(unchanged\)|existing code (here|stays)|same as before|no changes? (here|below))", re.I)


# ---------------------------------------------------------------- hashing / reading
def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    with open(path, "rb") as f:
        return sha_bytes(f.read())


def read_current(path):
    """Return (text, encoding, newline) so a write can keep the same format."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        enc = "utf-8-sig"
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        enc = "utf-16"
    else:
        enc = "utf-8"
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            enc = "cp1252"
    text = raw.decode(enc, errors="replace")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), enc, newline


# ---------------------------------------------------------------- telling the model
def instructions(files):
    """A system message for chats that have editable files.
    files: list of dicts with name and path."""
    names = "\n".join(f"- {f['name']}  ({f['path']})" for f in files)
    first = files[0]["name"]
    return (
        "You are a helpful assistant running on the user's laptop. The user has attached these "
        f"files, and the app can apply changes to them:\n{names}\n\n"
        "Only when the user asks you to change, fix, rewrite or add to one of these files:\n"
        "1. Say briefly what you changed and why.\n"
        f"2. Then give the COMPLETE updated file in ONE code block whose opening line is exactly "
        f"```edit:{first} (use the right file name).\n"
        "3. Include every line of the file, also the unchanged ones. Never write things like "
        "'... rest of the code unchanged'.\n"
        "The app shows the user a before/after comparison and they decide whether to apply it.\n"
        "If the user only asks a question about a file, answer normally and do NOT use edit: blocks."
    )


# ---------------------------------------------------------------- reading the reply
_EDIT_BLOCK = re.compile(r"```[\w+#.-]*[ \t]*edit:[ \t]*([^\n`]+?)[ \t]*\n(.*?)\n?```", re.S)
_ANY_BLOCK = re.compile(r"```[^\n]*\n(.*?)\n?```", re.S)


def extract(reply, files, user_message=""):
    """Find proposed new versions of `files` in a model reply.
    Returns a list of dicts: name, path, new_text, base_sha."""
    by_name = {f["name"].lower(): f for f in files}
    found = {}
    for m in _EDIT_BLOCK.finditer(reply):
        name = os.path.basename(m.group(1).strip().strip("'\"`"))
        f = by_name.get(name.lower())
        if f is None and len(files) == 1:
            f = files[0]  # model mangled the name but there's only one file
        if f is not None:
            found[f["path"]] = _proposal(f, m.group(2))

    if not found and len(files) == 1 and CHANGE_WORDS.search(user_message or ""):
        # Fallback: the model ignored the edit: format but clearly rewrote the file.
        f = files[0]
        try:
            old, _, _ = read_current(f["path"])
        except OSError:
            old = None
        if old:
            best, best_ratio = None, 0.0
            for m in _ANY_BLOCK.finditer(reply):
                block = m.group(1)
                if len(block.splitlines()) < max(3, 0.5 * len(old.splitlines())):
                    continue
                ratio = difflib.SequenceMatcher(None, old, block, autojunk=False).ratio()
                if ratio > best_ratio:
                    best, best_ratio = block, ratio
            if best is not None and 0.6 <= best_ratio < 0.9999:
                found[f["path"]] = _proposal(f, best)
    return list(found.values())


def _proposal(f, new_text):
    return {"name": f["name"], "path": f["path"], "new_text": new_text.rstrip("\n") + "\n",
            "base_sha": f.get("sha"), "status": "pending"}


# ---------------------------------------------------------------- checks
def checks(path, old, new, base_sha=None):
    """Warnings to show before Apply: list of (level, message), level is
    'block' (no Apply at all), 'error' (Apply becomes 'Apply anyway') or 'warn'."""
    out = []
    # A privacy placeholder the model wrote into NEW text is not the user's real value, so
    # applying would corrupt the file. Only placeholders absent from the old text count: one
    # already in the file and still there is the user's own text. Level "block" = no Apply.
    old_ph = {m.upper() for m in privacy.PLACEHOLDER_RE.findall(old)}
    for ph in dict.fromkeys(m.upper() for m in privacy.PLACEHOLDER_RE.findall(new)):
        if ph not in old_ph:
            out.append(("block", f"Not applied: the edit contains {ph}, a placeholder from the "
                                 "privacy filter, not your real value. Ask again or edit by hand."))
            break
    if old == new:
        out.append(("warn", "The proposed version is identical to the current file."))
    if base_sha and os.path.exists(path):
        try:
            if sha_file(path) != base_sha:
                out.append(("error", "The file changed on disk after you attached it. This "
                                     "comparison is against the current file: lines shown as "
                                     "removed may be your newer edits."))
        except OSError:
            log.exception("couldn't read the file to compare it with the attached copy")
    old_lines, new_lines = len(old.splitlines()), len(new.splitlines())
    if PLACEHOLDER.search(new) and not PLACEHOLDER.search(old):
        out.append(("error", "The new version contains a placeholder like '... rest of the code'. "
                             "Applying it would delete those parts of your file."))
    elif old_lines >= 20 and new_lines < 0.5 * old_lines:
        out.append(("warn", f"The new version is much shorter ({new_lines} lines vs {old_lines}). "
                            "Check that nothing was left out."))
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ".pyw"):
        try:
            ast.parse(new)
        except SyntaxError as e:
            out.append(("error", f"The new Python code has a syntax error on line {e.lineno}: {e.msg}."))
    elif ext == ".json":
        try:
            json.loads(new)
        except ValueError as e:
            out.append(("error", f"The new JSON is not valid: {e}."))
    return out


# ---------------------------------------------------------------- diff
def diff_rows(old, new, context=3):
    """Rows for a side-by-side view:
    (tag, left_no, left_text, right_no, right_text)
    tag: equal | delete | insert | replace | skip (a run of hidden unchanged lines;
    left_text then says how many)."""
    a, b = old.splitlines(), new.splitlines()
    rows = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = sm.get_opcodes()
    for idx, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            n = i2 - i1
            keep_head = 0 if idx == 0 else context
            keep_tail = 0 if idx == len(ops) - 1 else context
            if len(ops) == 1:  # no changes at all: show it all
                keep_head, keep_tail = n, 0
            if n > keep_head + keep_tail + 2:
                for k in range(keep_head):
                    rows.append(("equal", i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k]))
                hidden = n - keep_head - keep_tail
                rows.append(("skip", None, f"{hidden} unchanged line{'s' if hidden != 1 else ''}", None, ""))
                for k in range(n - keep_tail, n):
                    rows.append(("equal", i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k]))
            else:
                for k in range(n):
                    rows.append(("equal", i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k]))
        elif tag == "delete":
            for i in range(i1, i2):
                rows.append(("delete", i + 1, a[i], None, ""))
        elif tag == "insert":
            for j in range(j1, j2):
                rows.append(("insert", None, "", j + 1, b[j]))
        else:  # replace: pair lines up, pad the shorter side
            for k in range(max(i2 - i1, j2 - j1)):
                li, rj = i1 + k, j1 + k
                rows.append(("replace",
                             li + 1 if li < i2 else None, a[li] if li < i2 else "",
                             rj + 1 if rj < j2 else None, b[rj] if rj < j2 else ""))
    return rows


def stats(old, new):
    added = removed = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


# ---------------------------------------------------------------- apply / undo
class EditError(Exception):
    pass


def _log_path():
    return os.path.join(config.BACKUP_DIR, LOG_NAME)


def apply(path, new_text, conv_id=None):
    """Back up the current file, then write `new_text` in the file's own
    encoding and line endings. Returns the log record (used by undo)."""
    if not os.path.isfile(path):
        raise EditError(f"The file no longer exists: {path}")
    _, enc, newline = read_current(path)
    stamp = _dt.datetime.now()
    day_dir = os.path.join(config.BACKUP_DIR, stamp.strftime("%Y-%m-%d"))
    os.makedirs(day_dir, exist_ok=True)
    backup = os.path.join(day_dir, f"{stamp.strftime('%H%M%S')}_{os.path.basename(path)}")
    n = 1
    while os.path.exists(backup):
        n += 1
        backup = os.path.join(day_dir, f"{stamp.strftime('%H%M%S')}-{n}_{os.path.basename(path)}")
    shutil.copy2(path, backup)

    data = new_text.replace("\r\n", "\n").replace("\n", newline).encode(enc)
    tmp = path + ".hybridai-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)  # atomic: the file is never half-written

    record = {"id": f"{int(time.time() * 1000)}", "path": path, "backup": backup,
              "applied_sha": sha_bytes(data), "time": stamp.isoformat(timespec="seconds"),
              "conv_id": conv_id, "undone": False}
    _append_log(record)
    return record


def undo(record, force=False):
    """Put the backup back. Refuses if the file changed after the edit was
    applied (unless force=True), so a later change isn't lost silently."""
    path, backup = record["path"], record["backup"]
    if not os.path.exists(backup):
        raise EditError("The backup for this edit is missing.")
    if os.path.exists(path) and not force and sha_file(path) != record["applied_sha"]:
        raise EditError("changed-since")
    shutil.copy2(backup, path)
    record["undone"] = True
    _rewrite_log(lambda r: {**r, "undone": True} if r["id"] == record["id"] else r)
    return record


def recent_edits(limit=10):
    path = _log_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError as _exc:
                log.warning("skipped a damaged line in the edits log: %s", _exc)
    return list(reversed(out))[:limit]


def _append_log(record):
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _rewrite_log(fn):
    path = _log_path()
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(fn(r), ensure_ascii=False) + "\n")
