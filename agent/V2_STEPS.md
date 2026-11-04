# Hybrid AI — v2 Steps for Claude Code

As of 2026-11-04. Source: the "Hybrid AI — v2 Plan for Claude Code" doc on claude.ai.
The full plan and every phase's "Done when" list are in agent/V2_PLAN.md.
Rules and model routing are in CLAUDE.md; the agents are in .claude/agents/.

## The step loop

1. Pick the next step from this file.
2. Steps marked "architect -> builder": the architect (Opus) writes a design to
   agent/designs/<step>.md; the user approves it before any code is written.
3. Normal steps: builder (Sonnet). Mechanical steps: mechanic (Haiku).
4. pytest must pass; if not, the builder fixes and reruns.
5. The user reads the diff; commit as "v2 <step id>: <what and why>".
6. Last step of a phase: reviewer (Opus) checks every "Done when" item, then merge to main and tag.

Steps marked "You" are done by the user, not Claude Code.
Ideas outside a step go to "Later ideas" at the end of this file.

## Phase 0 — Safety net (branch v2/phase-0-safety)

| Step | Who (model) | Task | Check |
| --- | --- | --- | --- |
| 0.1 | You | `git push`, `git tag v1.0`, `git push --tags` | GitHub shows the latest commit and the tag |
| 0.2 | builder (Sonnet) | Add `log_setup.py`: one logger writing to `%LOCALAPPDATA%\HybridAI\logs\app.log`, rotating at 1 MB, 5 files kept. Call it once at start-up in `app.py` | Stopping Ollama mid-reply writes an error line to `app.log` |
| 0.3 | mechanic (Haiku) | Replace every `except ...: pass` with `log.exception(...)`, behaviour otherwise unchanged | No `except` followed by a bare `pass` remains |
| 0.4 | mechanic (Haiku) | Add `pyproject.toml` with the dependencies from `requirements.txt` and a basic `ruff` config. Don't fix lint warnings yet | `ruff check .` runs and prints a count |
| 0.5 | builder (Sonnet) | Add `.github/workflows/tests.yml` running `pytest` on `windows-latest` for each push | The Actions tab shows a green run |
| 0.6 | mechanic (Haiku) | Run `pytest` with coverage; write the pass count and per-module coverage to `agent/LOG.md` as the v2 baseline | `agent/LOG.md` has today's numbers |
| 0.7 | You, then mechanic | Decide to finish or drop Phase 8, then update `README.md` and `agent/PLAN.md` to match | The docs mention no missing files |
| 0.8 | reviewer (Opus) | End-of-phase review | Every Phase 0 "Done when" item passes |

## Phase 1 — Fixes (branch v2/phase-1-fixes)

Back up `%LOCALAPPDATA%\HybridAI` before step 1.5.

| Step | Who (model) | Task | Check |
| --- | --- | --- | --- |
| 1.1 | You | Look up current model IDs on the Google Gemini and Groq model pages | Two current IDs written down |
| 1.2 | builder (Sonnet) | Move cloud model IDs from `router.MODELS` into settings, set them to the IDs from 1.1, replace "(fallback)" with a clear "Answered locally: cloud unavailable" notice | Wi-Fi off + cloud question shows the notice |
| 1.3 | architect -> builder | Phone safe defaults: `phone.lan` off by default, signed links expire after 1 hour, "Reset phone token" button in settings | New install has the phone server off; old links fail after expiry |
| 1.4 | architect -> builder | Cloud privacy filter: mask emails, API keys, tokens and file paths (patterns from `access.py`) before a prompt goes to a cloud model; show what was masked | A prompt with a fake key reaches the cloud masked |
| 1.5 | architect -> builder | Chat storage: save new messages as rows instead of rewriting whole chats, add an index, migrate old chats. Back up `chats.db` first | All old chats open; opening one takes under 50 ms |
| 1.6 | architect -> builder | Vector store: memory embeddings into `sqlite-vec` behind a small `vectors.py` that Phase 3 reuses; `numpy` fallback. Ask before installing `sqlite-vec` | Same search results, under 50 ms |
| 1.7 | builder (Sonnet) | Tests for cloud fallback, phone login, expired links, path traversal | Router and phone coverage each above 70% |
| 1.8 | reviewer (Opus) | End-of-phase review | Every Phase 1 "Done when" item passes |

## Phase 2 — Memory controls (branch v2/phase-2-memory)

| Step | Who (model) | Task | Check |
| --- | --- | --- | --- |
| 2.1 | architect (Opus) | Design the per-chat memory switch: `use_memory` and `save_choice` columns, how the context builder skips memory, how the three close triggers (chat switch, 30 min idle, restart) treat unticked chats. Rule: never delete a chat before the user answers | User agrees with `agent/designs/2.1.md` |
| 2.2 | builder (Sonnet) | Add the two columns to `chats.db` with a safe migration (old chats get `use_memory = 1`) | Old chats still open |
| 2.3 | builder (Sonnet) | Context builder adds no recent facts and no search hits when `use_memory` is 0; log the prompt at debug level | Debug log of an unticked chat shows no memory text |
| 2.4 | builder (Sonnet) | New-chat checkbox "Use my memory" (default from settings), locked after the first message, "Memory: on/off" in the chat header | Box locks after sending; header label right |
| 2.5 | builder (Sonnet) | Close path from the 2.1 design: ask on chat switch, mark "waiting" on idle, ask once at start-up for waiting chats. Yes -> summarise; No -> delete | Yes leaves one summary; No leaves nothing; idle chat survives a restart |
| 2.6 | builder (Sonnet) | Same checkbox on the phone page, same column | Unticked phone chat gets no memory |
| 2.7 | mechanic (Haiku) | Record "summaries only, per-chat opt-out, ask before saving" in `agent/DECISIONS.md` | Entry present |
| 2.8 | reviewer (Opus) | End-of-phase review, extra care on any path that deletes data | Every Phase 2 "Done when" item passes |

## Phase 3 — Knowledge base (branch v2/phase-3-library)

| Step | Who (model) | Task | Check |
| --- | --- | --- | --- |
| 3.1 | architect (Opus) | Design `library.py` and `knowledge.db`: tables, chunk size and overlap, extractors already in `attachments.py`, combining `vectors.py` with SQLite full-text search, prompt markers for library text, library text never reaches the cloud unless enabled | User agrees with `agent/designs/3.1.md` |
| 3.2 | builder (Sonnet) | Reading and splitting: text, Markdown, code (Python by function via `codeexplain.py`), PDF, Word. Every path through `AccessGate` | Unit tests split sample files as designed; a `.env` is skipped |
| 3.3 | builder (Sonnet) | Embedding and storing: `nomic-embed-text` with `search_document: ` / `search_query: ` prefixes, vectors in `vectors.py`, text in a full-text index | Searching a test folder finds the expected chunk |
| 3.4 | builder (Sonnet) | Indexing on a worker thread with progress through `self.events`; Refresh re-reads only changed files and drops deleted ones | 200 files index without freezing; one edit re-reads only that file |
| 3.5 | builder (Sonnet) | Answering: "Use my library" toggle, top 5 chunks from both searches combined, fitted into the 8,192-token budget, Sources under the reply that open the file | A question about a test file cites that file |
| 3.6 | builder (Sonnet) | Library screen: add files or folders, see indexed and skipped files, Refresh, remove | Add, refresh and remove a folder works |
| 3.7 | You | Write 20 questions about your own documents, each with the answering file, in `eval_library_questions.json` | 20 questions saved |
| 3.8 | builder (Sonnet) | `eval_library.py`, like `eval_memory.py`: report how often the right file is in the top 5 | At least 16 of 20 |
| 3.9 | builder (Sonnet) | Library questions and Sources on the phone page | A phone question shows its sources |
| 3.10 | reviewer (Opus) | End-of-phase review, including a test that library text containing instructions is not obeyed | Every Phase 3 "Done when" item passes |

## Phase 4 — Model and read-only tools (branch v2/phase-4-tools)

Can be skipped or delayed without blocking Phase 5.

| Step | Who (model) | Task | Check |
| --- | --- | --- | --- |
| 4.1 | builder (Sonnet) | `bench.py`: 20 prompts on any list of Ollama models; record time to first word and words per second; save answers with model names hidden | One run produces a results file |
| 4.2 | You | `ollama pull qwen3:8b` and `qwen3:4b`; run `bench.py` against them and `llama3.1:8b`; judge the hidden answers | A model is picked |
| 4.3 | mechanic (Haiku) | Set the chosen default model; record result and reason in `agent/LOG.md` and `agent/DECISIONS.md` | App uses the new default |
| 4.4 | architect (Opus) | Design the tool loop: through the router's client, at most 3 rounds, local models only, results marked as reference, fallback when a model doesn't support tools | User agrees with `agent/designs/4.4.md` |
| 4.5 | builder (Sonnet) | Build the loop and five tools: `search_memory`, `search_library`, `read_file` (through `AccessGate`), `list_reminders`, `get_time`; "Read: ..." line under replies; setting to turn tools off | "What's in app.py?" shows "Read: app.py" |
| 4.6 | builder (Sonnet) | Tool eval: 20 prompts, some needing a tool and some small talk, scoring the tool picked | Right tool at least 17 times; none on small talk |
| 4.7 | reviewer (Opus) | End-of-phase review, including a `read_file` attempt on a secret file | Every Phase 4 "Done when" item passes |

## Phase 5 — Interface polish (branch v2/phase-5-ui)

Claude Code can't see the screen: after each step the user runs the app and reports what looks
wrong, ideally with a screenshot.

| Step | Who (model) | Task | Check |
| --- | --- | --- | --- |
| 5.1 | You | Screenshot every screen into `agent/screens/before/` | All screens captured |
| 5.2 | builder (Sonnet) | `ui/theme.py`: light and dark colours, 8 px spacing steps, corner radius, Segoe UI and Cascadia Code; live light/dark switch | Switching theme needs no restart |
| 5.3 | mechanic (Haiku) | Replace every colour value in `ui/` and `app.py` with a name from `ui/theme.py` | No colour values outside `theme.py` |
| 5.4 | builder (Sonnet) | Message cards: rounded, more padding, user and model styled differently, model name and notices underneath | Looks right to the user |
| 5.5 | builder (Sonnet) | Basic formatting with text tags (bold, inline code, headings, lists); code blocks with language label and Copy button | Copy puts the exact code on the clipboard |
| 5.6 | builder (Sonnet) | Smoother streaming: each 40 ms poll's text in one insert, no redraw of the message | A 500-line reply streams without stutter |
| 5.7 | builder (Sonnet) | Sidebar (Today / Yesterday / Earlier, search, hover, Library entry) and a growing input box: Enter sends, Shift+Enter new line | Looks and works right to the user |
| 5.8 | builder (Sonnet) | New-chat screen with memory and library toggles and 3 example prompts; settings tabs: Models, Memory, Library, Phone, Privacy | Every setting still reachable |
| 5.9 | mechanic (Haiku) | Apply the theme colours to the phone page's CSS | Phone and desktop colours match |
| 5.10 | You, then reviewer | Screenshot every screen into `agent/screens/after/`, then end-of-phase review | Every Phase 5 "Done when" item passes |

## When something goes wrong

| Problem | What to do |
| --- | --- |
| A step broke the app | `git revert` that step's commit, retry with a clearer prompt. `main` and phase tags are safe points |
| Data looks wrong after a migration | Stop using the app, restore the `%LOCALAPPDATA%\HybridAI` backup, ask the architect to find the cause |
| An agent goes in circles or edits files outside the step | Esc, `/clear`, restart the step with "Only change these files: ..." |
| An agent seems to use the wrong model | Check `CLAUDE_CODE_SUBAGENT_MODEL` is unset and the session started with `--model sonnet`, not `opusplan` |
| Tests fail after a step | The builder fixes until `pytest` passes. Never commit failing or skipped tests |
| A design doesn't make sense | Don't approve; ask the architect to explain more simply or give two options |
| Opus or Sonnet not available | Use Sonnet for architect and reviewer and read their output more carefully |

## Later ideas

(Add ideas that come up during a step here instead of doing them.)
