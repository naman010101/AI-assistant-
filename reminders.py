"""Reminders, your weekly timetable, and the daily brief.

    rs = ReminderStore(path)
    when, what, repeat = parse("remind me DSA assignment due Friday 5pm")   # no model needed
    rs.add(what, when, repeat)
    for r in rs.due(now): ...notify...; rs.fired(r)
    text = brief(rs, mem, projects, now)

Times are local time. Plain-words parsing handles the common forms
("tomorrow 9am", "friday at 17:30", "in 2 hours", "on 5 nov at 4pm",
"every monday 10am", "every day at 8"); anything else can be passed to the
local model with model_prompt() / parse_model_reply().
"""
import datetime as dt
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid

log = logging.getLogger("hybridai")

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ABBR = {d[:3]: i for i, d in enumerate(DAYS)} | {"tues": 1, "thur": 3, "thurs": 3}
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                          "nov", "dec"])}
REPEATS = ("none", "daily", "weekdays", "weekly")
DEFAULT_HOUR = 9  # "remind me tomorrow to …" with no time

SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id       TEXT PRIMARY KEY,
    text     TEXT NOT NULL,
    due      REAL NOT NULL,
    repeat   TEXT NOT NULL DEFAULT 'none',
    done     INTEGER NOT NULL DEFAULT 0,
    fired    REAL,                 -- when the notification was last shown
    created  REAL NOT NULL,
    source   TEXT                  -- desktop | phone | chat
);
CREATE TABLE IF NOT EXISTS classes (
    id     TEXT PRIMARY KEY,
    day    INTEGER NOT NULL,       -- 0 = Monday
    start  TEXT NOT NULL,          -- "09:00"
    end    TEXT,
    title  TEXT NOT NULL,
    place  TEXT
);
"""


# ================================================================ parsing
_TIME = re.compile(r"\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?=\b|$)", re.I)
_NAMED_TIME = {"noon": (12, 0), "midday": (12, 0), "midnight": (0, 0), "morning": (9, 0),
               "afternoon": (14, 0), "evening": (18, 0), "tonight": (20, 0), "night": (21, 0)}


def _clean_what(text):
    t = re.sub(r"^\s*(please\s+)?(can you\s+)?(set\s+(a\s+)?reminder\s*(to|for|:)?|remind\s+me\s*(to|about|that|of)?)\s*",
               "", text, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip(" ,.-:") or text.strip()


def parse(text, now=None):
    """(due datetime, what, repeat) or None if no time could be found."""
    now = now or dt.datetime.now()
    s = " " + text.strip() + " "
    low = s.lower()
    used = []           # (start, end) spans that described the time, removed from `what`
    day = None
    repeat = "none"

    m = re.search(r"\bin\s+(\d+|an?|half\s+an?)\s*(min(?:ute)?s?|hours?|hrs?|days?|weeks?)\b", low)
    if m:
        n = {"a": 1, "an": 1}.get(m.group(1), None)
        if m.group(1).startswith("half"):
            n = 0.5
        n = float(m.group(1)) if n is None else n
        unit = m.group(2)
        delta = (dt.timedelta(minutes=n) if unit.startswith("min") else dt.timedelta(hours=n) if unit.startswith(("hour", "hr"))
                 else dt.timedelta(days=n) if unit.startswith("day") else dt.timedelta(weeks=n))
        due = (now + delta).replace(second=0, microsecond=0)
        what = _clean_what(s[:m.start()] + s[m.end():])
        return due, what, "none"

    m = re.search(r"\bevery\s+(day|weekday|morning|evening|night|(?:mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*)\b", low)
    if m:
        word = m.group(1)
        used.append(m.span())
        if word in ("day", "morning", "evening", "night"):
            repeat = "daily"
        elif word == "weekday":
            repeat = "weekdays"
        else:
            repeat = "weekly"
            day = DAY_ABBR.get(word[:4]) if word[:4] in DAY_ABBR else DAY_ABBR.get(word[:3])

    date = None
    if day is None:
        m = re.search(r"\b(today|tonight|tomorrow|tmrw|tmr|day after tomorrow)\b", low)
        if m:
            used.append(m.span())
            w = m.group(1)
            date = now.date() + dt.timedelta(days=2 if w.startswith("day after") else 1 if w.startswith("t") and w != "today"
                                             and w != "tonight" else 0)
        else:
            m = re.search(r"\b(?:on\s+|this\s+|next\s+|by\s+)?(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\b", low)
            if m:
                used.append(m.span())
                d = DAY_ABBR.get(m.group(1))
                ahead = (d - now.weekday()) % 7
                if "next" in m.group(0) and ahead == 0:
                    ahead = 7
                date = now.date() + dt.timedelta(days=ahead)
            else:
                m = re.search(r"\b(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
                              r"|\b(?:on\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
                if m:
                    used.append(m.span())
                    dd = int(m.group(1) or m.group(4))
                    mm = MONTHS[(m.group(2) or m.group(3))[:3]]
                    try:
                        date = dt.date(now.year, mm, dd)
                        if date < now.date():
                            date = dt.date(now.year + 1, mm, dd)
                    except ValueError:
                        return None
                else:
                    m = re.search(r"\b(?:on\s+)?(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", low)  # 5/11 = 5 Nov (day first)
                    if m:
                        used.append(m.span())
                        y = int(m.group(3)) if m.group(3) else now.year
                        y = y + 2000 if y < 100 else y
                        try:
                            date = dt.date(y, int(m.group(2)), int(m.group(1)))
                        except ValueError:
                            return None

    # time of day
    hh = mi = None
    named = re.search(r"\b(noon|midday|midnight|morning|afternoon|evening|tonight|night)\b", low)
    for tm in _TIME.finditer(low):
        if any(a <= tm.start() < b for a, b in used):
            continue
        h, mnt, ap = int(tm.group(1)), tm.group(2), (tm.group(3) or "").replace(".", "")
        ctx_before = low[max(0, tm.start() - 4):tm.start()]
        if not ap and not mnt and "at" not in tm.group(0) and "at " not in ctx_before:
            continue  # a bare number ("2 assignments") isn't a time
        if h > 23 or (mnt and int(mnt) > 59):
            continue
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if not ap and h < 7 and not mnt:
            h += 12  # "at 5" means 5 pm
        hh, mi = h, int(mnt or 0)
        used.append(tm.span())
        break
    if hh is None and named:
        hh, mi = _NAMED_TIME[named.group(1)]
        used.append(named.span())
    if hh is None and date is None and day is None and repeat == "none":
        return None

    if repeat in ("daily", "weekdays") and hh is None:
        hh, mi = DEFAULT_HOUR, 0
    if day is not None:  # every <weekday>
        ahead = (day - now.weekday()) % 7
        date = now.date() + dt.timedelta(days=ahead)
    if date is None:
        date = now.date()
    if hh is None:
        hh, mi = DEFAULT_HOUR, 0
    due = dt.datetime.combine(date, dt.time(hh, mi))
    if due <= now:
        if repeat != "none" or (date == now.date() and not re.search(r"\btoday\b", low)):
            due = _next_occurrence(due, repeat if repeat != "none" else "daily", now)
    what, cut_from = s, len(s) + 1
    for a, b in sorted(set(used), reverse=True):  # remove each time phrase once, skipping overlaps
        b = min(b, cut_from)
        if a < b:
            what = what[:a] + " " + what[b:]
            cut_from = a
    what = re.sub(r"\b(at|on|by|due)\s*$", "", what.strip(), flags=re.I)
    return due, _clean_what(what), repeat


def _next_occurrence(due, repeat, now):
    step = {"daily": 1, "weekly": 7}.get(repeat, 1)
    while due <= now:
        due += dt.timedelta(days=step)
        if repeat == "weekdays":
            while due.weekday() >= 5:
                due += dt.timedelta(days=1)
    return due


def is_reminder_request(text):
    return bool(re.match(r"\s*(please\s+)?(can you\s+)?(remind me|set (a )?reminder)\b", text or "", re.I))


def model_prompt(text, now=None):
    now = now or dt.datetime.now()
    return (f"Now it is {now.strftime('%A %d %B %Y, %H:%M')} (local time). Turn this request into a reminder.\n"
            f'Reply with JSON only: {{"what": short text, "due": "YYYY-MM-DD HH:MM", "repeat": one of '
            f'{list(REPEATS)}}}. If there is no time at all, use 09:00 on the next sensible day.\n\nRequest: {text}')


def parse_model_reply(reply):
    try:
        d = json.loads(re.search(r"\{.*\}", reply, re.S).group(0))
        due = dt.datetime.strptime(str(d["due"]).strip()[:16], "%Y-%m-%d %H:%M")
        rep = d.get("repeat") if d.get("repeat") in REPEATS else "none"
        what = str(d.get("what") or "").strip()
        return (due, what, rep) if what else None
    except Exception:
        return None


def describe(due, repeat="none", now=None):
    """'today 17:00', 'tomorrow 09:00', 'Fri 7 Nov, 17:00', 'every Monday 10:00'."""
    now = now or dt.datetime.now()
    t = due.strftime("%H:%M")
    if repeat == "daily":
        return f"every day {t}"
    if repeat == "weekdays":
        return f"weekdays {t}"
    if repeat == "weekly":
        return f"every {DAYS[due.weekday()].title()} {t}"
    days = (due.date() - now.date()).days
    if days == 0:
        return f"today {t}"
    if days == 1:
        return f"tomorrow {t}"
    if 1 < days < 7:
        return f"{due.strftime('%a')} {t}"
    return f"{due.strftime('%a')} {due.day} {due.strftime('%b')}, {t}"


# ================================================================ store
class ReminderStore:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---------- reminders ----------
    def add(self, text, due, repeat="none", source="desktop"):
        rid = uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute("INSERT INTO reminders (id, text, due, repeat, created, source) VALUES (?, ?, ?, ?, ?, ?)",
                             (rid, text.strip(), due.timestamp(), repeat if repeat in REPEATS else "none",
                              time.time(), source))
        return rid

    def _rows(self, where="", args=()):
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM reminders {where} ORDER BY due", args).fetchall()
        return [dict(r) | {"done": bool(r["done"]), "due_dt": dt.datetime.fromtimestamp(r["due"])} for r in rows]

    def upcoming(self, include_done=False):
        return self._rows("" if include_done else "WHERE done = 0")

    def get(self, rid):
        r = self._rows("WHERE id = ?", (rid,))
        return r[0] if r else None

    def update(self, rid, text=None, due=None, repeat=None):
        r = self.get(rid)
        if not r:
            return
        with self._lock, self._db:
            self._db.execute("UPDATE reminders SET text = ?, due = ?, repeat = ?, fired = NULL, done = 0 WHERE id = ?",
                             (text if text is not None else r["text"],
                              due.timestamp() if due is not None else r["due"],
                              repeat if repeat in REPEATS else r["repeat"], rid))

    def complete(self, rid):
        with self._lock, self._db:
            self._db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))

    def delete(self, rid):
        with self._lock, self._db:
            self._db.execute("DELETE FROM reminders WHERE id = ?", (rid,))

    def snooze(self, rid, minutes=10):
        with self._lock, self._db:
            self._db.execute("UPDATE reminders SET due = ?, fired = NULL WHERE id = ?",
                             (time.time() + minutes * 60, rid))

    def due(self, now=None):
        """Reminders whose time has come and that haven't been shown yet."""
        now = (now or dt.datetime.now()).timestamp()
        return self._rows("WHERE done = 0 AND due <= ? AND fired IS NULL", (now,))

    def fired(self, r, now=None):
        """Mark as shown; repeating reminders move to their next time."""
        now = now or dt.datetime.now()
        with self._lock, self._db:
            if r["repeat"] != "none":
                nxt = _next_occurrence(dt.datetime.fromtimestamp(r["due"]), r["repeat"], now)
                self._db.execute("UPDATE reminders SET due = ?, fired = NULL WHERE id = ?", (nxt.timestamp(), r["id"]))
            else:
                self._db.execute("UPDATE reminders SET fired = ? WHERE id = ?", (now.timestamp(), r["id"]))

    # ---------- timetable ----------
    def classes(self, day=None):
        with self._lock:
            q = "SELECT * FROM classes" + (" WHERE day = ?" if day is not None else "") + " ORDER BY day, start"
            return [dict(r) for r in self._db.execute(q, (day,) if day is not None else ()).fetchall()]

    def add_class(self, day, start, title, end=None, place=None):
        cid = uuid.uuid4().hex
        with self._lock, self._db:
            self._db.execute("INSERT INTO classes (id, day, start, end, title, place) VALUES (?, ?, ?, ?, ?, ?)",
                             (cid, int(day), _hhmm(start), _hhmm(end) if end else None, title.strip(), place))
        return cid

    def delete_class(self, cid):
        with self._lock, self._db:
            self._db.execute("DELETE FROM classes WHERE id = ?", (cid,))

    def replace_timetable(self, rows):
        with self._lock, self._db:
            self._db.execute("DELETE FROM classes")
            for r in rows:
                self._db.execute("INSERT INTO classes (id, day, start, end, title, place) VALUES (?, ?, ?, ?, ?, ?)",
                                 (uuid.uuid4().hex, int(r["day"]), _hhmm(r["start"]),
                                  _hhmm(r["end"]) if r.get("end") else None, r["title"].strip(), r.get("place")))

    def close(self):
        with self._lock:
            self._db.close()


def _hhmm(s):
    m = re.match(r"\s*(\d{1,2})[:.]?(\d{2})?\s*(am|pm)?", str(s), re.I)
    if not m:
        raise ValueError(f"not a time: {s}")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    ap = (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


# ================================================================ timetable from a photo
TIMETABLE_PROMPT = """This image is a weekly class timetable. List every class as JSON only:
{"classes": [{"day": "Monday", "start": "09:00", "end": "10:00", "title": "DBMS", "place": "AB1-204"}]}
Use 24-hour times. Leave "place" empty if not shown. Skip breaks and empty slots. Do not invent classes."""


def parse_timetable(reply):
    """Rows from the vision model's JSON, validated. Bad rows are skipped."""
    try:
        data = json.loads(re.search(r"\{.*\}|\[.*\]", reply, re.S).group(0))
    except Exception:
        return []
    items = data.get("classes", []) if isinstance(data, dict) else data
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            day = str(it.get("day", "")).strip().lower()[:3]
            d = DAY_ABBR[day] if day in DAY_ABBR else int(it.get("day"))
            title = str(it.get("title") or "").strip()
            if not title or not 0 <= d <= 6:
                continue
            out.append({"day": d, "start": _hhmm(it.get("start")), "end": _hhmm(it["end"]) if it.get("end") else None,
                        "title": title, "place": (str(it.get("place") or "").strip() or None)})
        except Exception:
            continue
    return out


# ================================================================ daily brief
def brief(rs, now=None, mem=None, project_changes=None):
    """The daily brief as Markdown. project_changes: [(project name, n files changed)]."""
    now = now or dt.datetime.now()
    today = now.date()
    parts = [f"**Good {'morning' if now.hour < 12 else 'afternoon' if now.hour < 17 else 'evening'}** — "
             f"{now.strftime('%A %d %B')}"]
    classes = rs.classes(now.weekday())
    if classes:
        parts.append("**Classes today**\n" + "\n".join(
            f"- {c['start']}{('–' + c['end']) if c.get('end') else ''} {c['title']}" + (f" ({c['place']})" if c.get("place") else "")
            for c in classes))
    else:
        parts.append("**Classes today**\n- none")
    items = rs.upcoming()
    overdue = [r for r in items if r["due_dt"].date() < today and r["repeat"] == "none"]
    todays = [r for r in items if r["due_dt"].date() == today]
    soon = [r for r in items if today < r["due_dt"].date() <= today + dt.timedelta(days=7) and r["repeat"] == "none"]
    lines = [f"- ⚠ overdue: {r['text']} ({describe(r['due_dt'], 'none', now)})" for r in overdue]
    lines += [f"- {r['due_dt'].strftime('%H:%M')} {r['text']}" for r in todays]
    parts.append("**Due today**\n" + ("\n".join(lines) if lines else "- nothing"))
    if soon:
        parts.append("**This week**\n" + "\n".join(f"- {describe(r['due_dt'], 'none', now)}: {r['text']}" for r in soon[:8]))
    if mem is not None:
        try:
            opens = []
            for s in mem.summaries(limit=6):
                for item in s.get("open_items", [])[:2]:
                    opens.append(f"- {item} — from “{s['title']}”")
            if opens:
                parts.append("**Left open in recent sessions**\n" + "\n".join(opens[:6]))
        except Exception:
            log.exception("couldn't add open items from recent sessions to the brief")
    if project_changes:
        changed = [f"- {name}: {n} file{'s' if n != 1 else ''} changed since your last review"
                   for name, n in project_changes if n]
        if changed:
            parts.append("**Code to review**\n" + "\n".join(changed))
    return "\n\n".join(parts)
