"""Embedding providers.

The default is a deterministic hashing embedder: no model download, no network, identical
results across machines. It is genuinely useful for lexical/near-duplicate retrieval and
keeps the whole memory stack testable offline (§58). Swap in a real model in production by
changing ``THURSDAY_EMBEDDING_BACKEND``.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

_TOKEN = re.compile(r"[\w฀-๿]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    # Thai has no spaces between words; character trigrams give the hash space something
    # to bite on for Thai input without pulling in a segmenter.
    thai = [t for t in tokens if any("฀" <= ch <= "๿" for ch in t)]
    for word in thai:
        tokens.extend(word[i : i + 3] for i in range(max(1, len(word) - 2)))
    return tokens


class HashEmbeddingProvider:
    """Bag-of-tokens hashed into a fixed vector, L2-normalised."""

    name = "hash"
    local = True

    def __init__(self, dimensions: int = 256) -> None:
        self.dimensions = dimensions

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _tokenize(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


class OllamaEmbeddingProvider:
    """Local embedding model over Ollama's HTTP API."""

    name = "ollama"
    local = True

    def __init__(self, url: str, model: str = "nomic-embed-text", dimensions: int = 768) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            out: list[list[float]] = []
            for text in texts:
                response = await client.post(
                    f"{self.url}/api/embeddings", json={"model": self.model, "prompt": text}
                )
                response.raise_for_status()
                out.append(response.json()["embedding"])
            return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
