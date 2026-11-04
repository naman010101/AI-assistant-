# Hybrid AI — v2 Implementation Plan

As of 2026-11-04. Source: the "Hybrid AI — v2 Implementation Plan" doc on claude.ai.
Step-by-step instructions for Claude Code are in agent/V2_STEPS.md.

v2 makes Hybrid AI safer and more useful for its owner on Windows. It runs in six phases:
fixes first, then memory controls, a knowledge base, a stronger model with read-only tools,
and finally a cleaner interface.

## At a glance

| Phase | Name | Size | Main work |
| --- | --- | --- | --- |
| 0 | Safety net | 1–2 days | Push, logging, CI, test baseline |
| 1 | Fixes | 1–2 weeks | Cloud model IDs, phone defaults, privacy filter, speed |
| 2 | Memory controls | 3–5 days | Use-memory checkbox, save-this-chat prompt |
| 3 | Knowledge base | 2–3 weeks | Library, hybrid search, sources under replies |
| 4 | Model and tools | 1–2 weeks | Benchmark Qwen3, 5 read-only tools |
| 5 | Interface polish | 1–2 weeks | Theme, message cards, code blocks with Copy |

About 6–10 weeks of work in total. Phase 1 sets up the vector store that Phase 3 uses.
Phase 4 can be skipped or delayed without blocking Phase 5.
A phase ends when every "Done when" item in it is ticked.

## Decisions this plan is built on

| Topic | Decision | What it changes |
| --- | --- | --- |
| Who v2 is for | Mainly the owner | No installer or first-run wizard for strangers (B1, B2 dropped) |
| Platform | Windows only | No macOS/Linux work (B12 dropped); Quick Ask stays |
| Time | No limit | Phases are ordered by what depends on what, not by weeks |
| Memory | Summaries only, plus a "Use my memory" checkbox on new chats and a "Save this chat to memory?" prompt for unticked chats | Phase 2; A1 becomes "delete only after a summary, or after the user says no" |
| Cloud | Kept, used now and then | Fix retired model IDs (A2) and add a privacy filter (D1) |
| Tool calling | Yes, read-only tools first | Phase 4, after a model swap is measured (B9) |
| First big feature | Knowledge base (C4) | Phase 3 |
| Desktop interface | Stay on CustomTkinter, polish it with a light web-style feel | Phase 5; no rewrite |
| Phone | No native app; keep the web page, make it safe | Safe defaults in Phase 1; native app parked |

## Phase 0 — Safety net

Before changing anything, make sure every change can be seen, tested and undone.

1. Push the 4 local commits to GitHub so the remote matches the laptop, then tag it `v1.0`.
2. Add logging: one `logging` setup writing to `%LOCALAPPDATA%\HybridAI\logs\app.log`, rotating
   at about 1 MB with 5 old files kept. Replace the 10 `except: pass` blocks with
   `log.exception(...)`. The other 62 broad excepts are handled as each file is touched.
3. Add `pyproject.toml` listing the dependencies with versions, next to `requirements.txt`,
   and a `ruff` setting for basic linting.
4. Add CI: a GitHub Actions workflow running `pytest` on `windows-latest` for every push.
5. Record the baseline: 61 tests pass, 1 skipped. Write that and per-module coverage into
   `agent/LOG.md`.
6. Settle Phase 8: its files are missing. Finish it or remove it from `agent/PLAN.md` and the README.

Done when:
- [ ] GitHub shows the same latest commit as the laptop, tagged `v1.0`
- [ ] Forcing an error (for example, stopping Ollama mid-reply) writes a line to `app.log`
- [ ] The CI run is green on GitHub
- [ ] README and `agent/PLAN.md` no longer mention work that doesn't exist

## Phase 1 — Fixes

| # | Fix | What to do | Size |
| --- | --- | --- | --- |
| A2 | Retired cloud models | `gemini-2.0-flash` is retired; Groq lists `llama-3.3-70b-versatile` for shutdown. Move model IDs out of `router.MODELS` into settings, pick current replacements from each provider's models page, and show a clear notice when a reply falls back to local instead of a small "(fallback)" label | S |
| A3 | Phone safe defaults | `phone.lan` off by default. Signed file links expire (for example after 1 hour). A "Reset phone token" button in settings | S |
| D1 | Cloud privacy filter | Before a prompt goes to Gemini or Groq, mask emails, API keys, tokens and file paths, and show what was masked. Reuse the secret patterns in `access.py` | M |
| A9 | Slow search | `memory.search` loops over every vector in Python (208 ms at 1,000 summaries in a test). Move vectors to `sqlite-vec`, which Phase 3 needs anyway | M |
| A10 | Slow chat loading | `Store.get` took 133 ms at 2,000 chats. Save new messages as rows instead of rewriting whole chats, and add an index | M |
| A14 | Missing tests | Router is at 41% coverage, phone at 16%. Add tests for cloud fallback, phone login, expired links and path traversal | M |

The timings came from a cloud machine, not the laptop; measure again before and after A9 and A10.

Done when:
- [ ] A cloud question with no internet shows a visible "answered locally" notice
- [ ] A fresh install has the phone server off; an old file link stops working after it expires
- [ ] A prompt containing a fake API key reaches the cloud masked, and the app shows what it masked
- [ ] Memory search and chat opening are each under 50 ms on the laptop with real data
- [ ] Router and phone coverage are each above 70%, and CI is green

## Phase 2 — Memory controls

Each new chat gets a "Use my memory" checkbox. Unticked chats ignore the past and ask before
anything is saved.

What the user sees:
- A new chat shows "Use my memory", ticked by default (default changeable in settings).
- After the first message the box locks. The chat header shows "Memory: on" or "Memory: off".
- When an unticked chat closes, the app asks "Save this chat to memory?". Yes summarises it as
  today. No deletes it with nothing kept.

What changes in the code:
1. `chats.db`: add `use_memory` (default 1) and `save_choice` (empty, yes or no).
2. Context builder: when `use_memory` is 0, add neither the 15 recent facts nor the search hits.
3. Close path (`_on_summarised` -> `_forget`): for unticked chats, skip the summary until answered.
4. The three close triggers for unticked chats:
   - Switching chats: ask right away.
   - 30 minutes idle: don't ask while away; mark the chat "waiting for your answer".
   - App restart: ask once at startup about any chats still waiting.
5. The phone page shows the same checkbox, using the same column.
6. Record "summaries only, per-chat opt-out, ask before saving" in `agent/DECISIONS.md`.

Rule: a chat is never deleted until the user has answered.
Optional, later: split the checkbox into "facts about me" and "past chats".

Done when:
- [ ] An unticked chat's prompt contains no memory text (checked in the debug log)
- [ ] Answering No leaves no summary and no chat; answering Yes leaves one summary
- [ ] An unticked chat left idle is still there after a restart, and the app asks about it once
- [ ] Tests cover all three triggers for ticked and unticked chats

## Phase 3 — Knowledge base (C4)

Files or folders are added to a Library. The app searches them when asked a question and lists
the files it used under the reply. New `library.py` and `knowledge.db`.

1. Add: a Library screen adds files or folders. Every path goes through `AccessGate`; secret
   files are skipped and listed.
2. Read: text, Markdown and code directly; PDF and Word with the extractors `attachments.py`
   has, adding `pypdf` or `python-docx` only if missing.
3. Split: chunks of about 500 tokens with a small overlap, broken at headings and paragraphs.
   Python files split by function, reusing `codeexplain.py`.
4. Embed: `nomic-embed-text`; chunks get `search_document: `, questions get `search_query: `.
5. Store: vectors via `sqlite-vec` (from Phase 1); text also in SQLite full-text search (C12).
6. Keep fresh: on start-up and Refresh, re-read only changed files; remove deleted ones. Index
   on a worker thread with progress through the event queue.
7. Answer: a "Use my library" toggle. Top 5 chunks from both searches combined, fitted into the
   8,192-token budget, shown as Sources under the reply; clicking opens the file.

Safety:
- Library text is reference material, never instructions, inside clear prompt markers.
- Library chunks never go to a cloud model unless enabled in settings; then the privacy filter runs.

Measuring: `eval_library.py`, like `eval_memory.py`: 20 questions with the file holding each answer.

Done when:
- [ ] A folder of 200 mixed files indexes without freezing the window, with progress shown
- [ ] The right file is in the top 5 sources for at least 16 of the 20 eval questions
- [ ] Editing one file and pressing Refresh re-reads only that file
- [ ] A `.env` file inside an added folder is skipped and listed
- [ ] The phone page can ask a library question and shows its sources

## Phase 4 — Better model and read-only tools (B9, D2)

Step 1, measure before switching:
1. `bench.py` with 20 real prompts: general, code, reminders, memory.
2. Run on `llama3.1:8b`, `qwen3:8b` (5.2 GB) and `qwen3:4b` (2.5 GB). Record time to first word,
   words per second, and whether each fits in GPU memory.
3. Judge answers blind. Switch the default only if the new model wins and is fast enough.
4. Qwen3 thinking off by default, with a "Think harder" toggle.

Step 2, read-only tools through the router's OpenAI-compatible client, at most 3 rounds:

| Tool | What it does | Guard |
| --- | --- | --- |
| `search_memory` | Searches summaries and facts | Off in chats with memory unticked |
| `search_library` | Searches the Phase 3 library | Off when "Use my library" is off |
| `read_file` | Reads one file | Through `AccessGate`; outside paths still ask |
| `list_reminders` | Lists upcoming reminders | None needed |
| `get_time` | Current date and time | None needed |

Rules:
- Every tool call shows as a small line under the reply, such as "Read: app.py".
- Tool results are reference, never instructions.
- Tools work only with local models at first.
- If a model doesn't support tools, answer without them instead of failing.
- Tools that change things come after v2, each behind an approval card.

Done when:
- [ ] `bench.py` results are in `agent/LOG.md`, with the model choice and the reason
- [ ] On 20 test prompts, the right tool is picked at least 17 times and none on small talk
- [ ] A `read_file` request for a secret file is refused by `AccessGate`
- [ ] Turning tools off restores today's behaviour exactly

## Phase 5 — Interface polish

Keep CustomTkinter; make it simpler and a little more like a modern web chat.

1. Screenshot every screen first, into `agent/screens/before/`.
2. One theme file, `ui/theme.py`: every colour (light and dark), spacing in steps of 8 px,
   corner radius, fonts (Segoe UI, Cascadia Code). No other file sets a colour.
3. Message cards: rounded, more padding, user and model styled differently; model name and
   notices under the card.
4. Basic formatting with text tags: bold, inline code, headings, lists. Code blocks with a
   monospace panel, language label and Copy button. Tables stay plain text.
5. Smoother streaming: insert each 40 ms poll's text in one go.
6. Sidebar: Today / Yesterday / Earlier, search box, hover highlights, Library entry.
7. Input box that grows; Enter sends, Shift+Enter new line; attach button inside.
8. New-chat screen with memory and library toggles and three example prompts.
9. Settings in tabs: Models, Memory, Library, Phone, Privacy.

The phone page gets the same theme colours.

Done when:
- [ ] Before and after screenshots of every screen are in `agent/screens/`
- [ ] Searching the code finds no colour values outside `ui/theme.py`
- [ ] Switching light and dark takes effect without a restart
- [ ] A 500-line reply with code streams without visible stutter; Copy puts the exact code on the clipboard

## Risks

| Risk | Phase | What to do |
| --- | --- | --- |
| `sqlite-vec` won't install or load | 1, 3 | Fall back to `numpy`; fine up to tens of thousands of chunks |
| PDFs extract badly | 3 | Check the eval per file type; skip scanned PDFs or OCR them with Windows OCR |
| Qwen3 too slow or doesn't fit the GPU | 4 | Keep `llama3.1:8b`, try `qwen3:4b`, or keep tools off |
| The model calls tools badly | 4 | Fewer tools, clearer descriptions, or tools behind a button |
| CustomTkinter can't reach the wanted look | 5 | Accept the limits for v2; a web UI stays a later option |
| Untested `app.py` and `ui/` break | All | Small commits, read the diff, add a test for each bug |

## Open questions

- [ ] Which GPU does the laptop have, and how much video memory?
- [ ] Which folders go in the Library first, and roughly how many files?
- [ ] Is the phone page used now?
- [ ] Should new chats start with memory ticked (default) or unticked?
- [ ] What was Phase 8 meant to be?
