"""Step 1.4: the cloud privacy filter. No network: the model clients are fakes.
Fake secrets are built at run time so no secret-looking literal sits in the repo."""
import copy
import queue
import sys
import types

import pytest

import privacy

FAKE_KEY = "sk-proj-" + "FAKE" * 8
HOME = "C:\\Users\\Test Person"


@pytest.fixture(autouse=True)
def fake_home(monkeypatch):
    monkeypatch.setenv("USERPROFILE", HOME)
    monkeypatch.setenv("HOME", HOME)


@pytest.fixture
def router(monkeypatch):
    try:
        import openai  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=lambda **k: object()))
    import router
    return router


class FakeClient:
    """Records the messages it was sent and streams back `chunks`."""

    def __init__(self, chunks=(), fail=False):
        self.chunks, self.fail, self.sent = list(chunks), fail, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, model, messages, stream):
        self.sent.append(copy.deepcopy(messages))
        if self.fail:
            raise ConnectionError("offline")
        return _Stream(self.chunks)


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __iter__(self):
        for c in self.chunks:
            delta = types.SimpleNamespace(content=c)
            yield types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta)])

    def close(self):
        pass


def install(router, monkeypatch, cloud, local):
    monkeypatch.setattr(router, "local_client", local)
    monkeypatch.setitem(router.MODELS, "local-general", ("llama", local))
    monkeypatch.setitem(router.MODELS, "cloud-groq", ("groq-model", cloud))


def mask(s):
    m = privacy.Masker()
    return privacy.mask_text(s, m), m


# ---------- 1-3, 8: the router ----------
def test_fake_key_reaches_cloud_masked_and_reply_is_unmasked(router, monkeypatch):
    cloud, local = FakeClient(["use [API_", "KEY_1] ", "now"]), FakeClient()
    install(router, monkeypatch, cloud, local)
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    reports, tokens = [], []
    text, route_name, _, _ = router.stream_chat(
        hist, f"my key is {FAKE_KEY}", model_override="cloud-groq", on_token=tokens.append,
        on_masked=reports.append, system="be brief")
    sent = repr(cloud.sent)
    assert FAKE_KEY not in sent and "[API_KEY_1]" in sent
    assert [r["counts"] for r in reports] == [{"api_key": 1}]
    assert text == f"use {FAKE_KEY} now" and "".join(tokens) == text
    assert hist[-1] == {"role": "assistant", "content": f"use {FAKE_KEY} now"}
    assert hist[-2] == {"role": "user", "content": f"my key is {FAKE_KEY}"}  # history is never masked
    assert local.sent == []


def test_local_model_gets_unmasked_text(router, monkeypatch):
    cloud, local = FakeClient(), FakeClient(["ok"])
    install(router, monkeypatch, cloud, local)
    reports = []
    router.stream_chat([], f"key {FAKE_KEY}", model_override="local-general", on_masked=reports.append)
    assert FAKE_KEY in repr(local.sent) and reports == []


def test_cloud_failure_falls_back_to_local_with_unmasked_text(router, monkeypatch):
    cloud, local = FakeClient(fail=True), FakeClient(["done"])
    install(router, monkeypatch, cloud, local)
    text, route_name, _, _ = router.stream_chat([], f"key {FAKE_KEY}", model_override="cloud-groq")
    assert router.is_fallback(route_name) and text.endswith("done")
    assert FAKE_KEY not in repr(cloud.sent) and FAKE_KEY in repr(local.sent)


def test_mask_cloud_false_sends_raw_text(router, monkeypatch):
    cloud, local = FakeClient(["ok"]), FakeClient()
    install(router, monkeypatch, cloud, local)
    reports = []
    router.stream_chat([], f"key {FAKE_KEY}", model_override="cloud-groq", mask_cloud=False,
                       on_masked=reports.append)
    assert FAKE_KEY in repr(cloud.sent) and reports == []


def test_stop_and_end_flush_held_text(router, monkeypatch):
    cloud, local = FakeClient(["a [API_KEY_1] b [PA"]), FakeClient()
    install(router, monkeypatch, cloud, local)
    tokens = []
    text, _, _, _ = router.stream_chat([], f"key {FAKE_KEY}", model_override="cloud-groq",
                                       on_token=tokens.append)
    assert "".join(tokens) == f"a {FAKE_KEY} b [PA" == text


# ---------- 4: each kind ----------
def test_each_kind_is_caught_and_numbered_stably():
    k = "A" * 30
    cases = {
        "mail me at a.b@example.com": "[EMAIL_1]",
        "sk-" + k: "[API_KEY_1]",
        "sk-ant-" + k: "[API_KEY_1]",
        "AIza" + "B" * 35: "[API_KEY_1]",
        "gsk_" + k: "[API_KEY_1]",
        "ghp_" + k: "[API_KEY_1]",
        "github_pat_" + k: "[API_KEY_1]",
        "AKIA" + "Q" * 16: "[API_KEY_1]",
        "xoxb-" + "1" * 12 + "-abc": "[API_KEY_1]",
        "sk_live_" + k: "[API_KEY_1]",
        "hf_" + k + "AAAA": "[API_KEY_1]",
        "Authorization: Bearer " + k: "Bearer [TOKEN_1]",
        "eyJ" + "a" * 8 + ".eyJ" + "b" * 8 + "." + "c" * 8: "[TOKEN_1]",
        "postgres://admin:" + "pw" * 5 + "@db.local/x": "admin:[TOKEN_1]@db",
        '-----BEGIN RSA PRIVATE KEY-----\nMIIabc\ndef\n-----END RSA PRIVATE KEY-----': "[PRIVATE_KEY_1]",
        'password = "hunter2hunter2"': 'password = "[SECRET_1]"',
        "GROQ_API_KEY=" + "z" * 12: "GROQ_API_KEY=[SECRET_1]",
    }
    for raw, expected in cases.items():
        out, m = mask(raw)
        assert expected in out, (raw, out)
        assert m.unmask(out) == raw
    out, m = mask("a@b.com then c@d.org then a@b.com")
    assert out == "[EMAIL_1] then [EMAIL_2] then [EMAIL_1]"
    assert m.report()["counts"] == {"email": 2}


def test_paths_keep_the_file_name_and_hide_the_folder():
    out, m = mask(r"see C:\Users\Test Person\Documents\x.py now")
    assert out == r"see [PATH_1]\x.py now"
    assert "Test Person" not in out and m.unmask(out) == r"see C:\Users\Test Person\Documents\x.py now"
    assert mask(r"open \\fileserver\share\docs\r.txt please")[0] == r"open [PATH_1]\r.txt please"
    assert mask(r"in %APPDATA%\HybridAI\notes.json.")[0] == r"in [PATH_1]\notes.json."
    assert mask("at C:/work/proj/main.py")[0] == "at [PATH_1]/main.py"
    assert mask("at /home/bob/src/main.py")[0] == "at [PATH_1]/main.py"
    assert mask('quoted "C:\\My Docs\\the file.txt" end')[0] == 'quoted "[PATH_1]\\the file.txt" end'
    assert mask("~/notes/todo.md")[0] == "[PATH_1]/todo.md"
    out, _ = mask("same C:\\a\\b\\one.py and C:\\a\\b\\two.py")
    assert out == "same [PATH_1]\\one.py and [PATH_1]\\two.py"


def test_secret_file_paths_are_masked_whole_and_username_only_in_paths():
    out, m = mask(r"load C:\proj\app\.env and C:\proj\keys\id_rsa now")
    assert out == "load [PATH_1] and [PATH_2] now"
    assert m.unmask(out) == r"load C:\proj\app\.env and C:\proj\keys\id_rsa now"
    # the user name in a normal sentence is not touched
    assert mask("Hi, I am Test Person")[0] == "Hi, I am Test Person"
    assert mask(f"home is {HOME}")[0] == "home is [PATH_1]"


# ---------- 5: false positives ----------
@pytest.mark.parametrize("text", [
    "a" * 64,
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "123e4567-e89b-12d3-a456-426614174000",
    "commit 3b18e512dba79e4c8300dd08aeb37f8e728b8dad",
    "data: " + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" * 3,
    'token = os.environ.get("X")',
    "api_key = settings.key",
    "@app.route('/x')",
    "npm i pkg@1.2.3",
    "password = 12345678",
    "pwd = abc",
])
def test_false_positives_stay_unmasked(text):
    assert mask(text)[0] == text


# ---------- 6: system, digest, attachments, images ----------
def test_system_digest_and_file_blocks_are_masked_but_images_are_not():
    import attachments
    att = types.SimpleNamespace(is_image=False, text=f'KEY = "{FAKE_KEY}" # a@b.com', name="c.py",
                                path=r"C:\proj\c.py", detail="", cut=False)
    content, _ = attachments.build_message("explain", [att], 8192)
    url = "data:image/png;base64," + "iVBORw0KGgo" * 3
    img_msg = {"role": "user", "content": [{"type": "text", "text": "see a@b.com"},
                                           {"type": "image_url", "image_url": {"url": url}}]}
    msgs = [{"role": "system", "content": "notes: c@d.org"},
            {"role": "user", "content": "Summary of earlier chat: key " + FAKE_KEY},
            {"role": "user", "content": content}, img_msg]
    before = copy.deepcopy(msgs)
    out, m = privacy.mask_messages(msgs)
    assert msgs == before                                   # input untouched
    blob = repr(out)
    assert FAKE_KEY not in blob and "a@b.com" not in blob and "c@d.org" not in blob
    assert 'path="[PATH_1]\\c.py"' in out[2]["content"]
    assert out[3]["content"][1] == img_msg["content"][1]    # image part unchanged
    assert out[3]["content"][0]["text"] == "see [EMAIL_2]"
    rep = m.report()
    assert rep["images"] == 1 and "1 image sent as-is" in privacy.describe(rep)
    assert all(FAKE_KEY not in i["preview"] for i in rep["items"])


def test_preview_shows_only_the_ends_of_a_key():
    _, m = mask(FAKE_KEY)
    assert m.report()["items"][0]["preview"] == FAKE_KEY[:3] + "…" + FAKE_KEY[-4:]


# ---------- 7: unmasker ----------
def test_unmasker_handles_split_case_and_unknown_placeholders():
    _, m = mask("a@b.com")
    u = privacy.Unmasker(m)
    out = "".join([u.feed("mail [EMA"), u.feed("il_1] and [FOO_9"), u.feed("] "), u.feed("[email_1]"),
                   u.feed(" tail ["), u.flush()])
    assert out == "mail a@b.com and [FOO_9] a@b.com tail ["
    u2 = privacy.Unmasker(m)
    assert u2.feed("[EMAIL_") == "" and u2.feed("1]") == "a@b.com"
    assert u2.feed("[not a placeholder at all, long text here") != ""


# ---------- 9: the UI event ----------
def test_masked_event_does_not_end_the_generation(monkeypatch):
    pytest.importorskip("customtkinter")
    import config
    import log_setup
    monkeypatch.setattr(config, "migrate_legacy", lambda: [])  # importing app must not touch real data
    monkeypatch.setattr(log_setup, "setup_safe", lambda *a, **k: None)
    try:
        import app
    except Exception as e:  # pragma: no cover - environment without a display
        pytest.fail(f"app could not be imported: {e}")
    q = queue.Queue()
    q.put((7, "masked", {"counts": {"email": 1}}))
    done = []
    fake = types.SimpleNamespace(
        events=q, gen={"id": 7}, after=lambda *a: None, _poll=None,
        _on_done=lambda *a: done.append(a), _on_route=lambda *a: None, _on_tokens=lambda *a: None)
    app.HybridAIApp._poll(fake)
    assert done == [] and fake.gen["masked"] == {"counts": {"email": 1}}
