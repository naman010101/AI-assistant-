import logging
import re
import os
import time
import threading
from openai import OpenAI
import config

config.load_env()  # API keys live in the private data folder (see config.py)

# --- Clients ---
local_client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

gemini_client = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=os.getenv("GEMINI_API_KEY") or "missing"
)

groq_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY") or "missing"
)

# --- Models ---
MODELS = {
    "local-general": ("llama3.1:8b", local_client),
    "local-coder": ("qwen2.5-coder:7b", local_client),
    "local-vision": ("qwen2.5vl:7b", local_client),   # can see images
    "cloud-gemini": ("gemini-2.0-flash", gemini_client),
    "cloud-groq": ("llama-3.3-70b-versatile", groq_client),
}

# Models that can read images. The others get text only.
VISION_MODELS = {"local-vision", "cloud-gemini"}
DEFAULT_MODEL = "local-general"

# --- Classification (NOT USED by the app right now) ---
#
# The app is in manual mode: you pick the model, nothing is routed
# automatically. The classifier below is kept for studying and for
# eval_router.py; route() only uses it if called without a model.
#
# Routing happens in layers, cheapest and most certain first:
#   1. PII regex            -> "private"  (a hard privacy rule, never learned)
#   2. Obvious code signals -> "code"     (``` fences, tracebacks, error names)
#   3. Very long prompts    -> "complex"
#   4. Embedding classifier -> simple / code / complex, if confident
#   5. Keyword rules        -> the original classifier, used as the fallback
#
# Then a policy table maps (mode, category) to a model.

PII_PATTERNS = [
    r'\b\d{3}-\d{2}-\d{4}\b',
    r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
]

CODE_SIGNALS = [
    r'```',
    r'Traceback \(most recent call last\)',
    r'\b[A-Z][a-zA-Z]+(Error|Exception)\b',          # TypeError, KeyError, ValueError...
    r'^\s*(def|class)\s+\w+.*:\s*$',                 # def foo(x):   class Foo:
    r'^\s*import\s+[\w.]+(\s+as\s+\w+)?\s*$',          # import torch
    r'^\s*from\s+[\w.]+\s+import\s+',                  # from x import y
    r'\{\s*$|^\s*\}\s*;?\s*$',                        # C/JS/Java blocks
]

COMPLEX_KEYWORDS = [
    "analyze", "compare", "explain in detail", "write an essay",
    "research", "summarize", "debug", "refactor", "optimize",
    "multi-step", "reason", "pros and cons", "architecture",
    "in depth", "thoroughly", "comprehensive", "evaluate",
]

CODE_KEYWORDS = [
    "write code", "function", "implement", "algorithm", "script",
    "class ", "def ", "async", "api endpoint", "debug this code",
    "write a program", "code for", "syntax", "compile", "error in",
]

LONG_PROMPT_WORDS = 150

# (mode, category) -> model key
POLICY = {
    "auto":  {"private": "local-general", "code": "local-coder",
              "complex": "cloud-gemini", "simple": "local-general"},
    "local": {"private": "local-general", "code": "local-coder",
              "complex": "local-general", "simple": "local-general"},
    "cloud": {"private": "cloud-gemini", "code": "cloud-groq",
              "complex": "cloud-gemini", "simple": "cloud-groq"},
}

_embedder = None


def get_classifier():
    """Created lazily so importing router.py never waits on Ollama."""
    global _embedder
    if _embedder is None:
        from classifier import EmbeddingClassifier
        from routing_data import SEED
        _embedder = EmbeddingClassifier(SEED)
    return _embedder


def warmup():
    """Embed the seed examples in advance (call from a background thread)."""
    return get_classifier().warmup()


def classify_keywords(prompt):
    """The original rule-based classifier. Kept as the fallback and as the
    baseline that eval_router.py compares against."""
    lower = prompt.lower()

    for pattern in PII_PATTERNS:
        if re.search(pattern, prompt):
            return "private"

    if any(kw in lower for kw in CODE_KEYWORDS):
        return "code"

    word_count = len(prompt.split())
    has_complex = any(kw in lower for kw in COMPLEX_KEYWORDS)

    if word_count > LONG_PROMPT_WORDS or has_complex:
        return "complex"

    return "simple"


def classify_prompt(prompt, use_embeddings=True):
    """Return (category, how) where `how` explains the decision."""
    for pattern in PII_PATTERNS:
        if re.search(pattern, prompt):
            return "private", "PII detected"

    for pattern in CODE_SIGNALS:
        if re.search(pattern, prompt, re.MULTILINE):
            return "code", "code or error text in the prompt"

    if len(prompt.split()) > LONG_PROMPT_WORDS:
        return "complex", f"long prompt (>{LONG_PROMPT_WORDS} words)"

    if use_embeddings:
        result = get_classifier().classify(prompt)
        if result and result["confident"]:
            return result["label"], (f"embedding {result['confidence']:.0%} · "
                                     f"closest: “{result['nearest'][:48]}”")
        if result:
            category = classify_keywords(prompt)
            return category, (f"keywords (embedding unsure: {result['label']} "
                              f"{result['confidence']:.0%})")

    return classify_keywords(prompt), "keywords (embeddings unavailable)"


def route(prompt, mode="manual", model_override=None):
    # Manual mode: the model you picked, or the local default. Nothing is
    # sent to the cloud unless you chose a cloud model yourself.
    if model_override in MODELS:
        return model_override, "Selected manually"
    if mode == "manual":
        return DEFAULT_MODEL, "Default model"

    category, how = classify_prompt(prompt)
    model = POLICY.get(mode, POLICY["auto"])[category]
    if mode == "cloud" and category == "private":
        how += " — sent to cloud because Cloud mode is on"
    return model, f"{category} · {how}"


def _stream(client, model_id, messages, on_token, should_stop):
    """Stream one completion. Returns (text, stopped)."""
    text = ""
    response = client.chat.completions.create(
        model=model_id,
        messages=messages,
        stream=True,
    )
    for chunk in response:
        if should_stop and should_stop():
            response.close()  # closes the HTTP stream, so the server stops generating
            return text, True
        # Some providers send a final chunk with no choices (usage info).
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            text += token
            if on_token:
                on_token(token)
    return text, False


def has_images(content):
    return isinstance(content, list) and any(p.get("type") == "image_url" for p in content)


def text_only(messages):
    """A copy of the history with images replaced by a note, for models that
    can't see images (sending them would fail or confuse the model)."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            texts = [p.get("text", "") if p.get("type") == "text" else
                     "[an image was attached here, but this model can't see images]" for p in c]
            m = {**m, "content": "\n\n".join(t for t in texts if t)}
        out.append(m)
    return out


def _with_system(system, messages):
    return ([{"role": "system", "content": system}] + messages) if system else messages


def prepare(route_name, messages, system=None, fit_args=None, on_context=None):
    """What the model actually receives. With fit_args (budget, digest,
    digest_upto) the history is fitted the same way for every model, so
    switching models never changes what the conversation contains."""
    msgs = for_model(route_name, messages)
    if not fit_args:
        return _with_system(system, msgs)
    import context
    out, info = context.fit(msgs, system, fit_args.get("digest"), fit_args.get("digest_upto", 0),
                            fit_args.get("budget", 8192))
    if on_context:
        on_context(info)
    return out


def for_model(route_name, messages):
    return messages if route_name.split(" ")[0] in VISION_MODELS else text_only(messages)


def stream_chat(messages, prompt, mode="manual", on_token=None, on_done=None,
                model_override=None, should_stop=None, on_route=None, content=None, system=None,
                fit_args=None, on_context=None):
    """`content` (optional) is what the model receives for this turn: the
    prompt plus attached file text, or a list of parts with images. Without
    it, the plain prompt is sent. `system` (optional) is an instruction sent
    first but not stored in the history (e.g. how to propose file edits)."""
    route_name, reason = route(prompt, mode, model_override)
    if on_route:
        on_route(route_name, reason)
    model_id, client = MODELS[route_name]

    # Add the new user message to history
    messages.append({"role": "user", "content": content if content is not None else prompt})

    start = time.time()
    full_response = ""   # what the user sees
    answer = ""          # what is saved to history (no notices or errors)

    try:
        answer, _ = _stream(client, model_id, prepare(route_name, messages, system, fit_args, on_context),
                            on_token, should_stop)
        full_response = answer

    except Exception as e:
        logging.getLogger("hybridai").error(
            "model stream failed (route=%s)", route_name, exc_info=True)
        if "cloud" in route_name and not (should_stop and should_stop()):
            notice = "[Cloud unavailable, falling back to local]\n\n"
            full_response = notice
            if on_token:
                on_token(notice)
            fallback = "local-vision" if has_images(messages[-1]["content"]) else "local-general"
            try:
                answer, _ = _stream(local_client, MODELS[fallback][0],
                                    prepare(fallback, messages, system, fit_args),
                                    on_token, should_stop)
                full_response += answer
                route_name = f"{fallback} (fallback)"
                reason = f"{reason} → cloud error, fell back"
            except Exception as e2:
                logging.getLogger("hybridai").error(
                    "fallback stream failed", exc_info=True)
                err = f"\n\nError: {str(e2)}"
                full_response += err
                if on_token:
                    on_token(err)
        else:
            err = f"\n\nError: {str(e)}"
            full_response += err
            if on_token:
                on_token(err)

    # Save the assistant reply to history. Error text is left out so it
    # doesn't confuse the model on the next turn; a failed turn is dropped.
    if answer:
        messages.append({"role": "assistant", "content": answer})
    else:
        messages.pop()

    elapsed = round((time.time() - start) * 1000)

    if on_done:
        on_done(route_name, reason, elapsed)

    return full_response, route_name, reason, elapsed
