"""Text embeddings for pgvector. Uses a NIM embedding model when configured; otherwise a deterministic
hashed bag-of-words/bigram embedding so the whole project runs offline (same 1024 dims either way).
NOTE: don't mix providers inside one table - reload notes if you switch."""
from __future__ import annotations

import hashlib
import re

import numpy as np

from .nim import NIMClient

EMBED_DIM = 1024


def hash_embed(text: str) -> np.ndarray:
    toks = re.findall(r"[a-z0-9\$]+", text.lower())
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    for f in feats:
        h = int(hashlib.md5(f.encode()).hexdigest(), 16)
        v[h % EMBED_DIM] += 1.0 if (h >> 64) & 1 else -1.0
    n = np.linalg.norm(v)
    return v / n if n else v


def embed(texts: list[str], kind: str = "passage", client: NIMClient | None = None) -> np.ndarray:
    """kind: 'passage' for stored notes, 'query' for search strings."""
    client = client if client is not None else NIMClient.from_env()
    if client is not None:
        try:
            arr = np.array(client.embed(texts, kind), dtype=np.float32)
            if arr.shape[1] == EMBED_DIM:
                return arr / np.linalg.norm(arr, axis=1, keepdims=True)
        except Exception as exc:  # network / quota / model mismatch -> degrade gracefully
            print(f"[embeddings] NIM embed failed ({exc}); falling back to hashed embeddings")
    return np.stack([hash_embed(t) for t in texts])
