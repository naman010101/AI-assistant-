# Hybrid AI — rules for Claude Code

Hybrid AI is a private, local-first Windows desktop assistant:
Python, CustomTkinter, Ollama. Entry point app.py. Data lives in
%LOCALAPPDATA%\HybridAI (chats.db, memory.db, reminders.db).
The v2 plan is agent/V2_PLAN.md; the steps are agent/V2_STEPS.md.
Architect designs are saved in agent/designs/<step id>.md.

## Rules
- Do one step at a time, exactly as written. Ask before going beyond it.
- Windows only. No new dependency without asking me first.
- Every file read for the model goes through access.AccessGate.
- Never delete or migrate user data without a backup first.
- Worker threads talk to the UI only through the self.events queue.
- Write or update a test for each change. Run pytest before saying done.
- One commit per step, message: "v2 <step id>: <what and why>".
- After each phase, add a line to agent/LOG.md and record any choice
  in agent/DECISIONS.md.

## Model routing
- Design, security, data deletion or migration, AccessGate,
  phone server, tool loop -> architect agent first.
- Normal code once the design is agreed -> builder agent.
- Mechanical edits (renames, logging swaps, config, moving
  colours) -> mechanic agent.
- End of every phase -> reviewer agent checks the diff against
  the phase's "Done when" list before the merge.
