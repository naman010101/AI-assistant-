"""Fitting a chat into a model's context window — the same way for every model.

Why: a local model gets ~8k tokens, Groq 128k, Gemini much more. If every
model got "as much history as fits", switching models mid-chat would change
what the model knows about the conversation (Llama silently loses the start,
Groq still sees it). Instead, every model gets the SAME fitted context:

    [system]  memory notes + edit instructions          (rebuilt each message)
    [system]  digest: a summary of older messages       (if the chat is long)
    [history] the most recent messages, oldest dropped first, newest user
              message always kept

The digest is written by the local model in the background once a chat grows
past DIGEST_AT of the budget, so older messages are summarised instead of
silently cut. Nothing here calls a model; app.py does that.
"""
import json
import logging
import re

log = logging.getLogger("hybridai")

CHARS_PER_TOKEN = 3.5      # rough, safe for English and code
IMAGE_TOKENS = 1000        # an attached image, roughly
REPLY_RESERVE = 1536       # room left for the model's answer
DIGEST_AT = 0.6            # start summarising older messages at 60% of the budget
KEEP_RECENT = 6            # messages never folded into the digest


def tokens(content):
    """Rough token count of a message content (str or list of parts)."""
    if isinstance(content, list):
        return sum(tokens(p.get("text", "")) if p.get("type") == "text" else IMAGE_TOKENS for p in content)
    return int(len(content or "") / CHARS_PER_TOKEN) + 4


def total(messages):
    return sum(tokens(m.get("content")) for m in messages)


def fit(messages, system=None, digest=None, digest_upto=0, budget=8192):
    """Return (messages_to_send, info).

    messages     the chat history; leading system messages (e.g. "continuing
                 from a past session") are always kept
    digest       summary text of messages[:digest_upto], or None
    info         dict: sent, dropped, digest_used, tokens
    """
    lead = []
    for m in messages:
        if m.get("role") != "system":
            break
        lead.append(m)
    body = messages[len(lead):]
    start = 0
    head = [{"role": "system", "content": system}] if system else []
    head += lead
    used_digest = False
    if digest and digest_upto > 0:
        start = min(digest_upto - len(lead), len(body))
        start = max(start, 0)
        head.append({"role": "system", "content": "Summary of the earlier part of this conversation "
                                                  "(the messages themselves are no longer shown):\n" + digest})
        used_digest = True
    tail = body[start:]

    room = budget - REPLY_RESERVE - total(head)
    kept = []
    for m in reversed(tail):
        t = tokens(m.get("content"))
        if kept and room - t < 0:
            break
        kept.append(m)
        room -= t
    kept.reverse()
    # never start the kept part with an assistant reply that has lost its question
    while len(kept) > 1 and kept[0].get("role") == "assistant":
        kept.pop(0)
    dropped = len(tail) - len(kept)
    out = head + kept
    return out, {"sent": len(kept), "dropped": dropped, "digest_used": used_digest, "tokens": total(out)}


def needs_digest(messages, digest_upto, budget):
    """True when the not-yet-digested part is big enough to fold up."""
    lead = 0
    for m in messages:
        if m.get("role") != "system":
            break
        lead += 1
    start = max(digest_upto, lead)
    pending = messages[start:len(messages) - KEEP_RECENT]
    return bool(pending) and total(messages[start:]) > DIGEST_AT * budget


def digest_range(messages, digest_upto):
    """(start, end) of the messages to fold into the next digest."""
    lead = 0
    for m in messages:
        if m.get("role") != "system":
            break
        lead += 1
    start = max(digest_upto, lead)
    end = max(start, len(messages) - KEEP_RECENT)
    # end on a finished exchange (after an assistant message)
    while end > start and messages[end - 1].get("role") != "assistant":
        end -= 1
    return start, end


DIGEST_PROMPT = """You keep a running summary of a long conversation between a user and their AI assistant.
Update the summary so it also covers the new messages. Keep every decision, fact, name, number,
file name and open question; drop small talk. Write plain bullet points, at most 250 words.
Reply with the updated summary only.

CURRENT SUMMARY:
{old}

NEW MESSAGES:
{new}
"""


def digest_prompt(old_digest, messages):
    lines = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(p.get("text", "[image]") if p.get("type") == "text" else "[image]" for p in c)
        c = re.sub(r"```edit:[^\n]*\n.*?```", "[a proposed new version of a file]", c or "", flags=re.S)
        if len(c) > 3000:
            c = c[:1500] + " … " + c[-1200:]
        lines.append(f"{m.get('role', '?').upper()}: {c}")
    return DIGEST_PROMPT.format(old=old_digest or "(none yet)", new="\n\n".join(lines))


def clean_digest(text):
    text = (text or "").strip()
    try:  # the JSON-mode completer may wrap it
        data = json.loads(text)
        if isinstance(data, dict):
            text = next((v for v in data.values() if isinstance(v, str)), text)
            lists = [v for v in data.values() if isinstance(v, list)]
            if lists and not isinstance(text, str):
                text = "\n".join(f"- {x}" for x in lists[0])
    except ValueError:
        log.debug("digest reply wasn't JSON; keeping it as plain text", exc_info=True)
    return text[:4000]
