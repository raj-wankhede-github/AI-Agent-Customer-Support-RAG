"""Deterministic contradiction detection and authority/effective-date resolution.

Two evidence sentences from *different documents* conflict when they talk about the same
thing (similar surrounding wording that overlaps the question) but state different
quantities of the same kind (days, money, percentages...). Conflicts are never resolved
by picking arbitrarily: a higher-authority source wins; for equal authority the most
recent already-effective source wins; otherwise the conflict stays unresolved and the
agent abstains and escalates.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.config import Settings
from app.rag.types import RetrievedChunk
from app.utils.text import jaccard, normalize_unicode, split_sentences, term_set

_QUANTITY = re.compile(
    r"(?P<money>[$€£]\s?\d[\d,]*(?:\.\d+)?)"
    r"|(?P<percent>\d+(?:\.\d+)?\s?(?:%|percent))"
    r"|(?P<duration>\d+(?:\s?-\s?\d+)?\s+(?:business\s+|calendar\s+|working\s+)?"
    r"(?P<unit>days?|weeks?|months?|years?|hours?|minutes?))",
    re.IGNORECASE,
)


@dataclass
class _Fact:
    chunk: RetrievedChunk
    family: str
    value: str
    context: set[str]


@dataclass
class Conflict:
    document_ids: list[uuid.UUID]
    family: str
    values: dict[str, str]
    sentences: dict[str, str]
    resolved: bool = False
    winner_document_id: uuid.UUID | None = None
    resolution: str | None = None
    losers: list[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_ids": [str(d) for d in self.document_ids],
            "kind": self.family,
            "values": self.values,
            "sentences": self.sentences,
            "resolved": self.resolved,
            "winner_document_id": str(self.winner_document_id) if self.winner_document_id else None,
            "resolution": self.resolution,
        }


def _facts(chunk: RetrievedChunk, key_terms: set[str]) -> list[_Fact]:
    facts = []
    for sentence in split_sentences(chunk.content):
        matches = list(_QUANTITY.finditer(normalize_unicode(sentence)))
        if not matches:
            continue
        context = term_set(_QUANTITY.sub(" ", normalize_unicode(sentence)))
        context = {t for t in context if not t.isdigit()}
        if not context & key_terms:
            continue
        for match in matches:
            if match.group("money"):
                family = "money"
            elif match.group("percent"):
                family = "percent"
            else:
                family = "duration:" + match.group("unit").lower().rstrip("s")
            value = re.sub(r"\s+", " ", match.group(0).lower().replace(",", ""))
            facts.append(_Fact(chunk, family, value, context))
    return facts


def detect_conflicts(chunks: list[RetrievedChunk], key_terms: list[str], settings: Settings) -> list[Conflict]:
    keys = set(key_terms)
    facts = [fact for chunk in chunks for fact in _facts(chunk, keys)]
    conflicts: dict[tuple[uuid.UUID, uuid.UUID, str], Conflict] = {}
    for i, a in enumerate(facts):
        for b in facts[i + 1 :]:
            if a.chunk.document_id == b.chunk.document_id or a.family != b.family:
                continue
            if a.value == b.value or jaccard(a.context, b.context) < settings.conflict_sentence_similarity:
                continue
            ids = sorted([a.chunk.document_id, b.chunk.document_id], key=str)
            key = (ids[0], ids[1], a.family)
            if key not in conflicts:
                conflicts[key] = Conflict(
                    document_ids=ids,
                    family=a.family,
                    values={a.chunk.document_title: a.value, b.chunk.document_title: b.value},
                    sentences={
                        a.chunk.document_title: a.chunk.content[:300],
                        b.chunk.document_title: b.chunk.content[:300],
                    },
                )
    return list(conflicts.values())


def resolve_conflicts(
    conflicts: list[Conflict], chunks: list[RetrievedChunk], settings: Settings, today: date
) -> tuple[list[RetrievedChunk], list[Conflict]]:
    """Resolve each conflict if metadata allows; drop losing documents from the evidence."""
    by_document = {c.document_id: c for c in chunks}
    ranks = settings.authority_ranks()
    losers: set[uuid.UUID] = set()
    for conflict in conflicts:
        a, b = (by_document[d] for d in conflict.document_ids)
        rank_a, rank_b = ranks.get(a.authority, 0), ranks.get(b.authority, 0)
        winner: RetrievedChunk | None = None
        if a.effective_date and a.effective_date > today and not (b.effective_date and b.effective_date > today):
            winner, conflict.resolution = b, "other_source_not_yet_effective"
        elif b.effective_date and b.effective_date > today and not (a.effective_date and a.effective_date > today):
            winner, conflict.resolution = a, "other_source_not_yet_effective"
        elif rank_a != rank_b:
            winner = a if rank_a > rank_b else b
            conflict.resolution = "higher_source_authority"
        elif a.effective_date and b.effective_date and a.effective_date != b.effective_date:
            winner = a if a.effective_date > b.effective_date else b
            conflict.resolution = "more_recent_effective_date"
        if winner is not None:
            loser = b if winner is a else a
            conflict.resolved = True
            conflict.winner_document_id = winner.document_id
            conflict.losers = [loser.document_id]
            losers.add(loser.document_id)
    kept = [c for c in chunks if c.document_id not in losers]
    return kept, conflicts
