"""Deterministic confidence from measurable signals - never a model's self-reported number.

score = 0.35 * best reranker relevance      (is the top evidence on point?)
      + 0.25 * query-term coverage          (does the evidence address the whole question?)
      + 0.15 * authority of cited sources   (how authoritative is what we relied on?)
      + 0.25 * mean claim support           (how literally do answer sentences match evidence?)

Relevance and claim support carry the most weight because they are the strongest
predictors of a correct grounded answer; authority is a tiebreaker-level signal.
Failed validation or an unresolved conflict forces ABSTAIN regardless of the score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.models.enums import ConfidenceLevel

_W_RELEVANCE = 0.35
_W_COVERAGE = 0.25
_W_AUTHORITY = 0.15
_W_SUPPORT = 0.25


@dataclass
class ConfidenceAssessment:
    level: ConfidenceLevel
    score: float
    components: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level.value, "score": round(self.score, 4), "components": self.components}


def assess_confidence(
    settings: Settings,
    *,
    top_relevance: float,
    coverage: float,
    authority_weight: float,
    claim_support: float,
    validation_passed: bool,
    unresolved_conflict: bool,
) -> ConfidenceAssessment:
    components = {
        "relevance": round(min(1.0, top_relevance), 4),
        "coverage": round(coverage, 4),
        "authority": round(authority_weight, 4),
        "claim_support": round(claim_support, 4),
    }
    score = (
        _W_RELEVANCE * components["relevance"]
        + _W_COVERAGE * coverage
        + _W_AUTHORITY * authority_weight
        + _W_SUPPORT * claim_support
    )
    if not validation_passed or unresolved_conflict:
        level = ConfidenceLevel.ABSTAIN
    elif score >= settings.confidence_high_threshold:
        level = ConfidenceLevel.HIGH
    elif score >= settings.confidence_medium_threshold:
        level = ConfidenceLevel.MEDIUM
    else:
        level = ConfidenceLevel.LOW
    return ConfidenceAssessment(level, score, components)
