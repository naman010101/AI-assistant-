"""Tests for tools.py and phone.py helpers (Phase 6)."""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=lambda **k: object()))

import tools  # noqa: E402

PAGE = '''<div><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2F&rut=x">Python &amp; docs</a>
<a class="result__snippet" href="#">The <b>official</b> docs</a></div>
<div><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
<div><a class="result__a" href="https://realpython.com/">Real Python</a></div>'''


def test_format_web():
    out = tools.format_web("python", PAGE)
    assert "[Python & docs](https://docs.python.org/3/)" in out and "The official docs" in out
    assert "Real Python" in out and "y.js" not in out
    assert "No web results" in tools.format_web("x", "<html></html>")


def test_unknown_command_lists_help():
    out = tools.run("/nope", None)
    assert out.startswith("Unknown command `/nope`") and "/find" in out


def test_titles_and_labels():
    import phone
    assert phone.new_title("  a   b ") == "a b" and len(phone.new_title("x" * 80)) == 34
    assert phone.label("cloud-groq (fallback)") == "Groq · GPT-OSS 120B (fallback)"
    assert phone.label("local-general (Answered locally: cloud unavailable)") == "Llama 3.1 8B (Answered locally: cloud unavailable)"
    assert phone.place("cloud-gemini") == "cloud" and phone.place("laptop") == "laptop"


# ---------- step 1.3: safe defaults, expiring links, token reset ----------
import http.client  # noqa: E402
import json  # noqa: E402
import urllib.parse  # noqa: E402

import pytest  # noqa: E402


class FakeGate:
    def check(self, path, action):
        return True, ""


class FakeHost:
    def __init__(self, public_url=""):
        self.settings = {"phone": {"public_url": public_url}}
        self.gate = FakeGate()

    def notify(self, event):
        pass

    def desktop_busy(self):
        return False


@pytest.fixture
def srv(tmp_path, monkeypatch):
    import phone
    monkeypatch.setattr(phone, "TOKEN_FILE", str(tmp_path / "phone_token.txt"))
    monkeypatch.setattr(phone, "SHOTS_DIR", str(tmp_path / "shots"))
    monkeypatch.setattr(phone, "UPLOAD_DIR", str(tmp_path / "uploads"))
    s = phone.PhoneServer(FakeHost(), port=0, lan=False, db_path=str(tmp_path / "chats.db"))
    assert s.start()
    s.real_port = s.httpd.server_address[1]
    yield s
    s.stop()


def get(srv, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.real_port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, body


def make_file(tmp_path):
    p = tmp_path / "hello.txt"
    p.write_text("hi")
    return str(p)


def test_fresh_settings_phone_off_and_lan_off(tmp_path):
    import settings
    s = settings.Settings(str(tmp_path / "settings.json"))
    assert s.get("phone")["enabled"] is False and s.get("phone")["lan"] is False


@pytest.mark.parametrize("enabled,lan_after", [(False, False), (True, True)])
def test_upgrade_v2_turns_lan_off_only_when_phone_off(tmp_path, enabled, lan_after):
    import settings
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"settings_version": 1, "phone": {"enabled": enabled, "lan": True},
                             "access": {"folders": []}}))
    s = settings.Settings(str(p))
    assert s.get("phone")["lan"] is lan_after and s.get("settings_version") == 2
    assert (tmp_path / "settings.v1.bak").exists()
    assert json.loads((tmp_path / "settings.v1.bak").read_text())["phone"]["lan"] is True
    assert json.loads(p.read_text())["phone"]["lan"] is lan_after


def test_file_link_works_then_expires(srv, tmp_path, monkeypatch):
    import phone
    link = srv.file_link(make_file(tmp_path))
    assert get(srv, link)[0] == 200
    real = phone.time.time()
    monkeypatch.setattr(phone.time, "time", lambda: real + phone.LINK_TTL + 1)
    status, body = get(srv, link)
    assert status == 403 and b"expired" in body


def test_old_format_link_rejected(srv, tmp_path):
    import hashlib
    import hmac
    path = make_file(tmp_path)
    old = hmac.new(srv.token.encode(), f"f:{path}".encode(), hashlib.sha256).hexdigest()[:32]
    status, body = get(srv, "/api/file?" + urllib.parse.urlencode({"p": path, "s": old}))
    assert status == 403 and b"not valid" in body


def test_tampered_expiry_or_path_rejected(srv, tmp_path):
    path = make_file(tmp_path)
    exp, sig = srv.make_sig(path, "f", now=1000)
    assert srv.check_link(path, "f", exp, sig, now=1000) == "ok"
    assert srv.check_link(path, "f", exp + 1, sig, now=1000) == "invalid"
    assert srv.check_link(path + "x", "f", exp, sig, now=1000) == "invalid"
    assert srv.check_link(path, "f", "12abc", sig, now=1000) == "invalid"
    assert srv.check_link(path, "f", "", sig, now=1000) == "invalid"
    assert srv.check_link(path, "a", exp, sig, now=1000) == "invalid"  # wrong kind


def test_ask_and_upload_ids_expire(srv, tmp_path):
    import phone
    path = make_file(tmp_path)
    for kind in ("a", "u"):
        exp, sig = srv.make_sig(path, kind, now=5000)
        assert exp == 5000 + phone.LINK_TTL
        assert srv.check_link(path, kind, exp, sig, now=exp) == "ok"
        assert srv.check_link(path, kind, exp, sig, now=exp + 1) == "expired"
    assert "e=" in srv.ask_link(path)


def test_reset_token_invalidates_running_server(srv, tmp_path):
    import phone
    auth = {"Authorization": f"Bearer {srv.token}"}
    link = srv.file_link(make_file(tmp_path))
    assert get(srv, "/api/ping", auth)[0] == 200 and get(srv, link)[0] == 200
    before = open(phone.TOKEN_FILE).read()
    srv.reset_token()
    assert get(srv, "/api/ping", auth)[0] == 401
    assert get(srv, link)[0] == 403
    assert open(phone.TOKEN_FILE).read() != before


def test_lan_off_binds_localhost_and_links(srv):
    assert srv.httpd.server_address[0] == "127.0.0.1"
    assert srv.links() == []
    srv.host.settings["phone"]["public_url"] = "https://x.ts.net/"
    assert srv.links() == [f"https://x.ts.net/#token={srv.token}"]


def test_pair_refuses_proxied_request(srv):
    assert get(srv, "/pair", {"X-Forwarded-For": "1.2.3.4"})[0] == 403
    assert get(srv, "/pair")[0] == 200
