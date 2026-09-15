"""The support agent: orchestrates the grounded-answer pipeline for one customer turn.

message -> query understanding -> safety/handoff policy -> retrieval -> reranking
-> evidence sufficiency -> contradiction handling -> grounded generation
-> citation + claim validation (one stricter retry) -> confidence -> answer | abstain | handoff

The LLM is only ever a reasoning component over retrieved evidence. There is no code path
that asks a model to answer without evidence, and any failure resolves to abstention or
handoff - never to an unvalidated answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from app.agents import responses
from app.agents.policy import (
    AUTHORITATIVE_ONLY_CATEGORIES,
    AUTHORITATIVE_SOURCES,
    HandoffDecision,
    PolicyOutcome,
    pre_retrieval_policy,
    repeated_failure_handoff,
)
from app.core.config import Settings
from app.core.errors import LLMError, RetrievalError
from app.llm.base import LLMUsage
from app.models.enums import AnswerStatus, ConfidenceLevel, HandoffPriority, HandoffReason
from app.observability.tracing import Timings
from app.rag.confidence import ConfidenceAssessment, assess_confidence
from app.rag.conflicts import detect_conflicts, resolve_conflicts
from app.rag.evidence import select_evidence
from app.rag.generator import AnswerGenerator, GenerationRequest, GenerationResult
from app.rag.grounding import GroundingValidator, LLMGroundingJudge, ValidationIssue, ValidationResult
from app.rag.query_analysis import LLMQueryAnalyzer, QueryAnalyzer, entity_terms
from app.rag.reranker import Reranker
from app.rag.types import (
    ConversationContext,
    EvidenceItem,
    GeneratedAnswer,
    QueryAnalysis,
    RetrievalFilters,
    Retriever,
)
from app.utils.text import split_sentences, term_set, truncate

log = structlog.get_logger(__name__)


@dataclass
class AgentRequest:
    company_id: uuid.UUID
    conversation_id: uuid.UUID
    message: str
    context: ConversationContext
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)


@dataclass
class AgentResult:
    status: AnswerStatus
    kind: str
    content: str
    reason: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    confidence: ConfidenceAssessment | None = None
    handoff: HandoffDecision | None = None
    analysis: QueryAnalysis | None = None
    counts_as_failure: bool = False
    provider: str | None = None
    model: str | None = None
    prompt_versions: dict[str, str] = field(default_factory=dict)
    usage: LLMUsage = field(default_factory=LLMUsage)
    trace: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0


class SupportAgent:
    def __init__(
        self,
        *,
        settings: Settings,
        analyzer: QueryAnalyzer,
        retriever: Retriever,
        reranker: Reranker,
        generator: AnswerGenerator,
        validator: GroundingValidator,
        judge: LLMGroundingJudge | None,
        embedding_model: str,
    ) -> None:
        self.settings = settings
        self.analyzer = analyzer
        self.retriever = retriever
        self.reranker = reranker
        self.generator = generator
        self.validator = validator
        self.judge = judge
        self.embedding_model = embedding_model

    async def respond(self, request: AgentRequest) -> AgentResult:
        timings = Timings()
        usage = LLMUsage()
        prompts: dict[str, str] = {}
        trace: dict[str, Any] = {
            "original_query": request.message,
            "standalone_query": request.message,
            "embedding_model": self.embedding_model,
            "candidates": [],
            "selected_evidence": [],
            "sufficiency": {},
            "conflicts": [],
            "validation": {},
            "confidence": {},
        }

        with timings.span("query_understanding"):
            analysis = await self.analyzer.analyze(request.message, request.context)
        if isinstance(self.analyzer, LLMQueryAnalyzer) and self.analyzer.last_usage[2]:
            usage.add(LLMUsage(*self.analyzer.last_usage[:2]))
            prompts["query_understanding"] = self.analyzer.last_usage[2]
        trace["standalone_query"] = analysis.standalone_query
        trace["analysis"] = analysis.model_dump(mode="json")

        def finish(result: AgentResult) -> AgentResult:
            result.analysis = analysis
            result.usage.add(usage)
            result.prompt_versions = {**prompts, **result.prompt_versions}
            result.timings = timings.values
            result.latency_ms = timings.total_ms()
            trace.update(
                decision=result.status.value,
                decision_reason=truncate(result.reason, 500),
                handoff_reason=result.handoff.reason.value if result.handoff else None,
                confidence=result.confidence.as_dict() if result.confidence else trace["confidence"],
                timings_ms={**timings.values, "total": result.latency_ms},
                prompt_versions=result.prompt_versions,
                provider=result.provider,
                model=result.model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            result.trace = trace
            log.info(
                "agent_decision",
                conversation_id=str(request.conversation_id),
                decision=result.status.value,
                kind=result.kind,
                handoff_reason=trace["handoff_reason"],
                retrieved=len(trace["candidates"]),
                selected=len(trace["selected_evidence"]),
                validation_passed=trace["validation"].get("passed"),
                confidence=trace["confidence"].get("level"),
                latency_ms=result.latency_ms,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            return result

        policy = pre_retrieval_policy(analysis, request.context)
        if policy is not None:
            return finish(self._from_policy(policy))

        sensitive_restricted = analysis.sensitive_category in AUTHORITATIVE_ONLY_CATEGORIES
        filters = request.filters
        if sensitive_restricted:
            filters = RetrievalFilters(filters.product, filters.category, filters.locale, AUTHORITATIVE_SOURCES)

        # --- Retrieval ----------------------------------------------------------------
        try:
            with timings.span("retrieval"):
                retrieval = await self.retriever.retrieve(
                    company_id=request.company_id, query=analysis.standalone_query, filters=filters
                )
        except RetrievalError as exc:
            log.error("retrieval_unavailable", error=exc.detail)
            return finish(self._handoff(
                HandoffReason.RETRIEVAL_FAILURE, "Knowledge-base retrieval failed; no answer was generated.",
                HandoffPriority.HIGH, responses.RETRIEVAL_UNAVAILABLE, failure=True))  # fmt: skip

        with timings.span("rerank"):
            if self.settings.reranker_enabled:
                ranked = await self.reranker.rerank(analysis.standalone_query, analysis.key_terms, retrieval.candidates)
            else:
                ranked = retrieval.candidates
        trace["candidates"] = [c.debug_view() for c in ranked[:20]]

        # --- Evidence sufficiency --------------------------------------------------------
        sufficiency = select_evidence(
            ranked, analysis.key_terms, self.settings, required_terms=entity_terms(analysis.entities)
        )
        trace["sufficiency"] = sufficiency.as_dict()
        if not sufficiency.sufficient:
            if sensitive_restricted:
                return finish(
                    self._sensitive_handoff(analysis, "No authoritative source covers this sensitive request.")
                )
            return finish(self._abstain(request.context, sufficiency.detail))

        # --- Contradictions ------------------------------------------------------------
        conflicts = detect_conflicts(sufficiency.selected, analysis.key_terms, self.settings)
        selected, conflicts = resolve_conflicts(conflicts, sufficiency.selected, self.settings, date.today())
        trace["conflicts"] = [c.as_dict() for c in conflicts]
        if any(not c.resolved for c in conflicts):
            return finish(self._handoff(
                HandoffReason.KNOWLEDGE_CONFLICT,
                "Retrieved sources state different values and authority/effective dates do not resolve which applies.",
                HandoffPriority.HIGH, responses.HANDOFF_CONFLICT, failure=True))  # fmt: skip

        evidence = [EvidenceItem(f"S{i + 1}", chunk) for i, chunk in enumerate(selected)]
        evidence_map = {item.evidence_id: item for item in evidence}
        trace["selected_evidence"] = [{"evidence_id": e.evidence_id, **e.chunk.debug_view()} for e in evidence]

        # --- Generation + validation (at most one stricter retry) ------------------------
        generation: GenerationResult | None = None
        validation = ValidationResult(False)
        feedback: list[str] | None = None
        attempts = 2 if self.generator.supports_retry else 1
        for attempt in range(1, attempts + 1):
            try:
                with timings.span("generation"):
                    generation = await self.generator.generate(GenerationRequest(
                        question=request.message, standalone_query=analysis.standalone_query,
                        key_terms=analysis.key_terms, evidence=evidence, context=request.context,
                        feedback=feedback))  # fmt: skip
            except LLMError as exc:
                log.error("generation_failed", error=exc.detail, refusal=type(exc).__name__)
                return finish(self._provider_failure(usage))
            usage.add(generation.usage)
            prompts["answer_generator"] = generation.prompt_version
            answer = generation.answer

            if answer.conflict_detected:
                return finish(self._with_model(self._handoff(
                    HandoffReason.KNOWLEDGE_CONFLICT, "The answer generator found conflicting evidence.",
                    HandoffPriority.HIGH, responses.HANDOFF_CONFLICT, failure=True), generation))  # fmt: skip
            if answer.should_abstain:
                if answer.handoff_required or sensitive_restricted:
                    return finish(
                        self._with_model(
                            self._sensitive_handoff(
                                analysis, answer.handoff_reason or answer.abstain_reason or "Generator escalated."
                            ),
                            generation,
                        )
                    )
                return finish(self._with_model(
                    self._abstain(request.context, f"Generator abstained: {answer.abstain_reason or 'insufficient evidence'}"),
                    generation))  # fmt: skip

            with timings.span("validation"):
                validation = await self._validate(answer, evidence_map, usage, prompts)
            trace["validation"] = {**validation.as_dict(), "attempt": attempt}
            if validation.passed:
                break
            feedback = validation.feedback()
            log.warning("grounding_validation_failed", attempt=attempt, issues=len(validation.issues))

        assert generation is not None
        answer = generation.answer
        if not validation.passed:
            return finish(self._with_model(self._handoff(
                HandoffReason.VALIDATION_FAILED,
                "The generated answer could not be verified against the cited evidence after a stricter retry.",
                HandoffPriority.NORMAL, responses.HANDOFF_VALIDATION, failure=True), generation))  # fmt: skip

        citations = self._citations(answer, evidence_map)
        cited_chunks = [evidence_map[c["evidence_id"]].chunk for c in citations]
        confidence = assess_confidence(
            self.settings,
            top_relevance=sufficiency.top_score,
            coverage=sufficiency.coverage,
            authority_weight=max(self.settings.authority_weight(c.authority) for c in cited_chunks),
            claim_support=validation.claim_support,
            validation_passed=validation.passed,
            unresolved_conflict=False,
        )
        trace["confidence"] = confidence.as_dict()
        if confidence.level in (ConfidenceLevel.LOW, ConfidenceLevel.ABSTAIN):
            result = self._abstain(request.context, f"Answer confidence too low ({confidence.score:.2f}).")
            result.confidence = confidence
            return finish(self._with_model(result, generation))
        if sensitive_restricted and confidence.level is not ConfidenceLevel.HIGH:
            return finish(self._with_model(
                self._sensitive_handoff(analysis, "Sensitive request without a high-confidence authoritative answer."),
                generation))  # fmt: skip

        if answer.handoff_required:
            result = self._handoff(
                HandoffReason.AI_ESCALATION, truncate(answer.handoff_reason or "Generator requested a human.", 400),
                HandoffPriority.NORMAL, f"{answer.answer}\n\n{responses.HANDOFF_AI_ESCALATION}")  # fmt: skip
            result.citations, result.confidence = citations, confidence
            return finish(self._with_model(result, generation))

        return finish(self._with_model(AgentResult(
            status=AnswerStatus.ANSWERED, kind="RAG_ANSWER", content=answer.answer,
            reason=f"Answered from {len(citations)} cited source(s); validation passed.",
            citations=citations, confidence=confidence), generation))  # fmt: skip

    # --- helpers ---------------------------------------------------------------------

    async def _validate(
        self,
        answer: GeneratedAnswer,
        evidence: dict[str, EvidenceItem],
        usage: LLMUsage,
        prompts: dict[str, str],
    ) -> ValidationResult:
        result = self.validator.validate(answer, evidence)
        if result.passed and self.judge is not None and self.settings.grounding_llm_judge_enabled:
            verdict = await self.judge.judge(answer, evidence)
            usage.add(LLMUsage(verdict.input_tokens, verdict.output_tokens))
            if verdict.prompt_version:
                prompts["grounding_validator"] = verdict.prompt_version
            result.judge = {"passed": verdict.passed, "unsupported": verdict.unsupported, "error": verdict.error}
            if not verdict.passed:
                result.passed = False
                detail = verdict.error or "; ".join(verdict.unsupported)[:500]
                result.issues.append(ValidationIssue("judge_unsupported", detail))
        return result

    def _citations(self, answer: GeneratedAnswer, evidence: dict[str, EvidenceItem]) -> list[dict[str, Any]]:
        """One citation per cited source, each pointing at a real retrieved chunk, with the
        excerpt that best supports the claims citing it."""
        claims_by_source: dict[str, list[str]] = {}
        for claim in answer.claims:
            for eid in claim.evidence_ids:
                if eid in evidence:
                    claims_by_source.setdefault(eid, []).append(claim.text)
        citations = []
        for eid, claim_texts in claims_by_source.items():
            chunk = evidence[eid].chunk
            citations.append({
                "evidence_id": eid,
                "chunk_id": str(chunk.chunk_id),
                "document_id": str(chunk.document_id),
                "document_version_id": str(chunk.document_version_id),
                "document_title": chunk.document_title,
                "version": chunk.version_number,
                "source_type": chunk.source_type,
                "source_uri": chunk.source_uri,
                "authority": chunk.authority,
                "section_title": chunk.section_title,
                "heading_path": chunk.heading_path,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "effective_date": chunk.effective_date.isoformat() if chunk.effective_date else None,
                "excerpt": _best_excerpt(chunk.content, claim_texts),
            })  # fmt: skip
        return citations

    def _from_policy(self, outcome: PolicyOutcome) -> AgentResult:
        return AgentResult(
            status=outcome.status,
            kind=outcome.kind,
            content=outcome.content,
            reason=outcome.reason,
            handoff=outcome.handoff,
            provider="policy",
        )

    def _abstain(self, context: ConversationContext, reason: str) -> AgentResult:
        escalation = repeated_failure_handoff(context, self.settings.handoff_failed_answers_threshold)
        if escalation is not None:
            escalation.detail = f"{escalation.detail} Last: {reason}"
            return AgentResult(
                AnswerStatus.HANDOFF_REQUIRED, "HANDOFF", responses.HANDOFF_REPEATED_FAILURES,
                reason, handoff=escalation, counts_as_failure=True,
            )  # fmt: skip
        return AgentResult(AnswerStatus.ABSTAINED, "ABSTENTION", responses.ABSTAIN, reason, counts_as_failure=True)

    def _handoff(
        self, reason: HandoffReason, detail: str, priority: HandoffPriority, content: str, failure: bool = False
    ) -> AgentResult:
        return AgentResult(
            AnswerStatus.HANDOFF_REQUIRED, "HANDOFF", content, detail,
            handoff=HandoffDecision(reason, detail, priority), counts_as_failure=failure,
        )  # fmt: skip

    def _sensitive_handoff(self, analysis: QueryAnalysis, detail: str) -> AgentResult:
        category = analysis.sensitive_category if analysis.sensitive_category != "none" else "sensitive"
        return self._handoff(
            HandoffReason.SENSITIVE_REQUEST, f"{category}: {detail}", HandoffPriority.HIGH,
            responses.HANDOFF_SENSITIVE, failure=True,
        )  # fmt: skip

    def _provider_failure(self, usage: LLMUsage) -> AgentResult:
        detail = "The LLM provider failed or refused; no answer was generated."
        if self.settings.handoff_on_provider_failure:
            return self._handoff(
                HandoffReason.PROVIDER_FAILURE,
                detail,
                HandoffPriority.NORMAL,
                responses.PROVIDER_UNAVAILABLE_HANDOFF,
                failure=True,
            )
        return AgentResult(
            AnswerStatus.ABSTAINED, "ERROR", responses.PROVIDER_UNAVAILABLE, detail, counts_as_failure=True
        )

    @staticmethod
    def _with_model(result: AgentResult, generation: GenerationResult) -> AgentResult:
        result.provider = generation.provider
        result.model = generation.model
        return result


def _best_excerpt(content: str, claims: list[str], max_chars: int = 400) -> str:
    """The source sentences that best support each claim, in document order."""
    sentences = split_sentences(content)
    if not sentences:
        return truncate(content, max_chars)
    chosen: list[int] = []
    for claim in claims:
        terms = term_set(claim)
        best = max(range(len(sentences)), key=lambda i: len(term_set(sentences[i]) & terms))
        if best not in chosen:
            chosen.append(best)
    excerpt = ""
    for index in sorted(chosen):
        candidate = f"{excerpt} {sentences[index]}".strip()
        if excerpt and len(candidate) > max_chars:
            break
        excerpt = candidate
    return truncate(excerpt, max_chars)
