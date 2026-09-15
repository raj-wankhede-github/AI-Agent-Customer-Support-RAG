"""Answer validation: citation validity and claim-level grounding.

Deterministic checks run for every answer, whatever generated it:
- the answer cites at least one source and only sources that were actually retrieved;
- each claim is lexically supported by the sources it cites;
- each answer sentence is supported by the cited evidence, including every number,
  date (month names) and named entity / email / URL / phone number it contains;
- the answer does not echo the system-prompt canary.

When an LLM is configured, an LLM judge additionally checks every claim against its
cited sources. Either layer failing fails validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.prompts import get_prompt
from app.llm.structured import StructuredLLM
from app.rag.prompt_builder import build_judge_prompt
from app.rag.types import EvidenceItem, GeneratedAnswer
from app.utils.text import extract_numbers, jaccard, normalize_unicode, split_sentences, term_set

log = structlog.get_logger(__name__)

_MONTHS = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", re.I
)
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+|\bwww\.\S+|\+?\d[\d\s().-]{7,}\d")
_CAPITALIZED = re.compile(r"\b[A-Z][a-zA-Z0-9&'-]*[a-zA-Z0-9]\b")
_CITATION_MARKER = re.compile(r"\[(?:S\d+(?:,\s*)?)+\]")
_ALWAYS_OK = frozenset({"I", "You", "Your", "We", "Our", "Yes", "No", "Please", "Note", "However", "If", "For"})


@dataclass
class ValidationIssue:
    kind: str
    detail: str
    sentence: str | None = None


@dataclass
class ValidationResult:
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    claim_support: float = 0.0
    judge: dict[str, Any] | None = None

    def feedback(self) -> list[str]:
        return [f"{i.kind}: {i.detail}" + (f' (sentence: "{i.sentence}")' if i.sentence else "") for i in self.issues]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "claim_support": round(self.claim_support, 4),
            "issues": [{"kind": i.kind, "detail": i.detail, "sentence": i.sentence} for i in self.issues],
            "judge": self.judge,
        }


def _evidence_text(items: list[EvidenceItem]) -> str:
    return "\n".join(
        f"{i.chunk.document_title}\n{i.chunk.heading_path or ''}\n{i.chunk.product or ''}\n{i.chunk.content}"
        for i in items
    )


class GroundingValidator:
    def __init__(self, settings: Settings, canary: str, known_entities: set[str] | None = None) -> None:
        self.settings = settings
        self.canary = canary
        self.known_entities = {e.lower() for e in (known_entities or set())}

    def validate(self, answer: GeneratedAnswer, evidence: dict[str, EvidenceItem]) -> ValidationResult:
        issues: list[ValidationIssue] = []
        min_support = self.settings.grounding_min_claim_support
        text = _CITATION_MARKER.sub("", answer.answer).strip()

        if self.canary in answer.answer or any(self.canary in c.text for c in answer.claims):
            return ValidationResult(
                False, [ValidationIssue("prompt_leak", "Answer echoed confidential system instructions")]
            )
        if not text:
            issues.append(ValidationIssue("empty_answer", "Answer text is empty"))
        if not answer.claims:
            issues.append(ValidationIssue("no_citations", "Answer has no cited claims"))

        cited_ids: list[str] = []
        supports: list[float] = []
        for claim in answer.claims:
            unknown = [eid for eid in claim.evidence_ids if eid not in evidence]
            if unknown:
                issues.append(
                    ValidationIssue("unknown_citation", f"Cites sources that were not retrieved: {unknown}", claim.text)
                )
            known = [evidence[eid] for eid in claim.evidence_ids if eid in evidence]
            if not known:
                issues.append(ValidationIssue("uncited_claim", "Claim cites no retrieved source", claim.text))
                continue
            cited_ids.extend(eid for eid in claim.evidence_ids if eid in evidence)
            support = self._support(claim.text, _evidence_text(known))
            supports.append(support)
            if support < min_support:
                issues.append(
                    ValidationIssue(
                        "claim_not_supported_by_citation",
                        f"Only {support:.0%} of the claim's key words appear in its cited sources",
                        claim.text,
                    )
                )

        cited = [evidence[eid] for eid in dict.fromkeys(cited_ids)]
        if cited and text:
            issues.extend(self._check_sentences(text, answer, cited, evidence, supports))

        claim_support = min(supports) if supports else 0.0
        return ValidationResult(passed=not issues, issues=issues, claim_support=claim_support)

    def _check_sentences(
        self,
        text: str,
        answer: GeneratedAnswer,
        cited: list[EvidenceItem],
        evidence: dict[str, EvidenceItem],
        supports: list[float],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        all_cited_text = _evidence_text(cited)
        for sentence in split_sentences(text):
            terms = term_set(sentence)
            numbers = extract_numbers(sentence)
            if len(terms) < 2 and not numbers:
                continue  # conversational filler ("Yes.", "Happy to help!")
            matching_claims = [
                c for c in answer.claims if jaccard(terms, term_set(c.text)) >= 0.3 or sentence in c.text
            ]
            sources = [evidence[e] for c in matching_claims for e in c.evidence_ids if e in evidence] or cited
            source_text = _evidence_text(sources)
            support = self._support(sentence, source_text)
            supports.append(support)
            if support < self.settings.grounding_min_claim_support:
                issues.append(
                    ValidationIssue(
                        "unsupported_statement", f"{support:.0%} lexical support in cited sources", sentence
                    )
                )
            missing_numbers = numbers - extract_numbers(source_text)
            if missing_numbers:
                issues.append(
                    ValidationIssue(
                        "unsupported_number", f"Values not found in cited sources: {sorted(missing_numbers)}", sentence
                    )
                )
            lowered_source = normalize_unicode(all_cited_text).lower()
            for month in {m.lower() for m in _MONTHS.findall(sentence)}:
                if month not in lowered_source:
                    issues.append(ValidationIssue("unsupported_date", f"Date '{month}' not in cited sources", sentence))
            for contact in _CONTACT.findall(sentence):
                if normalize_unicode(contact).lower().rstrip(".,") not in lowered_source:
                    issues.append(ValidationIssue("unsupported_contact", f"'{contact}' not in cited sources", sentence))
            for entity in self._entities(sentence):
                if entity.lower() not in lowered_source and entity.lower() not in self.known_entities:
                    issues.append(ValidationIssue("unsupported_entity", f"'{entity}' not in cited sources", sentence))
        return issues

    @staticmethod
    def _entities(sentence: str) -> list[str]:
        tokens = _CAPITALIZED.findall(sentence)
        first = sentence.split(maxsplit=1)[0].strip("\"'(") if sentence.split() else ""
        return [
            t
            for t in tokens
            if (t != first and t not in _ALWAYS_OK and not t.isupper())
            or (t.isupper() and len(t) > 1 and t != first and t not in _ALWAYS_OK)
        ]

    @staticmethod
    def _support(statement: str, source_text: str) -> float:
        terms = term_set(statement)
        if not terms:
            return 1.0
        return len(terms & term_set(source_text)) / len(terms)


class _Verdict(BaseModel):
    claim_index: int
    supported: bool
    reason: str | None


class _JudgeOutput(BaseModel):
    verdicts: list[_Verdict]


@dataclass
class JudgeResult:
    passed: bool
    unsupported: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_version: str | None = None
    error: str | None = None


class LLMGroundingJudge:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    async def judge(self, answer: GeneratedAnswer, evidence: dict[str, EvidenceItem]) -> JudgeResult:
        prompt = get_prompt("grounding_validator")
        try:
            result = await self.llm.generate(
                prompt=prompt, user=build_judge_prompt(answer, evidence), output_model=_JudgeOutput, max_tokens=3000
            )
        except LLMError as exc:
            # Fail closed: an answer that cannot be verified is not returned.
            log.warning("grounding_judge_failed", error=exc.detail)
            return JudgeResult(False, [], error="judge_unavailable", prompt_version=prompt.version_id)
        verdicts = {v.claim_index: v for v in result.value.verdicts}
        unsupported = [
            f"{claim.text} ({verdicts[i].reason or 'unsupported'})" if i in verdicts else f"{claim.text} (no verdict)"
            for i, claim in enumerate(answer.claims)
            if i not in verdicts or not verdicts[i].supported
        ]
        return JudgeResult(
            passed=not unsupported,
            unsupported=unsupported,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            prompt_version=result.prompt_version,
        )
