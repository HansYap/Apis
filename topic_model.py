"""Apis' topic matcher: a small multilingual embedding model that runs on this computer.

It compares each vault topic (its name plus the sentences around its earlier links) with
passages of the transcript and reports how close the best passage is. The chat model makes
the final call; this only narrows the suggestions down. It runs as its own process with its
own packages (fastembed + onnxruntime), so the app window never loads them.

    python topic_model.py --download   fetch the model once (the only time it goes online)
    python topic_model.py < job.json    score {"topics": [{"name", "text"}], "chunks": [{"time", "text"}]}
"""

import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
HOME = ROOT / ".topic-model"
PACKAGES = HOME / "packages"
MODELS = HOME / "models"
CACHE = HOME / "topic-vectors.json"
READY = HOME / "ready"
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FASTEMBED = "fastembed==0.9.1"


def installed():
    return (PACKAGES / "fastembed" / "__init__.py").is_file() and READY.is_file() and READY.read_text().strip() == MODEL


def worker_env(online=False):
    """Only the matcher's own packages, and no network unless it is downloading the model."""
    env = dict(os.environ, PYTHONPATH=str(PACKAGES), HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    env.pop("PYTHONHOME", None)
    if not online:
        env["HF_HUB_OFFLINE"] = "1"
    return env


def load_model():
    from fastembed import TextEmbedding
    threads = max(1, (os.cpu_count() or 2) - 1)  # Leave a core for the window.
    return TextEmbedding(MODEL, cache_dir=str(MODELS), threads=threads)


def unit_rows(vectors):
    import numpy as np
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1, norms)


def topic_vectors(model, texts):
    """Topic vectors, reusing the ones from earlier runs; topics change far less often than transcripts."""
    keys = [hashlib.sha1(f"{MODEL}\n{text}".encode("utf-8")).hexdigest() for text in texts]
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    missing = [index for index, key in enumerate(keys) if key not in cache]
    if missing:
        for index, vector in zip(missing, model.embed([texts[index] for index in missing])):
            cache[keys[index]] = [round(float(value), 5) for value in vector]
    kept = {key: cache[key] for key in keys}  # Forget topics the vault no longer links.
    if missing or len(kept) != len(cache):
        HOME.mkdir(exist_ok=True)
        temporary = CACHE.with_suffix(".tmp")
        temporary.write_text(json.dumps(kept), encoding="utf-8")
        temporary.replace(CACHE)
    return [kept[key] for key in keys]


def score(job):
    topics, chunks = job.get("topics") or [], job.get("chunks") or []
    if not topics or not chunks:
        return []
    model = load_model()
    topic_matrix = unit_rows(topic_vectors(model, [topic["text"] for topic in topics]))
    chunk_matrix = unit_rows(list(model.embed([chunk["text"] for chunk in chunks])))
    similarity = topic_matrix @ chunk_matrix.T
    best = similarity.argmax(axis=1)
    return [{"name": topic["name"], "score": float(similarity[index, best[index]]), "chunk": int(best[index])}
            for index, topic in enumerate(topics)]


def main():
    if sys.argv[1:] == ["--download"]:
        list(load_model().embed(["Apis is ready."]))  # Downloads the model, then proves it runs.
        READY.write_text(MODEL, encoding="utf-8")
        return {"ready": True}
    return {"scores": score(json.loads(sys.stdin.read() or "{}"))}


if __name__ == "__main__":
    try:
        result = main()
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
    sys.exit(1 if "error" in result else 0)
