# Runbook

## Run the app (laptop)
`launch.bat` in `C:\Local_ai_model\hybrid-ai` (uses `venv\Scripts\pythonw.exe app.py`).
Needs Ollama running with: `llama3.1:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:7b`, `nomic-embed-text`.
Keys (optional) in `%LOCALAPPDATA%\HybridAI\.env`: Gemini, Groq.
Install: `venv\Scripts\python -m pip install -r requirements.txt --timeout 120 --retries 10`

## Tests
`python -m pytest tests -q` → expected 66 passed, 1 skipped (Tk-free; widgets are stubbed).
Files: test_core, test_editor, test_memory, test_context, test_codeexplain, test_quickask, test_phone, test_reminders, test_internship.
`python eval_memory.py` and `memory_check.py` need Ollama running.

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
5. Tell the owner to run `git push` (commits so far: Phases 0–7 are committed; Phase 8 is not).

## Commit message style
`Phase N: short description` (see `git log`). End with the attribution lines the session asks for.
