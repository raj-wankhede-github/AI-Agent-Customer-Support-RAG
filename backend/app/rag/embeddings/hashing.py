"""Offline, deterministic feature-hashing embeddings.

Features: stemmed content terms, adjacent-term bigrams and character 4-grams (which give
some robustness to morphology and typos), signed-hashed into a fixed dimension and L2
normalized. This is a lexical representation - it does not understand paraphrases the
way a neural embedding model does - and exists so the full pipeline runs, and CI tests
run, without an external API. Use a hosted embedding provider in production.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from itertools import pairwise

from app.utils.text import content_terms

_TERM_WEIGHT = 1.0
_BIGRAM_WEIGHT = 0.5
_CHARGRAM_WEIGHT = 0.2


class HashingEmbeddingProvider:
    name = "hashing"

    def __init__(self, dimension: int, model: str = "hashing-v1") -> None:
        self.dimension = dimension
        self.model = model

    def _features(self, text: str) -> dict[str, float]:
        terms = content_terms(text)
        features: defaultdict[str, float] = defaultdict(float)
        for term in terms:
            features[f"t:{term}"] += _TERM_WEIGHT
            if len(term) >= 5:
                padded = f"<{term}>"
                for i in range(len(padded) - 3):
                    features[f"c:{padded[i : i + 4]}"] += _CHARGRAM_WEIGHT
        for left, right in pairwise(terms):
            features[f"b:{left}_{right}"] += _BIGRAM_WEIGHT
        return features

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for feature, weight in self._features(text).items():
            digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            index = digest % self.dimension
            sign = 1.0 if (digest >> 63) & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(weight)) if weight >= 1 else sign * weight
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            # A zero vector has undefined cosine distance in pgvector; use a tiny constant.
            vector[0] = 1e-6
            return vector
        return [v / norm for v in vector]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
