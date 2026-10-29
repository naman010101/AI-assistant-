"""Measure how well the router classifies prompts.

    venv\\Scripts\\python eval_router.py           # keywords vs hybrid on the TEST set
    venv\\Scripts\\python eval_router.py --loo     # + leave-one-out on the SEED examples

What the numbers mean
  accuracy   share of TEST prompts given the right category
  recall     per category: of the prompts that really were X, how many we caught
  confusion  rows = true category, columns = predicted category
  latency    time to classify one prompt (embedding call + kNN)

Leave-one-out: classify each SEED example using all the *other* examples.
It's a cheap way to get a bigger test set and to spot examples that look
like a different category (often a sign the example itself is ambiguous).
"""
import argparse
import statistics
import sys
import time

import router
from classifier import _dot
from routing_data import SEED, TEST

LABELS = ["simple", "code", "complex", "private"]


def evaluate(name, classify):
    preds, times, hows = [], [], []
    for _label, prompt in TEST:
        t0 = time.perf_counter()
        out = classify(prompt)
        times.append((time.perf_counter() - t0) * 1000)
        pred, how = out if isinstance(out, tuple) else (out, "")
        preds.append(pred)
        hows.append(how)

    truth = [label for label, _ in TEST]
    correct = sum(p == t for p, t in zip(preds, truth))
    print(f"\n=== {name} ===")
    print(f"accuracy: {correct}/{len(TEST)} = {correct / len(TEST):.1%}")
    print("recall:   " + "   ".join(
        f"{lab} {sum(p == t == lab for p, t in zip(preds, truth))}/{truth.count(lab)}"
        for lab in LABELS if lab in truth))
    print(f"latency:  median {statistics.median(times):.1f} ms · "
          f"max {max(times):.1f} ms")

    print("\nconfusion (rows = true, cols = predicted)")
    print(" " * 10 + "".join(f"{lab[:7]:>9}" for lab in LABELS))
    for t_lab in LABELS:
        row = [sum(t == t_lab and p == p_lab for p, t in zip(preds, truth)) for p_lab in LABELS]
        print(f"{t_lab:>9} " + "".join(f"{n:>9}" for n in row))

    if any(hows):
        print("\ndecided by:")
        kinds = {}
        for h in hows:
            k = h.split(" ")[0] if h else "?"
            kinds[k] = kinds.get(k, 0) + 1
        for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<12} {n}")

    wrong = [(t, p, prompt, h) for (t, prompt), p, h in zip(TEST, preds, hows) if p != t]
    if wrong:
        print("\nmisroutes:")
        for t, p, prompt, h in wrong:
            print(f"  [{t} → {p}] {prompt[:70]}" + (f"\n      ({h})" if h else ""))

    cloud = sum(router.POLICY["auto"][p].startswith("cloud") for p in preds)
    print(f"\nin Auto mode {cloud}/{len(preds)} prompts would go to the cloud")
    return correct / len(TEST)


def leave_one_out(clf):
    vecs, ex = clf._vectors, clf.examples
    correct, wrong = 0, []
    for i, (label, text) in enumerate(ex):
        scored = sorted(((_dot(vecs[i], v), j) for j, v in enumerate(vecs) if j != i), reverse=True)
        votes = {}
        for sim, j in scored[: clf.k]:
            votes[ex[j][0]] = votes.get(ex[j][0], 0.0) + max(sim, 0.0)
        pred = max(votes, key=votes.get)
        if pred == label:
            correct += 1
        else:
            wrong.append((label, pred, text))
    print(f"\n=== leave-one-out on SEED ===\naccuracy: {correct}/{len(ex)} = {correct / len(ex):.1%}")
    for label, pred, text in wrong:
        print(f"  [{label} → {pred}] {text[:70]}")


def main():
    # Windows consoles/redirects may not be UTF-8; don't crash on arrows and quotes.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--loo", action="store_true", help="also run leave-one-out on SEED")
    args = ap.parse_args()

    overlap = {p for _, p in TEST} & {t for ts in SEED.values() for t in ts}
    if overlap:
        print(f"WARNING: {len(overlap)} TEST prompts also appear in SEED — accuracy will be inflated")

    base = evaluate("keywords (original router)", router.classify_keywords)

    clf = router.get_classifier()
    t0 = time.perf_counter()
    ok = clf.warmup()
    print(f"\nembedding model: {clf.model_name} · warmup {(time.perf_counter() - t0):.1f} s")
    if not ok:
        print(f"embeddings unavailable: {clf.last_error}")
        print("Is Ollama running, and did you run:  ollama pull " + clf.model_name)
        return

    new = evaluate("hybrid (rules + embeddings + keyword fallback)", router.classify_prompt)
    print(f"\nchange vs keywords: {new - base:+.1%}")
    if args.loo:
        leave_one_out(clf)


if __name__ == "__main__":
    main()
