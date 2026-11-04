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
