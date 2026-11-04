# Decisions and rules

## Owner decisions
- GPU is 6 GB → local models are 7–8B; Ollama context 8192.
- Moving data = move only (no copy left behind). Default allowed folders: Desktop, Documents, Downloads (incl. OneDrive).
- Cloud files: **ask each time** before file content is sent to Gemini/Groq.
- Edits: **overwrite the file, with a backup**, after the owner approves a diff.
- Memory: **summary only**, saved right away when a session ends; not the full chats.
- Trigger for memory: **automatic** (searches as the owner writes); facts about the owner need confirmation.
- Model choice is **manual**. Auto was removed on purpose.
- Phone app: Android, the owner's own (not a third-party app); phone talks to the laptop.
- Deliver step by step with short updates.

## Rules that must not break
- Private data only under `%LOCALAPPDATA%\HybridAI` (`config.DATA_DIR`). Never write chats/memory/keys into the project.
- `AccessGate` is the only way to read/write user files. Always blocked: `.env`, `*.pem`, `*.key`, `id_rsa*`,
  `credentials*`, `client_secret*.json`, `token.json`, `*.kdbx`, `.ssh .gnupg .aws .azure .kube .docker`,
  the app's own data folder, Windows, Program Files, ProgramData, AppData.
- Tk is main-thread only: worker threads put results on a queue; `_poll()` in `app.py` applies them. Never read a Tk var from a thread.
- Every model gets the same fitted context (`context.fit`), so switching models does not change what the chat "knows".
- Phase 8 (internship kit) was dropped from v2 on 2026-11-04 and parked under Later ideas in `agent/V2_STEPS.md`; its files were never in the repo.
- Reminder/brief times are local time. Phone token and file links are signed; never log the token.
- No automatic cloud routing; never send private content to the cloud without the confirm step.

## Capabilities note (to build)
The system prompt should say, in plain words: the app can read files the user attaches or pastes a path for, edit them
only through a reviewed diff, search past sessions, set reminders, and (after the folder feature) read a whole folder
through the access settings. The model must not say it has no file access.

## Gotchas learned
- `venv\Scripts\pip` breaks if the venv was moved → always `venv\Scripts\python -m pip`.
- Emoji in Tk buttons can render blank on Windows → use text labels.
- pip may time out: `--timeout 120 --retries 10`.
- Local models deny having memory unless the context block says so (see `memory.context_block`).
