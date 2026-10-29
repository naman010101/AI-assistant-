"""Embedding-based prompt classifier (k-nearest neighbours).

How it works
  1. Every example prompt in routing_data.SEED is turned into an embedding:
     a vector (768 numbers for nomic-embed-text) where prompts with similar
     meaning end up pointing in similar directions.
  2. A new prompt is embedded the same way.
  3. We find the k most similar examples by cosine similarity and let them
     vote, weighted by similarity. The winning label's share of the vote is
     the confidence.

Why kNN and not a trained model? It needs no training step, you can fix a
misroute by adding one example, and it's easy to inspect ("it went to code
because it looked like 'write a regex…'"). A small PyTorch classifier on top
of the same embeddings is a good next experiment — eval_router.py lets you
compare the two fairly.

Embeddings come from Ollama (/api/embed), so there are no new Python
dependencies. Run once:  ollama pull nomic-embed-text
"""
import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("ROUTER_EMBED_MODEL", "nomic-embed-text")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

# Talk to Ollama directly, never through a system HTTP proxy.
_local = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _prefix(model):
    # nomic-embed-text is trained with task prefixes; using the right one helps.
    return "classification: " if "nomic" in model else ""


def _post(path, payload, timeout):
    req = urllib.request.Request(f"{OLLAMA_URL}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with _local.open(req, timeout=timeout) as r:
        return json.load(r)


def ollama_embed(texts, model=EMBED_MODEL, timeout=60):
    """Return one L2-normalised vector per text."""
    inputs = [_prefix(model) + t for t in texts]
    try:
        data = _post("/api/embed", {
            "model": model,
            "input": inputs,
            "keep_alive": "30m",  # keep the small embedding model loaded between prompts
        }, timeout)
        return [_normalise(v) for v in data["embeddings"]]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        detail = e.read().decode("utf-8", "replace")
        if "not found" in detail and "model" in detail:
            raise RuntimeError(f"Ollama has no model '{model}'. Run: ollama pull {model}") from None
    # Older Ollama versions (before 0.3) only have /api/embeddings, one text per call.
    return [_normalise(_post("/api/embeddings", {"model": model, "prompt": t}, timeout)["embedding"])
            for t in inputs]


def _normalise(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a, b):
    # Both vectors are normalised, so the dot product IS the cosine similarity.
    return sum(x * y for x, y in zip(a, b))


class EmbeddingClassifier:
    def __init__(self, examples, embed_fn=ollama_embed, model_name=EMBED_MODEL,
                 k=7, min_confidence=0.6, retry_after=60):
        self.examples = [(label, text) for label, texts in examples.items() for text in texts]
        self.embed_fn = embed_fn
        self.model_name = model_name
        self.k = k
        self.min_confidence = min_confidence
        self.retry_after = retry_after
        self._vectors = None
        self._lock = threading.Lock()
        self._failed_at = 0.0
        self.last_error = None

    # ---------- setup ----------
    @property
    def ready(self):
        return self._vectors is not None

    def warmup(self):
        """Embed the examples (or load them from cache). Safe to call from any thread."""
        with self._lock:
            if self._vectors is not None:
                return True
            if time.time() - self._failed_at < self.retry_after:
                return False  # Ollama was down a moment ago; don't hammer it
            try:
                self._vectors = self._load_cache() or self._embed_examples()
                self.last_error = None
                return True
            except Exception as e:
                self._failed_at = time.time()
                self.last_error = f"{type(e).__name__}: {e}"
                return False

    def _cache_path(self):
        h = hashlib.sha1(json.dumps([self.model_name, self.examples]).encode()).hexdigest()[:12]
        return os.path.join(CACHE_DIR, f"router_{self.model_name.replace(':', '_')}_{h}.json")

    def _load_cache(self):
        path = self._cache_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    def _embed_examples(self):
        vectors = self.embed_fn([text for _, text in self.examples])
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(self._cache_path(), "w", encoding="utf-8") as f:
            json.dump(vectors, f)
        return vectors

    # ---------- inference ----------
    def classify(self, text):
        """Return dict(label, confidence, nearest, similarity) or None if unavailable."""
        if not self.ready and not self.warmup():
            return None
        try:
            q = self.embed_fn([text])[0]
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

        scored = sorted(((_dot(q, v), i) for i, v in enumerate(self._vectors)), reverse=True)
        top = scored[: self.k]
        votes = {}
        for sim, i in top:
            label = self.examples[i][0]
            votes[label] = votes.get(label, 0.0) + max(sim, 0.0)
        label = max(votes, key=votes.get)
        total = sum(votes.values()) or 1.0
        best_sim, best_i = top[0]
        return {
            "label": label,
            "confidence": votes[label] / total,
            "confident": votes[label] / total >= self.min_confidence,
            "nearest": self.examples[best_i][1],
            "similarity": best_sim,
        }
