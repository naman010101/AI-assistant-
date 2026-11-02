# Hybrid AI

A private, local-first AI assistant for Windows. It runs local models through
[Ollama](https://ollama.com) and can use cloud models (Gemini, Groq) when you
choose them. It reads and edits your files with diff approval, and remembers
past sessions as short summaries — with your data kept on your laptop.

## Features

- **You choose the model.** Llama 3.1 8B, Qwen 2.5 Coder 7B and Qwen 2.5 VL 7B
  run locally; Gemini 2.0 Flash and Groq Llama 3.3 70B are optional. Nothing
  leaves the laptop unless you pick a cloud model.
- **Private by design.** Chats, memory, settings, backups and API keys live in
  `%LOCALAPPDATA%\HybridAI`, never in the project folder. An access gate limits
  the folders the assistant may read and always blocks secrets (`.env`, keys,
  `.ssh`) and system folders.
- **Files and images.** Attach code, text, PDF, Word, Excel or Jupyter files
  with 📎, by pasting a full path, or paste a screenshot with Ctrl+V. Long files
  are cut to fit and you're told how much was included.
- **Safe editing.** Ask for a change; the assistant proposes a new version and
  you review a side-by-side diff before anything is written. Syntax errors,
  "rest of the code unchanged" placeholders and files changed on disk are
  flagged. Every edit is backed up and can be undone.
- **Memory.** Finished sessions are summarised by the local model and stored
  with embeddings (`nomic-embed-text`). Relevant past sessions are retrieved as
  you write. Facts about you are only used after you confirm them, and you can
  browse, edit or delete everything in the Memory window.
- **Consistent context.** Every model receives the same fitted history; in long
  chats, older messages are replaced by a rolling summary instead of being
  silently cut.
- **Code explainer.** Map a project (files, classes, functions, who calls
  what), explain what changed since your last review (great for code an AI
  wrote), explain a file or function at three depths, trace a feature, quiz
  yourself, or explain a traceback against your own code.
- **Quick Ask.** Press Ctrl+Shift+Space in any app: a popup that already has
  the window and your selected text. Explain, summarise, translate, rewrite
  or fix grammar, then copy the answer or paste it in place of the selection.
- **Phone app.** An installable Android app (PWA) that uses the laptop's
  models, chats and memory, runs laptop commands (`/find`, `/screenshot`,
  `/web`) and takes files from Android's Share menu.
- **Measured.** Unit tests, plus evaluation scripts for the prompt router
  (`eval_router.py`) and memory retrieval (`eval_memory.py`).

## Architecture

```
Desktop app (app.py + ui/)
        │
        ▼
Core modules ─ access.py        allowed folders, blocked secrets
             ─ attachments.py   read files and images, fit to the context
             ─ editor.py        proposed edits, checks, diff, backup, undo
             ─ memory.py        session summaries, facts, embedding search
             ─ context.py       same fitted context for every model
             ─ router.py        model picker, streaming, cloud → local fallback
             ─ storage.py       chats (SQLite)
        │
        ├──► Local models (Ollama)      ├──► Cloud models (only when picked)
        ▼
Private data: %LOCALAPPDATA%\HybridAI  — chats.db, memory.db, settings.json, .env, backups\
```

The UI runs on Tkinter's main thread; model calls and summaries run in worker
threads and send events back through a queue, so the window never freezes.

## Setup

Requirements: Windows 10/11, Python 3.11+, [Ollama](https://ollama.com),
a GPU with about 6 GB of memory for the vision model (text models run on less).

```powershell
git clone https://github.com/<you>/hybrid-ai.git
cd hybrid-ai
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt

ollama pull llama3.1:8b
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5vl:7b          # for images (optional)
ollama pull nomic-embed-text      # for memory
setx OLLAMA_CONTEXT_LENGTH 8192   # then restart Ollama
```

Optional cloud keys: create `%LOCALAPPDATA%\HybridAI\.env` with

```
GEMINI_API_KEY=...
GROQ_API_KEY=...
```

Start the app with `launch.bat` (no console window) or
`venv\Scripts\python app.py`.

Optional, for reading screen text in Quick Ask (Windows OCR):

```powershell
venv\Scripts\python -m pip install winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging winrt-Windows.Storage.Streams winrt-Windows.Globalization winrt-Windows.Foundation winrt-Windows.Foundation.Collections
```

### Phone

1. Desktop app → Settings → Phone → turn on **Let my phone connect**
   (allow it through the Windows firewall when asked).
2. Press **Pair a phone (QR)** and scan the code with your phone. On the same
   Wi-Fi this works straight away.
3. To use it anywhere and install it as an app, install
   [Tailscale](https://tailscale.com) on the laptop and the phone (same
   account), then on the laptop run `tailscale serve --bg 8765` and paste the
   `https://….ts.net` address it prints into Settings → Phone. Pair again from
   that address, then in Chrome: ⋮ → **Install app**. Hybrid AI now appears in
   Android's Share menu.
4. Keep the laptop awake while plugged in (Windows power settings).

## Project layout

| File | What it does |
| --- | --- |
| `app.py` | Main window, sending, streaming, sessions, memory and edit flows |
| `ui/` | Sidebar, chat view, composer, inspector panel, diff and memory windows |
| `router.py` | Models, manual selection, streaming, fallback, what each model receives |
| `context.py` | Fitting history to the window; rolling digest of long chats |
| `memory.py` | Summaries, facts, embeddings, retrieval |
| `attachments.py` | Readers for text/code/PDF/Word/Excel/notebooks/images; pasted paths |
| `editor.py` | Edit proposals, safety checks, diffs, apply with backup, undo |
| `access.py` | Allowed folders and always-blocked locations |
| `storage.py`, `settings.py`, `config.py` | Chats in SQLite, settings, private data folder |
| `codeexplain.py`, `ui/code_view.py` | Project map, changes since last review, explain/trace/quiz prompts |
| `quickask.py`, `ui/quickask_view.py` | Global hotkey, window capture, selection, OCR, popup |
| `phone.py`, `tools.py`, `mobile/` | Phone server (inside the app), laptop commands, the phone app |
| `classifier.py`, `routing_data.py`, `eval_router.py` | Embedding prompt router (kept for study; the app uses manual choice) |
| `eval_memory.py`, `memory_check.py` | Memory retrieval eval; privacy-safe health check |
| `tests/` | `venv\Scripts\python -m pytest tests -q` |

## Privacy

- Personal data is stored only under `%LOCALAPPDATA%\HybridAI`.
- Files, images and memory go to cloud models only if you pick a cloud model,
  and attachments ask for confirmation first (configurable).
- `.gitignore` excludes `.env`, databases and `venv`.

## Roadmap

- [x] Private data folder, manual model choice, access control
- [x] Attachments and images
- [x] File editing with diff approval
- [x] Memory from session summaries
- [x] Code explainer for AI-written code
- [x] Quick Ask popup with screen and selected-text context
- [x] Android companion app
- [ ] Reminders and daily brief
- [ ] Internship kit

## For AI agents

Start with [`agent/README.md`](agent/README.md): plan with checkboxes, file map, decisions, runbook and session log.
