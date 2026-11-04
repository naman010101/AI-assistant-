# Session log (newest last)

- Phases 0–3: private data folder, manual models, access gate, attachments, edit-with-diff, memory from summaries. Committed.
- Consistency work: same fitted context for all models, rolling digest, fact confirmation. Committed.
- README written, GitHub linked (remote `naman010101/AI-assistant-`; push done by owner).
- Phases 4–7: code explainer, Quick Ask, phone server + PWA, reminders + brief. Committed (last commit: "Phase 7").
- Phase 8: internship kit built + tested in the cloud workspace; "+ File" button, Quick Ask thread fix, inspector fix. NOT yet on laptop.
- Answered: folder access rules; why chat data was not readable by the agent (it lives in AppData); agent-desktop comparison.
- Owner found: picker cannot attach a whole folder; model says it has no file access. → "Next up" items 2–3 in PLAN.md.
- 2026-11-02: created this `agent/` folder so the next agent can start without reading the whole project.

## 2026-11-04: v2 baseline (Phase 0, after step 0.5b)

- pytest (`python -m coverage run --source=. -m pytest tests -q -rs`): **65 passed, 2 skipped, 0 failed**.
- Skipped tests:
  - `tests/test_core.py:202`: reportlab is only used to build this test PDF and is not in requirements.txt
  - `tests/test_quickask.py:39`: Windows
- No failing tests.
- ruff (E/F rules, project config in pyproject.toml): **52 findings**.

Per-module coverage (`python -m coverage report --omit="venv/*,.claude/*,tests/*,*_backup.py"`):

| Module | Stmts | Miss | Cover |
|---|---:|---:|---:|
| access.py | 71 | 5 | 93% |
| app.py | 1279 | 1279 | 0% |
| attachments.py | 264 | 58 | 78% |
| classifier.py | 97 | 23 | 76% |
| codeexplain.py | 373 | 42 | 89% |
| config.py | 62 | 13 | 79% |
| context.py | 90 | 17 | 81% |
| editor.py | 198 | 19 | 90% |
| eval_memory.py | 41 | 41 | 0% |
| eval_router.py | 82 | 82 | 0% |
| log_setup.py | 22 | 0 | 100% |
| memory.py | 227 | 28 | 88% |
| memory_check.py | 29 | 29 | 0% |
| phone.py | 410 | 338 | 18% |
| quickask.py | 235 | 131 | 44% |
| reminders.py | 299 | 34 | 89% |
| router.py | 147 | 57 | 61% |
| routing_data.py | 2 | 0 | 100% |
| settings.py | 63 | 0 | 100% |
| storage.py | 83 | 13 | 84% |
| tools.py | 146 | 100 | 32% |
| `ui\__init__.py` | 0 | 0 | 100% |
| `ui\chat_view.py` | 252 | 252 | 0% |
| `ui\code_view.py` | 298 | 298 | 0% |
| `ui\composer.py` | 88 | 88 | 0% |
| `ui\dialogs.py` | 41 | 41 | 0% |
| `ui\diff_view.py` | 118 | 118 | 0% |
| `ui\inspector.py` | 312 | 312 | 0% |
| `ui\memory_view.py` | 206 | 206 | 0% |
| `ui\quickask_view.py` | 152 | 152 | 0% |
| `ui\reminders_view.py` | 241 | 241 | 0% |
| `ui\sidebar.py` | 110 | 110 | 0% |
| `ui\theme.py` | 37 | 37 | 0% |
| **TOTAL** | **6075** | **4164** | **31%** |
