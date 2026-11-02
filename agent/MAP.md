# File map (what lives where)

All paths relative to the project root. Private data is NOT here — it is in `%LOCALAPPDATA%\HybridAI`.

## Core
- `app.py` — main window and all wiring. `send`, `_generate(conv, text, model, content, meta, notes, query, build, use_memory, on_finished, title)`, `start_prepared_chat`, `start_code_chat`, `_memory_block/_memory_context`, `memory_system`, `_check_before_send` (vision switch, cloud confirm), `attach_path`, `pick_files`, `paste_from_clipboard`, `read_resume`, `kit_json/kit_stream`, `_poll` (main-thread queue).
- `config.py` — paths (`DATA_DIR`, `DB_PATH`, `MEMORY_DB`, `SETTINGS_PATH`, `ENV_PATH`, `BACKUP_DIR`), `migrate_legacy()`, `load_env()`. Env var `HYBRID_AI_DATA_DIR` overrides (used by tests).
- `settings.py` — defaults + `_upgrade()`; keys: model, context_tokens, cloud_files, memory, quickask, phone, brief, internship, code_projects, access.
- `router.py` — `MODELS`, `DEFAULT_MODEL`, `VISION_MODELS`, manual `route()`, `prepare()`, `stream_chat()`; Ollama + OpenAI-compatible cloud clients.
- `context.py` — `fit`, `needs_digest`, `digest_range`, `digest_prompt`, `clean_digest` (same context for every model).
- `storage.py` — SQLite chats; turn `extra` JSON (attachments, notes, edits, memory, via); conversation `meta` (digest).
- `classifier.py`, `routing_data.py`, `eval_router.py` — old keyword router experiments (not used now that routing is manual).

## Files and access
- `access.py` — `AccessGate.check(path, action)` → `(ok, reason)`; `blocked_reason`; `allow_once/allow_always/add_folder/remove_folder`.
- `attachments.py` — `Attachment`, `load_file`, `load_image`, `find_paths`, `build_message(question, attachments, context_tokens)`, `_fair_shares`. **No folder support yet.**
- `editor.py` — `extract` (edit blocks), `checks`, `diff_rows`, `stats`, `apply` (backup, keep encoding/newlines), `undo`, `recent_edits`.

## Features
- `memory.py` — `MemoryStore`, `summarize`, `context_block`, `is_recall_question`, `ollama_embedder`, `ollama_completer`.
- `codeexplain.py` — project scan/map, changes since last review, journal, prompt builders (`budget_chars`).
- `quickask.py` — hotkey, foreground window, capture, selection via Ctrl+C (clipboard restored), never-capture list, OCR.
- `phone.py` — `PhoneServer` (stdlib HTTP, token, SSE, uploads, signed `/api/file`, `/api/reminders`, `/api/memory`).
- `tools.py` — phone commands /find /screenshot /web /status /brief /reminders /help.
- `reminders.py` — `parse()`, `ReminderStore`, timetable, `brief()`, popups.
- `internship.py` — SKILLS/coverage, `tailor_prompt`, `parse_tailor`, `apply_docx`, letter/interview prompts, `Tracker`.
- `memory_check.py`, `eval_memory.py` — manual checks that need Ollama.

## UI (`ui/`, CustomTkinter)
`theme.py` colours/fonts · `sidebar.py` nav + status rows · `chat_view.py` · `composer.py` ("+ File" button, send) ·
`inspector.py` (Settings: Display, Memory, Phone, Quick Ask, Access, Recent edits, Files and the cloud, Context size, Connections, Private data) ·
`dialogs.py` · `diff_view.py` · `memory_view.py` · `code_view.py` · `quickask_view.py` · `reminders_view.py` · `internship_view.py`.

## Phone app (`mobile/`)
`index.html`, `app.js` (markdown renderer, model picker, attachments, share target, notifications), `style.css`, `sw.js`, `manifest.webmanifest`, `pair.html`, icons.

## Tests (`tests/`)
One file per module: core, editor, memory, context, codeexplain, quickask, phone, reminders, internship.

## Other
`launch.bat` starts the app · `requirements.txt` · `README.md` (user-facing) · `*_backup.py` old copies (ignore).
