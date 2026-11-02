"""Tests for context.py: the same fitted context for every model."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import context as X  # noqa: E402


def chat(n, size=400):
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"question {i} " + "x" * size})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "y" * size})
    return msgs


def test_short_chat_unchanged():
    msgs = chat(3)
    out, info = X.fit(msgs, system="SYS", budget=8192)
    assert out[0] == {"role": "system", "content": "SYS"} and out[1:] == msgs
    assert info["dropped"] == 0 and not info["digest_used"]


def test_long_chat_trimmed_from_the_start():
    msgs = chat(60) + [{"role": "user", "content": "latest question"}]
    out, info = X.fit(msgs, system="SYS", budget=4096)
    assert out[-1]["content"] == "latest question"          # newest always kept
    assert out[1]["role"] == "user"                          # never starts with an orphaned answer
    assert info["dropped"] > 0 and info["tokens"] <= 4096 - X.REPLY_RESERVE + 50


def test_same_context_for_every_model():
    import types
    sys.modules.setdefault("openai", types.SimpleNamespace(OpenAI=lambda **k: object()))
    import router
    msgs = chat(60) + [{"role": "user", "content": "q"}]
    args = {"budget": 4096, "digest": "- earlier we fixed pip", "digest_upto": 40}
    a = router.prepare("local-general", msgs, "SYS", args)
    b = router.prepare("cloud-groq", msgs, "SYS", args)
    assert a == b and "earlier we fixed pip" in a[1]["content"]


def test_digest_and_lead_system_kept():
    lead = [{"role": "system", "content": "continuing from a past session"}]
    msgs = lead + chat(10)
    out, info = X.fit(msgs, digest="- summary", digest_upto=11, budget=8192)
    assert out[0]["content"].startswith("continuing") and "summary" in out[1]["content"]
    assert out[2] == msgs[11] and info["digest_used"]


def test_needs_digest_and_range():
    msgs = chat(30)
    assert not X.needs_digest(chat(2), 0, 8192)
    assert X.needs_digest(msgs, 0, 8192)
    start, end = X.digest_range(msgs, 0)
    assert start == 0 and end == len(msgs) - X.KEEP_RECENT and msgs[end - 1]["role"] == "assistant"
    assert not X.needs_digest(msgs, end, 8192)


def test_digest_prompt_hides_edit_blocks():
    p = X.digest_prompt(None, [{"role": "assistant", "content": "ok\n```edit:a.py\nsecret code\n```"}])
    assert "secret code" not in p and "(none yet)" in p
