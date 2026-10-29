"""Check that memory is working, without showing what's in it.

    venv\\Scripts\\python memory_check.py

Prints only counts and yes/no answers (no summaries, no facts), so the output
is safe to share when asking for help.
"""
import sys

import config
import memory


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"memory file: {'found' if __import__('os').path.exists(config.MEMORY_DB) else 'NOT FOUND'}")
    mem = memory.MemoryStore(config.MEMORY_DB)
    sums = mem.summaries()
    facts = mem.facts()
    print(f"summaries: {len(sums)} · searchable (have an embedding): {sum(1 for s in sums if s['vector'])}")
    print(f"facts: {len(facts)} ({sum(1 for f in facts if f['kind'] == 'user')} about you, "
          f"{sum(1 for f in facts if f['pinned'])} pinned) · sent with every message: {len(mem.user_facts())}")
    embed = memory.ollama_embedder()
    try:
        embed(["test"], kind="query")
        print(f"embedding model ({embed.model}): working")
    except Exception as e:
        print(f"embedding model ({embed.model}): NOT working: {type(e).__name__}: {e}")
        print(f"  fix: ollama pull {embed.model}")
        return
    missing = sum(1 for s in sums if not s["vector"])
    if missing:
        n = mem.embed_missing(embed, embed.model)
        print(f"indexed {n} summaries that weren't searchable yet")
    for q in ("what's my name?", "do you remember our last session?", "what did we work on?"):
        scored = mem.search(q, embed, k=3, threshold=None)
        best = f"{scored[0][0]:.2f}" if scored else "-"
        print(f"  '{q}': best similarity {best} · recall question: {memory.is_recall_question(q)}")


if __name__ == "__main__":
    main()
