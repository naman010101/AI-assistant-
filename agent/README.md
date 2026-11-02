# Agent handoff — start here

Read **this folder only** to pick up work. You should not need to read the whole project.

| File | What it is | When to read |
|---|---|---|
| `README.md` | This index + working rules | Always, first |
| `PLAN.md` | Implementation plan, every phase and task with a checkbox | To see what is done / what is next |
| `MAP.md` | One line per source file: what it does, key functions | Before touching code, to find the right file |
| `DECISIONS.md` | Choices the owner made, and rules that must not be broken | Before designing anything |
| `RUNBOOK.md` | How to run, test, deliver to the laptop, commit | Before running or shipping |
| `LOG.md` | Dated list of what each session did (newest at the bottom) | Last; append to it when you finish |

## Project in three lines
Hybrid AI: a private, local-first Windows desktop AI assistant (Python + CustomTkinter, Ollama local models,
optional Gemini/Groq). Owner: Naman Saluja. Repo: `naman010101/AI-assistant-`, folder `C:\Local_ai_model\hybrid-ai`.

## Working rules (from the owner)
1. **Make a plan before implementing** anything non-trivial, and ask before starting if something is unclear.
2. **Keep the owner updated** step by step; short messages in plain words.
3. **Manual model choice only.** No automatic routing between local and cloud (owner will study that later).
4. **Private data never goes in the project folder** — it lives in `%LOCALAPPDATA%\HybridAI`.
5. **The assistant never changes a file without a diff the owner approves**, and always makes a backup.
6. After finishing a task: tick it in `PLAN.md`, add a line to `LOG.md`, update `MAP.md` if files changed, run the tests.
7. Git commits stay local; **the owner runs `git push`** (the Claude GitHub app is read-only).
