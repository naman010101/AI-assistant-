# Runbook

## Run the app (laptop)
`launch.bat` in `C:\Local_ai_model\hybrid-ai` (uses `venv\Scripts\pythonw.exe app.py`).
Needs Ollama running with: `llama3.1:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:7b`, `nomic-embed-text`.
Keys (optional) in `%LOCALAPPDATA%\HybridAI\.env`: Gemini, Groq.
Install: `venv\Scripts\python -m pip install -r requirements.txt --timeout 120 --retries 10`

## Logs
App log: `%LOCALAPPDATA%\HybridAI\logspp.log` (rotates at 1 MB, 5 old files kept).
Level: set the environment variable `HYBRIDAI_LOG_LEVEL` to `DEBUG`, `INFO` (default), `WARNING` or `ERROR`
before starting the app, e.g. `set HYBRIDAI_LOG_LEVEL=DEBUG` then `launch.bat`. Unknown values mean INFO.
DEBUG also writes the per-tick lines that INFO hides.

## Tests
`python -m pytest tests -q` → expected 66 passed, 1 skipped (Tk-free; widgets are stubbed).
Files: test_core, test_editor, test_memory, test_context, test_codeexplain, test_quickask, test_phone, test_reminders, test_log_setup, test_no_silent_except.
`python eval_memory.py` and `memory_check.py` need Ollama running.

## Roll back the chat storage upgrade (step 1.5)
The upgrade keeps a copy of the old chats in `%LOCALAPPDATA%\HybridAIackups\chats-v1-<timestamp>.db` (path also shown in the Log tab). An older app opening an upgraded `chats.db` shows an empty chat list.
1. Close Hybrid AI.
2. In `%LOCALAPPDATA%\HybridAI`, move `chats.db`, `chats.db-wal` and `chats.db-shm` into `backupsfter-1.5\` (do not delete them: they hold chats made since the upgrade).
3. Copy `backups\chats-v1-<timestamp>.db` to `%LOCALAPPDATA%\HybridAI\chats.db`.
4. Check out the commit from before 1.5 (the 1.5 code would just upgrade again).
Chats made after the upgrade are lost unless copied over by hand. The chats-v1 backup is deleted automatically 30 days after the upgrade (logged in the Log tab), so roll back within 30 days or copy it somewhere safe first.

## Start-up switches (tests only)
`HYBRIDAI_NO_DIALOGS=1` makes `startup.py` print its messages ("already running", migration stopped) to stderr instead of opening a message box, and skips focusing the other window. Used by `tests/test_startup.py`; always set it, with `HYBRID_AI_DATA_DIR` pointing at a temp folder, when running `app.py` from a test or CI. Do not set it for normal use.

## Cloud workspace (the agent's machine)
- Linux container; no tkinter / PyPI by default. Project copy at `/home/claude/t`.
- GUI test env: uv python 3.13 + CustomTkinter cloned from GitHub (`/tmp/claude-0/tkpy`) + xvfb. May need rebuilding.
- App-flow scripts were in `/tmp/claude-0/apptest` (may be gone in a new session).
- Reach the laptop with the `mcp__remote-devices__*` tools: `device_bash`, `device_stage_files`, `device_commit_files`.

## Deliver to the laptop
1. Copy changed files to `/mnt/user-data/outputs/hybrid-ai/...` (copy PNG icons from `/home/claude/t`, not the mirror).
2. `device_commit_files` with `devicePath` under `~/mnt/Local_ai_model/hybrid-ai/...` (use `force` only if the owner edited it).
3. On the laptop: parse-check (`python -m py_compile`), run tests, then `git add -A` and `git commit`.
4. Do not delete files on the laptop without `device_request_delete_permission`. Leftover `.git/*.lock` files are safe to remove after asking.
5. Tell the owner to run `git push` (commits so far: Phases 0–7).

## Commit message style
`Phase N: short description` (see `git log`). End with the attribution lines the session asks for.
