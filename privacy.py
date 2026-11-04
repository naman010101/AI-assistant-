"""Cloud privacy filter (step 1.4).

Before a request goes to a cloud model, emails, keys, tokens, private keys,
secret values and file paths are replaced by placeholders such as [EMAIL_1].
The reply is un-masked locally while it streams, so you (and edit blocks)
see the real values and the placeholders never leave this laptop's history.

    payload, masker = mask_messages(messages)      # a masked deep copy
    unmasker = Unmasker(masker)                    # for the streamed reply
    shown = unmasker.feed(token) ... unmasker.flush()

Only stdlib `re`. Only fixed provider prefixes, PEM blocks, JWT, Bearer,
user:pass@ and "name = value" are matched; there is no entropy rule, so
hashes, UUIDs and base64 are left alone. Raw values are never logged.
"""
import copy
import fnmatch
import os
import re

from access import SECRET_NAMES

KINDS = {"email": "EMAIL", "api_key": "API_KEY", "token": "TOKEN",
         "private_key": "PRIVATE_KEY", "secret": "SECRET", "path": "PATH"}
_LABELS = {"email": "email", "api_key": "API key", "token": "token",
           "private_key": "private key", "secret": "secret value", "path": "path"}

_PH_RE = re.compile(r"\[(?:EMAIL|API_KEY|TOKEN|PRIVATE_KEY|SECRET|PATH)_\d+\]", re.I)
_PH_ONLY = re.compile(r"\[[A-Z_]+_\d+\]")
_PH_PREFIX = re.compile(r"\[[A-Za-z_0-9]*")
_MAX_HOLD = 25  # a "[" plus up to 24 characters

# ---------- content patterns ----------
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_URL_PASS = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@'\"]+:)([^\s/@'\"]+)(@)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
_API_KEY = re.compile(
    r"\bsk-[A-Za-z0-9_-]{20,}"                      # sk-, sk-proj-, sk-ant-
    r"|\bAIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"
    r"|\bgsk_[A-Za-z0-9]{20,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\b(?:sk|rk)_live_[A-Za-z0-9]{16,}"
    r"|\bhf_[A-Za-z0-9]{30,}")
_BEARER = re.compile(r"(\bBearer\s+)([A-Za-z0-9._~+/=-]{16,})", re.I)
_SECRET = re.compile(
    r"""(?P<name>\b\w*(?:api[_-]?key|secret|token|password|passwd|pwd|access_key)\w*)"""
    r"""(?P<sep>["']?\s*[:=]\s*)"""
    r"""(?:(?P<q>["'])(?P<qv>[^\n]*?)(?P=q)|(?P<uv>[^\s,;"'\)\]}]+))""", re.I)
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# ---------- paths ----------
_SEP = r"[\\/]"
_PATH_START = (r"[A-Za-z]:[\\/]"                           # C:\  C:/
               r"|\\\\[\w.$-]+[\\/]"                       # \\server\share
               r"|%[A-Za-z_][A-Za-z0-9_]*%[\\/]"           # %APPDATA%\
               r"|~[\\/]"
               r"|file:///[A-Za-z]:/"
               r"|/[a-z]/Users/[^/\s\"']+"                 # /c/Users/<name>
               r"|/home/[^/\s\"']+")
_USER_ROOT = re.compile(r"(?i)^(?:file:///)?(?:[a-z]:|/[a-z])?[\\/]?(?:users|home)[\\/][^\\/]+$")
_TAIL = r"[^\s\"'<>|*?]*"
_TRAIL_PUNCT = ".,;:!)]}"


def _home_alt():
    """Regex for the user's home folder (either slash style, any case), or None."""
    home = os.path.expanduser("~")
    if len(home) <= 3 or home == "~":
        return None
    parts = re.split(r"[\\/]+", home)
    return r"[\\/]+".join(re.escape(p) for p in parts) + r"(?![\w-])"


class Masker:
    """Remembers what was replaced: numbering, the report and un-masking."""

    def __init__(self):
        self._by_key = {}      # (kind, value) -> placeholder
        self._by_ph = {}       # PLACEHOLDER (upper case) -> value
        self._count = {}       # kind -> how many placeholders so far
        self._items = []       # [{"ph", "kind", "preview"}]
        self.images = 0
        self._home = _home_alt()
        self._home_re = re.compile("^(?:" + self._home + ")$", re.I) if self._home else None

    def _home_equals(self, path):
        return bool(self._home_re and self._home_re.match(path))

    def add(self, kind, value):
        key = (kind, value)
        ph = self._by_key.get(key)
        if ph is None:
            n = self._count.get(kind, 0) + 1
            self._count[kind] = n
            ph = f"[{KINDS[kind]}_{n}]"
            self._by_key[key] = ph
            self._by_ph[ph.upper()] = value
            self._items.append({"ph": ph, "kind": kind, "preview": _preview(kind, value)})
        return ph

    @property
    def has_items(self):
        return bool(self._items)

    def report(self):
        """{} when nothing was masked and no image was sent as-is."""
        if not self._items and not self.images:
            return {}
        counts = dict(self._count)
        rep = {"counts": counts, "items": [dict(i) for i in self._items]}
        if self.images:
            rep["images"] = self.images
        return rep

    def unmask(self, s):
        if not s or not self._by_ph:
            return s
        return _PH_RE.sub(lambda m: self._by_ph.get(m.group(0).upper(), m.group(0)), s)


def _preview(kind, value):
    if kind in ("api_key", "token", "secret"):
        if len(value) >= 12:
            return f"{value[:3]}…{value[-4:]}"
        return f"{value[:1]}…"
    if kind == "private_key":
        return "private key block"
    return value  # emails and paths: local UI only


def describe(report):
    """'1 email, 2 paths' (+ ', 1 image sent as-is'). Counts only, safe to log."""
    counts = (report or {}).get("counts") or {}
    parts = [f"{n} {_LABELS.get(k, k)}{'' if n == 1 else 's'}" for k, n in counts.items() if n]
    imgs = (report or {}).get("images", 0)
    if imgs:
        parts.append(f"{imgs} image{'' if imgs == 1 else 's'} sent as-is")
    return ", ".join(parts)


# ---------- masking ----------
def _secret_value_ok(v):
    if len(v) < 8 or v.isdigit() or "(" in v or _PH_ONLY.fullmatch(v):
        return False
    if "." in v and _DOTTED.fullmatch(v):
        return False
    return True


def _mask_path(full, masker):
    """Replace the folder part, keep the file name. Secrets files are masked whole."""
    sep_at = max(full.rfind("\\"), full.rfind("/"))
    name = full[sep_at + 1:] if sep_at >= 0 else full
    if (sep_at < 0 or not name or _USER_ROOT.match(full) or masker._home_equals(full)
            or any(fnmatch.fnmatch(name.lower(), p.lower()) for p in SECRET_NAMES)):
        whole = full.rstrip("\\/") or full
        return masker.add("path", whole) + full[len(whole):]
    folder = full[:sep_at]
    if not folder:
        return full
    return masker.add("path", folder) + full[sep_at:]


def _path_sub(m, masker):
    full = m.groupdict().get("p") or m.group(0)
    stripped = full.rstrip(_TRAIL_PUNCT)
    if not stripped:
        return m.group(0)
    return _mask_path(stripped, masker) + full[len(stripped):]


def _path_regexes(masker):
    starts = _PATH_START
    if masker._home:
        starts = masker._home + "|" + starts
    quoted = re.compile(r"(?P<q>[\"'])(?P<p>(?:" + starts + r")[^\n]*?)(?P=q)", re.I)
    plain = re.compile(r"(?<![\w/\\])(?:" + starts + r")" + _TAIL, re.I)
    return quoted, plain


def mask_text(s, masker):
    if not s or not isinstance(s, str):
        return s
    s = _PEM.sub(lambda m: masker.add("private_key", m.group(0)), s)
    s = _URL_PASS.sub(lambda m: m.group(1) + (m.group(2) if _PH_ONLY.fullmatch(m.group(2))
                                              else masker.add("token", m.group(2))) + m.group(3), s)
    s = _JWT.sub(lambda m: masker.add("token", m.group(0)), s)
    s = _API_KEY.sub(lambda m: masker.add("api_key", m.group(0)), s)
    s = _BEARER.sub(lambda m: m.group(1) + masker.add("token", m.group(2)), s)

    def secret(m):
        v = m.group("qv") if m.group("q") else m.group("uv")
        if not _secret_value_ok(v):
            return m.group(0)
        ph = masker.add("secret", v)
        if m.group("q"):
            return f'{m.group("name")}{m.group("sep")}{m.group("q")}{ph}{m.group("q")}'
        return f'{m.group("name")}{m.group("sep")}{ph}'

    s = _SECRET.sub(secret, s)
    s = _EMAIL.sub(lambda m: masker.add("email", m.group(0)), s)

    quoted, plain = _path_regexes(masker)

    def quoted_sub(m):
        q = m.group("q")
        return q + _path_sub(m, masker) + q

    s = quoted.sub(quoted_sub, s)
    s = plain.sub(lambda m: _path_sub(m, masker), s)
    return s


def mask_messages(msgs):
    """A masked deep copy of `msgs` and the Masker that holds the mapping.
    Only text is masked; image parts are copied untouched and counted."""
    masker = Masker()
    out = copy.deepcopy(msgs)
    for m in out:
        c = m.get("content")
        if isinstance(c, str):
            m["content"] = mask_text(c, masker)
        elif isinstance(c, list):
            for part in c:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    part["text"] = mask_text(part.get("text", ""), masker)
                elif part.get("type") == "image_url":
                    masker.images += 1
    return out, masker


class Unmasker:
    """Un-masks a streamed reply. A trailing '[' plus up to 24 characters that
    could still become a placeholder is held back until the next chunk."""

    def __init__(self, masker):
        self.masker = masker
        self._buf = ""

    def feed(self, tok):
        self._buf += tok
        i = self._buf.rfind("[")
        if i >= 0:
            tail = self._buf[i:]
            if len(tail) <= _MAX_HOLD and _PH_PREFIX.fullmatch(tail):
                out, self._buf = self._buf[:i], tail
                return self.masker.unmask(out)
        out, self._buf = self._buf, ""
        return self.masker.unmask(out)

    def flush(self):
        out, self._buf = self._buf, ""
        return self.masker.unmask(out)
