"""Answer generators.

`LLMAnswerGenerator` asks the configured model for a schema-constrained, claim-cited
answer. `ExtractiveAnswerGenerator` (offline mode) composes the answer from verbatim
evidence sentences that best cover the question, so it cannot state anything that is not
in the knowledge base - at the cost of less fluent answers. Both outputs go through the
same grounding validation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import Settings
from app.llm.base import LLMUsage
from app.llm.prompts import PromptTemplate, get_prompt
from app.llm.structured import StructuredLLM
from app.rag.prompt_builder import build_answer_prompt
from app.rag.types import Claim, ConversationContext, EvidenceItem, GeneratedAnswer
from app.security.prompt_injection import detect_injection
from app.utils.text import extract_numbers, split_sentences, term_set

EXTRACTIVE_VERSION = "extractive@2026-09-15.2"
# The best sentence must cover at least this share of the question's key terms; below it
# the evidence is judged not to answer the question and the generator abstains.
_MIN_SENTENCE_COVERAGE = 0.34
# A short section whose heading matches the question is returned whole (up to this many
# sentences); otherwise sentences are added only when they cover additional key terms.
_MAX_SECTION_SENTENCES = 4
_MAX_SCATTERED_SENTENCES = 3
# Sentences stating a concrete value (days, prices, limits) are usually the substantive
# part of a policy, so they win ties against generic prose.
_NUMBER_BONUS = 0.15
# A sentence under a heading that names the question's subject ("Support Hours") beats an
# incidental mention of the same words elsewhere ("... during support hours").
_TOPICAL_BONUS = 0.1
# Answer-type matching: a question asking for a value (how long, how much, a number, a price,
# a fee) is answered by a sentence that states one. Sentences without any number or contact
# detail are pushed down so an incidental word match ("card number by email or phone")
# cannot outrank the actual value ("Phone: 1-800-555-0199").
_VALUE_QUESTION = re.compile(
    r"\b(how (long|much|many|soon|often|far)|what time|when (do|does|will|is|are|can)"
    r"|phone|telephone|number|price|cost|fee|deadline|limit)\b",
    re.IGNORECASE,
)
_CONTACT_VALUE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://\S+")
_NON_VALUE_PENALTY = 0.35
# Short fragments (list items, labels) are only used when they name most of the question.
_SHORT_FRAGMENT_MIN_COVERAGE = 0.5
_META_SENTENCE = re.compile(
    r"^(effective( date)?\b|last (updated|revised)\b|version\b|all content is fictional"
    r"|this (policy|document|guide|faq|page) (applies|explains|describes|covers)\b)",
    re.IGNORECASE,
)


@dataclass
class GenerationRequest:
    question: str
    standalone_query: str
    key_terms: list[str]
    evidence: list[EvidenceItem]
    context: ConversationContext
    feedback: list[str] | None = None


@dataclass
class GenerationResult:
    answer: GeneratedAnswer
    provider: str
    model: str
    prompt_version: str
    latency_ms: int = 0
    usage: LLMUsage = field(default_factory=LLMUsage)


class AnswerGenerator(Protocol):
    name: str
    supports_retry: bool

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class LLMAnswerGenerator:
    name = "llm"
    supports_retry = True

    def __init__(self, llm: StructuredLLM, settings: Settings, canary: str) -> None:
        self.llm = llm
        self.settings = settings
        self.canary = canary

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        base = get_prompt("answer_generator", canary=self.canary)
        prompt = base
        if request.feedback:
            strict = get_prompt("answer_generator_strict")
            prompt = PromptTemplate(
                base.name,
                f"{base.version}+{strict.name}@{strict.version}",
                base.purpose,
                f"{base.system}\n\n{strict.system}",
            )
        user = build_answer_prompt(
            question=request.question,
            standalone_query=request.standalone_query,
            evidence=request.evidence,
            context=request.context,
            settings=self.settings,
            feedback=request.feedback,
        )
        result = await self.llm.generate(prompt=prompt, user=user, output_model=GeneratedAnswer)
        return GenerationResult(
            answer=result.value,
            provider=self.llm.provider.name,
            model=result.model,
            prompt_version=result.prompt_version,
            latency_ms=result.latency_ms,
            usage=result.usage,
        )


@dataclass
class _Sentence:
    score: float
    rank: int
    position: int
    text: str
    item: EvidenceItem
    own: set[str]
    covered: set[str]
    topical: bool
    short: bool


def _blocked(sentence: str) -> bool:
    """Never repeat instruction-like or boilerplate text as an answer."""
    return bool(detect_injection(sentence) or _META_SENTENCE.match(sentence))


def _heading(section: str) -> str:
    return section.title() if section.isupper() else section


class ExtractiveAnswerGenerator:
    name = "extractive"
    supports_retry = False

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        keys = set(request.key_terms)
        if not keys:
            return self._abstain("The question has no searchable terms.")

        seeks_value = bool(_VALUE_QUESTION.search(request.standalone_query))
        sentences: list[_Sentence] = []
        for rank, item in enumerate(request.evidence):
            chunk = item.chunk
            # Words from the document title describe every chunk equally, so they cannot tell
            # sentences apart; section headings can.
            section_terms = term_set(chunk.section_title or "") - term_set(chunk.document_title)
            topical = bool(keys & section_terms)
            for position, text in enumerate(split_sentences(chunk.content)):
                if _blocked(text):
                    continue
                own = keys & term_set(text)
                covered = own | (keys & section_terms)
                if not covered or (not own and not topical):
                    continue
                has_number = bool(extract_numbers(text))
                short = len(text.split()) < 5 and not has_number
                if short and len(own) / len(keys) < _SHORT_FRAGMENT_MIN_COVERAGE:
                    continue
                has_value = has_number or bool(_CONTACT_VALUE.search(text))
                score = len(covered) / len(keys) + (_NUMBER_BONUS if has_value else 0.0)
                if seeks_value and not has_value:
                    score -= _NON_VALUE_PENALTY
                score += (_TOPICAL_BONUS if topical else 0.0) + 0.1 * chunk.rerank_score - 0.01 * rank
                sentences.append(_Sentence(score, rank, position, text, item, own, covered, topical, short))

        sentences.sort(key=lambda s: s.score, reverse=True)
        if not sentences or len(sentences[0].covered) / len(keys) < _MIN_SENTENCE_COVERAGE:
            return self._abstain("No evidence sentence directly addresses the question.")

        best = sentences[0]
        picked = [best]
        if best.topical:
            for sentence in sorted(
                (s for s in sentences if s.item is best.item and s is not best), key=lambda s: s.position
            ):
                if len(picked) >= _MAX_SECTION_SENTENCES:
                    break
                picked.append(sentence)
        else:
            covered = set(best.covered)
            for sentence in sentences[1:]:
                if len(picked) >= _MAX_SCATTERED_SENTENCES:
                    break
                # A supporting sentence must itself be about the question, not just share a word.
                if sentence.own - covered and len(sentence.own) / len(keys) >= _SHORT_FRAGMENT_MIN_COVERAGE:
                    picked.append(sentence)
                    covered |= sentence.covered
        picked.sort(key=lambda s: (s.rank, s.position))  # document order reads naturally

        claims: list[Claim] = []
        prefixed: set[str] = set()
        for sentence in picked:
            text = sentence.text
            section = sentence.item.chunk.section_title
            evidence_id = sentence.item.evidence_id
            needs_heading = (
                section
                and not section.rstrip().endswith("?")
                and evidence_id not in prefixed
                and (sentence.covered - sentence.own or sentence.short)
            )
            if needs_heading and section:
                text = f"{_heading(section)}: {text}"  # keep the qualifying heading with the fact
                prefixed.add(evidence_id)
            if not text.rstrip().endswith((".", "!", "?", ":")):
                text = f"{text.rstrip()}."  # list items and labels read as sentences once joined
            claims.append(Claim(text=text, evidence_ids=[evidence_id]))
        return self._result(
            GeneratedAnswer(
                should_abstain=False,
                abstain_reason=None,
                answer=" ".join(c.text for c in claims),
                claims=claims,
                conflict_detected=False,
                handoff_required=False,
                handoff_reason=None,
            )
        )

    def _abstain(self, reason: str) -> GenerationResult:
        return self._result(
            GeneratedAnswer(
                should_abstain=True,
                abstain_reason=reason,
                answer="",
                claims=[],
                conflict_detected=False,
                handoff_required=False,
                handoff_reason=None,
            )
        )

    @staticmethod
    def _result(answer: GeneratedAnswer) -> GenerationResult:
        return GenerationResult(
            answer=answer, provider="none", model="extractive-v1", prompt_version=EXTRACTIVE_VERSION
        )
