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
    api_key=os.getenv("GEMINI_API_KEY")
)

groq_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

# --- Models ---
MODELS = {
    "local-general": ("llama3.1:8b", local_client),
    "local-coder": ("qwen2.5-coder:7b", local_client),
    "cloud-gemini": ("gemini-2.0-flash", gemini_client),
    "cloud-groq": ("llama-3.3-70b-versatile", groq_client),
}

# --- Classification ---
PII_PATTERNS = [
    r'\b\d{3}-\d{2}-\d{4}\b',
    r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
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


def classify_prompt(prompt):
    lower = prompt.lower()

    for pattern in PII_PATTERNS:
        if re.search(pattern, prompt):
            return "private"

    if any(kw in lower for kw in CODE_KEYWORDS):
        return "code"

    word_count = len(prompt.split())
    has_complex = any(kw in lower for kw in COMPLEX_KEYWORDS)

    if word_count > 150 or has_complex:
        return "complex"

    return "simple"


def route(prompt, mode="auto"):
    if mode == "local":
        return "local-general", "User chose local"
    if mode == "cloud":
        return "cloud-gemini", "User chose cloud"

    category = classify_prompt(prompt)

    if category == "private":
        return "local-general", "PII detected — kept local"
    elif category == "code":
        return "local-coder", "Code task — Qwen Coder"
    elif category == "complex":
        return "cloud-gemini", "Complex task — Gemini"
    else:
        return "local-general", "Simple — local"


def stream_chat(messages, prompt, mode="auto", on_token=None, on_done=None):
    route_name, reason = route(prompt, mode)
    model_id, client = MODELS[route_name]

    # Add the new user message to history
    messages.append({"role": "user", "content": prompt})

    start = time.time()
    full_response = ""

    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            stream=True,
        )

        for chunk in response:
            if chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full_response += token
                if on_token:
                    on_token(token)

    except Exception as e:
        if "cloud" in route_name:
            full_response = "[Cloud unavailable, falling back to local]\n\n"
            if on_token:
                on_token(full_response)
            try:
                fallback = local_client.chat.completions.create(
                    model="llama3.1:8b",
                    messages=messages,
                    stream=True,
                )
                for chunk in fallback:
                    if chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        full_response += token
                        if on_token:
                            on_token(token)
                route_name = "local-general (fallback)"
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

    # Save assistant reply to history
    messages.append({"role": "assistant", "content": full_response})

    elapsed = round((time.time() - start) * 1000)

    if on_done:
        on_done(route_name, reason, elapsed)

    return full_response, route_name, reason, elapsed