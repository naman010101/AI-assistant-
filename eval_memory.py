"""Measure memory search with the real embedding model (needs Ollama running).

    venv\\Scripts\\python eval_memory.py

A fixed set of example session summaries and questions, each question
labelled with the summary it should find (or none). For several similarity
thresholds it reports:
  hit@1      the right summary came first
  recall     the right summary was among those sent (top 3 above threshold)
  false use  questions that should find nothing but got a summary anyway
Pick the threshold with high recall and few false uses, then set it in
settings.json ("memory" -> "threshold"). This uses example data only; none
of your real memory is read.
"""
import sys

import memory

SUMMARIES = {
    "pip": {"title": "Fixing pip after moving the venv",
            "bullets": ["pip.exe failed with 'unable to create process' after the project folder moved",
                        "fix: run venv\\Scripts\\python -m pip install instead"], "tags": ["python", "venv"]},
    "bfs": {"title": "BFS on a graph with a queue",
            "bullets": ["wrote breadth-first search in Python using collections.deque",
                        "discussed visited sets and time complexity O(V+E)"], "tags": ["dsa", "graphs"]},
    "resume": {"title": "Tailoring my resume for an ML internship",
               "bullets": ["rewrote project bullets to show impact", "added PyTorch and SQL to skills"],
               "tags": ["career", "internship"]},
    "router": {"title": "Embedding router evaluation",
               "bullets": ["keyword router got 51% accuracy", "planned a kNN classifier on nomic embeddings"],
               "tags": ["ml", "routing"]},
    "exam": {"title": "Planning for the DBMS mid-sem",
             "bullets": ["made a 5-day plan: normalisation, SQL joins, transactions",
                         "decided to practise previous year papers"], "tags": ["exam", "dbms"]},
    "ollama": {"title": "Setting up the vision model",
               "bullets": ["pulled qwen2.5vl:7b in Ollama", "set OLLAMA_CONTEXT_LENGTH to 8192 for the 6 GB GPU"],
               "tags": ["ollama", "setup"]},
}

QUESTIONS = [
    ("pip install is broken in my venv again, what did we do last time?", "pip"),
    ("Fatal error in launcher when running pip", "pip"),
    ("how do I install packages into my project environment?", "pip"),
    ("can you write DFS like the BFS we did?", "bfs"),
    ("what's the complexity of breadth first search again", "bfs"),
    ("help me write a cover letter for the ML internship", "resume"),
    ("which skills did we add to my CV?", "resume"),
    ("how accurate was my prompt router?", "router"),
    ("let's improve the classifier for routing prompts", "router"),
    ("what was my study plan for DBMS?", "exam"),
    ("quiz me on SQL joins for my exam", "exam"),
    ("the image model is slow, what context size did I set?", "ollama"),
    ("which vision model did I install?", "ollama"),
    ("what's the capital of Australia?", None),
    ("tell me a joke", None),
    ("translate good morning to French", None),
    ("what is 15% of 240", None),
    ("suggest a name for my cat", None),
]

THRESHOLDS = [0.45, 0.5, 0.55, 0.6, 0.65, 0.7]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    embed = memory.ollama_embedder()
    try:
        docs = embed([memory.summary_text(s) for s in SUMMARIES.values()], kind="document")
        qs = embed([q for q, _ in QUESTIONS], kind="query")
    except Exception as e:
        print(f"Couldn't reach the embedding model ({e}). Is Ollama running? Try: ollama pull {embed.model}")
        return
    keys = list(SUMMARIES)
    scored = [sorted(((memory.dot(q, d), k) for d, k in zip(docs, keys)), reverse=True) for q in qs]

    print(f"embedding model: {embed.model} · {len(QUESTIONS)} questions, {len(keys)} summaries\n")
    print(f"{'threshold':>9}  {'hit@1':>6}  {'recall':>6}  {'false use':>9}")
    best = None
    for th in THRESHOLDS:
        hit1 = recall = false_use = 0
        n_pos = sum(1 for _, want in QUESTIONS if want)
        n_neg = len(QUESTIONS) - n_pos
        for (q, want), ranked in zip(QUESTIONS, scored):
            sent = [k for sim, k in ranked[:3] if sim >= th]
            if want:
                hit1 += bool(sent) and sent[0] == want
                recall += want in sent
            else:
                false_use += bool(sent)
        score = recall / n_pos - false_use / n_neg
        if best is None or score > best[0]:
            best = (score, th)
        print(f"{th:>9.2f}  {hit1 / n_pos:>6.0%}  {recall / n_pos:>6.0%}  {false_use}/{n_neg:<7}")

    print(f"\nsuggested threshold: {best[1]:.2f}  (set it in settings.json -> memory -> threshold)")
    print("\nclosest summary per question:")
    for (q, want), ranked in zip(QUESTIONS, scored):
        sim, k = ranked[0]
        mark = "ok " if (want == k) else ("-- " if want is None else "XX ")
        print(f"  {mark}{sim:.2f} {k:<7} {q[:60]}")


if __name__ == "__main__":
    main()
