#
# SPDX-FileCopyrightText: 2026 AOT Technologies
# SPDX-License-Identifier: Apache-2.0
#
"""BM25 ranking for MCP tool-search mode.

Standard Okapi BM25 over one text document per tool (name, description,
argument names and descriptions). Pure stdlib; the same approach as FastMCP's
``BM25SearchTransform``. Queries are natural-language keywords from a model, so
relevance ranking matters more than exact matching.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Mapping

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens; ``snake_case`` and ``camelCase`` are split into words."""
    return _WORD.findall(_CAMEL.sub(" ", text).lower())


class Bm25Index:
    """Rank documents (keyed by tool name) against a keyword query."""

    def __init__(self, documents: Mapping[str, str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._terms: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        doc_freq: Counter[str] = Counter()
        for key, text in documents.items():
            tokens = tokenize(text)
            self._terms[key] = Counter(tokens)
            self._lengths[key] = len(tokens)
            doc_freq.update(set(tokens))
        count = len(documents)
        self._avg_length = (sum(self._lengths.values()) / count) if count else 0.0
        # BM25+ style idf floor: never negative, so a word present in most tools
        # still counts a little instead of penalising the match.
        self._idf = {
            term: math.log(1 + (count - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()
        }

    def search(self, query: str, *, limit: int) -> list[str]:
        """Keys of the best-matching documents, best first; only those matching a query word."""
        words = set(tokenize(query))
        scores: dict[str, float] = {}
        for key, terms in self._terms.items():
            score = 0.0
            length_norm = (
                1
                - self._b
                + self._b * (self._lengths[key] / self._avg_length if self._avg_length else 0.0)
            )
            for word in words:
                tf = terms.get(word, 0)
                if not tf:
                    continue
                score += self._idf[word] * tf * (self._k1 + 1) / (tf + self._k1 * length_norm)
            if score > 0:
                scores[key] = score
        ranked = sorted(scores, key=lambda k: (-scores[k], k))
        return ranked[: max(limit, 0)]
