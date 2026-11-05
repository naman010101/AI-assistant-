"""Step 1.2: cloud model IDs come from settings; a cloud failure is shown as
'Answered locally: cloud unavailable'. No network: the clients are replaced."""
import json
import sys
import types

import pytest


@pytest.fixture
def router(monkeypatch):
    try:
        import openai  # noqa: F401
    except ImportError:  # stand-in so router can be imported without the package
        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=lambda **k: object()))
    import router
    return router


def test_default_cloud_ids_and_router_uses_them(router):
    import settings
    assert settings.DEFAULTS["cloud_models"] == {
        "cloud-gemini": "gemini-3.8-flash", "cloud-groq": "openai/gpt-oss-120b"}
    ids = settings.cloud_models(path="no-such-file.json")
    assert router.MODELS["cloud-gemini"][0] == ids["cloud-gemini"]
    assert router.MODELS["cloud-groq"][0] == ids["cloud-groq"]
    assert router.MODELS["cloud-gemini"][1] is router.gemini_client
    assert router.MODELS["local-general"][0] == "llama3.1:8b"  # local IDs stay in the router


def test_old_settings_file_gets_new_defaults_and_overrides_win(tmp_path):
    import settings
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"model": "local-coder"}), encoding="utf-8")
    assert settings.cloud_models(str(p))["cloud-groq"] == "openai/gpt-oss-120b"
    assert json.loads(p.read_text(encoding="utf-8")) == {"model": "local-coder"}  # read-only
    p.write_text(json.dumps({"cloud_models": {"cloud-groq": "other/model"}}), encoding="utf-8")
    ids = settings.cloud_models(str(p))
    assert ids == {"cloud-gemini": "gemini-3.8-flash", "cloud-groq": "other/model"}
    p.write_text("{not json", encoding="utf-8")
    assert settings.cloud_models(str(p))["cloud-gemini"] == "gemini-3.8-flash"


def test_cloud_failure_answers_locally_with_notice(router, monkeypatch):
    calls = []

    def fake_stream(client, model_id, messages, on_token, should_stop):
        calls.append(client)
        if client is not router.local_client:
            raise ConnectionError("wifi off")
        on_token("hi there")
        return "hi there", False

    monkeypatch.setattr(router, "_stream", fake_stream)
    monkeypatch.setattr(router, "prepare", lambda *a, **k: [])
    tokens = []
    hist = []
    text, route_name, reason, _ = router.stream_chat(
        hist, "hello", model_override="cloud-groq", on_token=tokens.append)
    assert route_name == "local-general (Answered locally: cloud unavailable)"
    assert router.is_fallback(route_name) and not router.is_fallback("cloud-groq")
    assert router.FALLBACK_NOTICE in text and text.endswith("hi there")
    assert router.is_fallback_notice(tokens[0]) and not router.is_fallback_notice("hi there")
    assert calls == [router.groq_client, router.local_client]
    assert hist[-1] == {"role": "assistant", "content": "hi there"}  # notice not saved to history
    assert router.for_model(route_name, [{"role": "user", "content": "x"}])  # base model still parsed


def test_ui_helpers_handle_the_notice():
    pytest.importorskip("customtkinter")
    from ui import theme
    name = "local-general (Answered locally: cloud unavailable)"
    assert theme.is_fallback(name) and not theme.is_fallback("local-general")
    assert theme.is_fallback("local-general (fallback)")  # label saved by v1 chats
    assert theme.model_label(name) == "Llama 3.1 8B (Answered locally: cloud unavailable)"
    assert theme.where(name) == "Local"
    assert theme.FALLBACK_NOTICE == sys.modules["router"].FALLBACK_NOTICE
