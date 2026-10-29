import re
import os
import time
import threading
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

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
    "cloud-gemini": ("gemini-2.0-flash", gemini_client),
    "cloud-groq": ("llama-3.3-70b-versatile", groq_client),
}

# --- Classification ---
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


def route(prompt, mode="auto", model_override=None):
    # A model picked by hand in the UI wins over everything else.
    if model_override in MODELS:
        return model_override, "Selected manually"

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


def stream_chat(messages, prompt, mode="auto", on_token=None, on_done=None,
                model_override=None, should_stop=None, on_route=None):
    route_name, reason = route(prompt, mode, model_override)
    if on_route:
        on_route(route_name, reason)
    model_id, client = MODELS[route_name]

    # Add the new user message to history
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    full_response = ""   # what the user sees
    answer = ""          # what is saved to history (no notices or errors)

    try:
        answer, _ = _stream(client, model_id, messages, on_token, should_stop)
        full_response = answer

    except Exception as e:
        if "cloud" in route_name and not (should_stop and should_stop()):
            notice = "[Cloud unavailable, falling back to local]\n\n"
            full_response = notice
            if on_token:
                on_token(notice)
            try:
                answer, _ = _stream(local_client, "llama3.1:8b", messages,
                                    on_token, should_stop)
                full_response += answer
                route_name = "local-general (fallback)"
                reason = f"{reason} → cloud error, fell back"
            except Exception as e2:
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
