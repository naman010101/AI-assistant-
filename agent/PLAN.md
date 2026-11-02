# Implementation plan and task status

Legend: `[x]` done and tested · `[ ]` not done · `[~]` partly done. Status as of 2026-11-02.

## Phase 0 — Foundations  [x]
- [x] Chats persist between sessions (SQLite `chats.db`)
- [x] Move private data out of the project to `%LOCALAPPDATA%\HybridAI` (`config.py`, `migrate_legacy()`)
- [x] Settings file with defaults and upgrade step (`settings.py`)
- [x] Remove "Auto" routing → manual model choice only
- [x] Model status in sidebar (Ollama / Gemini / Groq)

## Phase 1 — Files and access  [x]
- [x] Access gate: allowed folders, always-blocked secrets/system dirs (`access.py`)
- [x] Settings → Access: add/remove folders, toggles (read, edit, screen, search, clipboard, apps)
- [x] Attach files: "+ File" picker, pasted full path, Ctrl+V image (`attachments.py`)
- [x] Readers: text/code, PDF (pypdf), Word (python-docx), Excel (openpyxl), notebooks, images
- [x] Long files cut to fit context and the user is told how much was included
- [x] Ask before sending file content to a cloud model (`cloud_files` = "ask")
- [x] Vision: switch to `local-vision` when an image is attached

## Phase 2 — Safe editing  [x]
- [x] Model proposes ```edit:<name> blocks (`editor.py`)
- [x] Checks: syntax error, placeholder text, much shorter, file changed on disk, JSON validity
- [x] Side-by-side diff window with Apply / Discard / Copy (`ui/diff_view.py`)
- [x] Backup before write, keep encoding and line endings, undo, edit log in Settings

## Phase 3 — Memory  [x]
- [x] Summarise finished sessions with the local model (`memory.py`)
- [x] Embeddings (`nomic-embed-text`) and search with threshold 0.5
- [x] Facts about the user: pending → confirmed by the user → used
- [x] Recall questions ("do you remember…") search without needing a topic
- [x] Memory window: browse, edit, pin, delete (`ui/memory_view.py`)
- [x] Same fitted context for every model + rolling digest of old messages (`context.py`)
- [x] `eval_memory.py` to choose the threshold

## Phase 4 — Code explainer  [x]
- [x] Project map, changes since last review, journal (`codeexplain.py`, `ui/code_view.py`)
- [x] Explain file/function at three depths, trace a feature, quiz, explain a traceback

## Phase 5 — Quick Ask  [x]
- [x] Global hotkey Ctrl+Shift+Space (ctypes RegisterHotKey) (`quickask.py`)
- [x] Capture foreground window, selected text, screenshot + OCR (Windows OCR via winrt, optional)
- [x] Never-capture list (password managers, banking)
- [x] Popup: explain, summarise, translate, rewrite, fix grammar; copy or paste in place
- [ ] Real-laptop test of hotkey and OCR (owner to try)

## Phase 6 — Phone  [x]
- [x] Phone server: token, SSE chat, uploads, signed file links (`phone.py`)
- [x] Android web app / PWA: install to home screen, share-to-laptop, model picker (`mobile/`)
- [x] Commands: /find /screenshot /web /status /brief /reminders /help (`tools.py`)
- [x] Notifications while the app is open
- [ ] Real-phone test over Wi-Fi / Tailscale (owner to try)
- [ ] HTTPS via Tailscale, for install and notifications outside the home network
- [ ] Web push for notifications when the app is closed

## Phase 7 — Reminders and daily brief  [x]
- [x] Plain-words parser ("remind me … Friday 5pm") (`reminders.py`)
- [x] Reminder store, popups with snooze, weekly timetable (typed or from a photo)
- [x] Morning brief at a set time (laptop window + phone)

## Phase 8 — Internship kit  [x] (built; **not yet on the laptop / not committed**)
- [x] Skills coverage vs a job description (`internship.py`)
- [x] Tailor resume bullets (only rewrites of text that is really in the resume); save as a new .docx
- [x] Cover letter and interview-question prompts
- [x] Application tracker
- [x] UI window (`ui/internship_view.py`)
- [x] Fixes: "+ File" text button, Quick Ask thread fix, Settings model radio sync
- [ ] **Deliver to laptop and commit** (see RUNBOOK.md)

## Next up (in this order)
1. [ ] **Deliver Phase 8** to the laptop, run tests there, `git add -A`, commit.
2. [ ] **Attach a whole folder** (owner hit this: the picker only takes files).
   - "+ Folder" button and pasted folder path in `attachments.py` / `composer.py` / `app.py`
   - go through `AccessGate.check` for every file; skip secrets, binaries, `node_modules`, `.git`, `venv`
   - include a tree listing + most relevant files within the context budget (`build_message`, `_fair_shares`)
   - tell the user what was included / skipped
3. [ ] **Tell the model what the app can do** with files, in a system note, so it stops saying
       "I don't have access to your files" (see DECISIONS.md → capabilities note).
4. [ ] Optional: use OS accessibility trees (idea from `vercel-labs/agent-desktop`) to read the active app in
       Quick Ask before falling back to OCR; later maybe "do this in an app" with step-by-step confirmation
       and the allowed-apps list. Needs the owner's go-ahead.
5. [ ] Owner studying: auto-routing and keeping data from leaking to Gemini/Groq (do not build until asked).
