"""Tests for reminders.py (Phase 7)."""
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import reminders as R  # noqa: E402

NOW = dt.datetime(2026, 11, 2, 14, 30)  # a Monday afternoon


def p(text):
    r = R.parse(text, NOW)
    return (r[0].strftime("%a %d %b %H:%M"), r[1], r[2]) if r else None


def test_parse_common_forms():
    assert p("remind me DSA assignment due Friday 5pm") == ("Fri 06 Nov 17:00", "DSA assignment", "none")
    assert p("remind me to call mom tomorrow") == ("Tue 03 Nov 09:00", "call mom", "none")
    assert p("remind me in 2 hours to stretch") == ("Mon 02 Nov 16:30", "stretch", "none")
    assert p("remind me in half an hour to check the oven") == ("Mon 02 Nov 15:00", "check the oven", "none")
    assert p("remind me at 6 to submit the form") == ("Mon 02 Nov 18:00", "submit the form", "none")
    assert p("set a reminder for 5 nov 4pm lab viva") == ("Thu 05 Nov 16:00", "lab viva", "none")
    assert p("remind me on 12/11 at 9:30 internship call") == ("Thu 12 Nov 09:30", "internship call", "none")
    assert p("remind me next monday to pay fees") == ("Mon 09 Nov 09:00", "pay fees", "none")
    assert p("remind me tonight to pack") == ("Mon 02 Nov 20:00", "pack", "none")
    assert p("remind me today at 1pm lunch") == ("Mon 02 Nov 13:00", "lunch", "none")  # explicit past "today" kept


def test_parse_repeats():
    assert p("remind me every monday 10am team meeting") == ("Mon 09 Nov 10:00", "team meeting", "weekly")
    assert p("remind me every day at 8 to take vitamins") == ("Tue 03 Nov 08:00", "take vitamins", "daily")
    assert p("remind me to drink water every weekday") == ("Tue 03 Nov 09:00", "drink water", "weekdays")


def test_no_time_and_model_fallback():
    assert p("remind me to buy 2 notebooks") is None
    assert R.is_reminder_request("Remind me to x") and R.is_reminder_request("set a reminder for x")
    assert not R.is_reminder_request("what is a reminder?")
    due, what, rep = R.parse_model_reply('{"what": "buy notebooks", "due": "2026-11-03 09:00", "repeat": "none"}')
    assert due == dt.datetime(2026, 11, 3, 9, 0) and what == "buy notebooks" and rep == "none"
    assert R.parse_model_reply("not json") is None


def test_describe():
    assert R.describe(dt.datetime(2026, 11, 2, 17, 0), now=NOW) == "today 17:00"
    assert R.describe(dt.datetime(2026, 11, 3, 9, 0), now=NOW) == "tomorrow 09:00"
    assert R.describe(dt.datetime(2026, 11, 20, 9, 0), now=NOW) == "Fri 20 Nov, 09:00"
    assert R.describe(dt.datetime(2026, 11, 9, 10, 0), "weekly", now=NOW) == "every Monday 10:00"


def test_store_due_fired_repeat(tmp_path):
    rs = R.ReminderStore(str(tmp_path / "r.db"))
    a = rs.add("one-off", NOW - dt.timedelta(minutes=1))
    b = rs.add("weekly thing", NOW - dt.timedelta(minutes=5), "weekly")
    rs.add("later", NOW + dt.timedelta(days=2))
    due = {r["text"]: r for r in rs.due(NOW)}
    assert set(due) == {"one-off", "weekly thing"}
    for r in due.values():
        rs.fired(r, NOW)
    assert rs.due(NOW) == []
    weekly = rs.get(b)
    assert weekly["due_dt"] > NOW and weekly["due_dt"].weekday() == (NOW - dt.timedelta(minutes=5)).weekday()
    rs.snooze(a, 10)
    assert rs.get(a)["fired"] is None
    rs.complete(a)
    assert all(r["id"] != a for r in rs.upcoming())


def test_timetable_and_brief(tmp_path):
    rs = R.ReminderStore(str(tmp_path / "r.db"))
    rows = R.parse_timetable(json.dumps({"classes": [
        {"day": "Monday", "start": "9:00", "end": "10:00", "title": "DBMS", "place": "AB1-204"},
        {"day": "Mon", "start": "2pm", "title": "DSA lab"},
        {"day": "Funday", "start": "9", "title": "bad"}, {"day": "Tuesday", "start": "x", "title": "bad"}]}))
    assert [(r["day"], r["start"], r["title"]) for r in rows] == [(0, "09:00", "DBMS"), (0, "14:00", "DSA lab")]
    rs.replace_timetable(rows)
    rs.add("DSA assignment", NOW.replace(hour=17, minute=0))
    rs.add("old thing", NOW - dt.timedelta(days=1))
    rs.add("viva", NOW + dt.timedelta(days=3))

    class Mem:
        def summaries(self, limit=None):
            return [{"title": "Fixing pip", "open_items": ["test the vision model"]}]

    b = R.brief(rs, NOW, Mem(), [("hybrid-ai", 3), ("other", 0)])
    assert "09:00–10:00 DBMS (AB1-204)" in b and "14:00 DSA lab" in b
    assert "17:00 DSA assignment" in b and "overdue: old thing" in b and "viva" in b
    assert "test the vision model" in b and "hybrid-ai: 3 files changed" in b and "other" not in b
