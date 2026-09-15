"""RAG evaluation harness.

Ingests a corpus into a temporary, isolated tenant through the real ingestion pipeline,
runs every golden case through the real agent (multi-turn cases replay their conversation
first), scores retrieval, generation and safety, prints a report, writes JSON, and deletes
the temporary tenant. Uses whatever providers are configured (offline by default).

    python -m app.cli eval [--dataset tests/evals/golden_dataset.json] [--output reports/eval.json]

The dataset holds questions and *expectations* only. Nothing in the application reads it,
so passing cannot come from hard-coded answers.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete

from app.agents.support_agent import AgentRequest, AgentResult
from app.core.container import Container
from app.models import Company, User
from app.models.enums import AnswerStatus, MessageRole, SourceAuthority, UserRole
from app.rag.types import ConversationContext, HistoryTurn
from app.security.principal import Principal
from app.services.knowledge import KnowledgeService, UploadMetadata

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
DEFAULT_DATASET = BACKEND / "tests" / "evals" / "golden_dataset.json"


def decision_of(result: AgentResult) -> str:
    if result.status is AnswerStatus.HANDOFF_REQUIRED:
        return "HANDOFF"
    if result.status is AnswerStatus.ABSTAINED:
        return "ABSTAIN"
    return "CLARIFY" if result.kind == "CLARIFICATION" else "ANSWER" if result.kind == "RAG_ANSWER" else "OTHER"


@dataclass
class CaseResult:
    case_id: str
    category: str
    expected: str
    actual: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    answer: str = ""
    cited: list[str] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    validation_passed: bool | None = None
    handoff_reason: str | None = None
    latency_ms: int = 0


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score_case(case: dict[str, Any], result: AgentResult, k: int) -> CaseResult:
    expected = case["expected"]
    actual = decision_of(result)
    answer = result.content
    cited = sorted({c["document_title"] for c in result.citations})
    retrieved = list(dict.fromkeys(c["document_title"] for c in result.trace.get("candidates", [])))[:k]
    failures: list[str] = []
    if actual != expected["decision"]:
        failures.append(f"decision {actual} != expected {expected['decision']}")
    if expected.get("handoff_reason") and (
        result.handoff is None or result.handoff.reason.value != expected["handoff_reason"]
    ):
        failures.append(
            f"handoff reason {result.handoff.reason.value if result.handoff else None} != {expected['handoff_reason']}"
        )
    lowered = answer.lower()
    if actual == "ANSWER":
        failures += [f"answer missing {s!r}" for s in expected.get("answer_contains", []) if s.lower() not in lowered]
        if expected.get("sources") and (not cited or not set(cited) <= set(expected["sources"])):
            failures.append(f"cited {cited} not within expected {expected['sources']}")
    failures += [
        f"answer contains forbidden {s!r}" for s in expected.get("answer_must_not_contain", []) if s.lower() in lowered
    ]
    return CaseResult(
        case_id=case["id"], category=case["category"], expected=expected["decision"], actual=actual, passed=not failures,
        failures=failures, answer=answer, cited=cited, retrieved=retrieved,
        validation_passed=(result.trace.get("validation") or {}).get("passed"),
        handoff_reason=result.handoff.reason.value if result.handoff else None, latency_ms=result.latency_ms,
    )  # fmt: skip


def _hallucinated(result: CaseResult) -> bool:
    return not result.validation_passed or any(
        f.startswith(("answer contains forbidden", "cited")) for f in result.failures
    )


def aggregate(cases: list[dict[str, Any]], results: list[CaseResult]) -> dict[str, Any]:
    by_id = {c["id"]: c for c in cases}
    answered = [r for r in results if r.actual == "ANSWER"]
    expect_answer = [r for r in results if r.expected == "ANSWER"]
    expect_no_answer = [r for r in results if r.expected != "ANSWER"]
    with_sources = [r for r in expect_answer if by_id[r.case_id]["expected"].get("sources")]

    recalls, precisions, reciprocal_ranks = [], [], []
    for r in with_sources:
        relevant = set(by_id[r.case_id]["expected"]["sources"])
        hits = [title for title in r.retrieved if title in relevant]
        recalls.append(len(set(hits)) / len(relevant))
        precisions.append(len(hits) / len(r.retrieved) if r.retrieved else 0.0)
        rank = next((i + 1 for i, title in enumerate(r.retrieved) if title in relevant), None)
        reciprocal_ranks.append(1 / rank if rank else 0.0)

    def citations_ok(r: CaseResult) -> bool:
        sources = by_id[r.case_id]["expected"].get("sources")
        return bool(r.cited) and (not sources or set(r.cited) <= set(sources))

    injection = [r for r in results if r.category == "prompt_injection"]
    handoffs = [r for r in results if by_id[r.case_id]["expected"].get("handoff_reason")]
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "decision_accuracy": _ratio(sum(r.actual == r.expected for r in results), len(results)),
        "retrieval": {
            "recall_at_k": _ratio(sum(recalls), len(recalls)),
            "precision_at_k": _ratio(sum(precisions), len(precisions)),
            "mrr": _ratio(sum(reciprocal_ranks), len(reciprocal_ranks)),
        },
        "generation": {
            "answer_rate_on_answerable": _ratio(sum(r.actual == "ANSWER" for r in expect_answer), len(expect_answer)),
            "answer_correctness": _ratio(sum(r.passed for r in expect_answer if r.actual == "ANSWER"), sum(r.actual == "ANSWER" for r in expect_answer)),
            "groundedness": _ratio(sum(bool(r.validation_passed) for r in answered), len(answered)),
            "citation_correctness": _ratio(sum(citations_ok(r) for r in answered), len(answered)),
            "abstention_accuracy": _ratio(sum(r.actual != "ANSWER" for r in expect_no_answer), len(expect_no_answer)),
        },
        "safety": {
            "unsupported_answer_rate": _ratio(sum(r.actual == "ANSWER" for r in expect_no_answer), len(expect_no_answer)),
            # Hallucination: an answer containing forbidden content, citing sources outside the expected
            # set, or not passing grounding validation. An accurate, cited answer that misses the asked-for
            # fact is counted separately as off-target.
            "hallucination_rate": _ratio(sum(_hallucinated(r) for r in answered), len(answered)),
            "off_target_answer_rate": _ratio(sum(any(f.startswith("answer missing") for f in r.failures) for r in answered), len(answered)),
            "prompt_injection_resistance": _ratio(sum(r.passed for r in injection), len(injection)),
            "handoff_accuracy": _ratio(sum(r.handoff_reason == by_id[r.case_id]["expected"]["handoff_reason"] for r in handoffs), len(handoffs)),
        },
        "by_category": {
            category: f"{sum(r.passed for r in results if r.category == category)}/{sum(r.category == category for r in results)}"
            for category in sorted({r.category for r in results})
        },
    }  # fmt: skip


def check_thresholds(summary: dict[str, Any], thresholds: dict[str, float]) -> list[str]:
    """Threshold keys are dotted metric paths; `_max` suffix means an upper bound."""
    violations = []
    for key, bound in thresholds.items():
        path = key.removesuffix("_max").split(".")
        value: Any = summary
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            continue
        if key.endswith("_max") and value > bound:
            violations.append(f"{'.'.join(path)} = {value} exceeds {bound}")
        elif not key.endswith("_max") and value < bound:
            violations.append(f"{'.'.join(path)} = {value} below {bound}")
    return violations


async def _ingest_corpus(container: Container, principal: Principal, corpus: list[dict[str, Any]]) -> None:
    from app.cli import process_queue

    service = KnowledgeService(container.settings, container.storage)
    for entry in corpus:
        path = REPO / entry["path"]
        meta = UploadMetadata(
            title=entry.get("title"), authority=SourceAuthority(entry.get("authority", "SUPPORT_ARTICLE")),
            product=entry.get("product"), effective_date=date.fromisoformat(entry["effective_date"]) if entry.get("effective_date") else None,
        )  # fmt: skip
        async with container.sessions() as session:
            await service.upload(
                session, principal, filename=path.name, content_type=None, data=path.read_bytes(), meta=meta
            )
    await process_queue(container)


async def evaluate(container: Container, dataset: dict[str, Any]) -> tuple[dict[str, Any], list[CaseResult]]:
    slug = f"eval-{uuid.uuid4().hex[:8]}"
    async with container.sessions() as session:
        company = Company(slug=slug, name="Evaluation Tenant")
        session.add(company)
        await session.flush()
        admin = User(company_id=company.id, email=f"eval-admin@{slug}.invalid", name="Eval", role=UserRole.ADMIN)
        session.add(admin)
        await session.commit()
    principal = Principal(
        admin.id, company.id, company.name, UserRole.ADMIN, admin.email, admin.name, "eval", datetime.now(UTC)
    )
    try:
        await _ingest_corpus(container, principal, dataset["corpus"])
        results = []
        k = container.settings.rag_max_context
        for case in dataset["cases"]:
            context = ConversationContext()
            for turn in [*case.get("conversation", []), case["question"]]:
                result = await container.agent.respond(
                    AgentRequest(company_id=company.id, conversation_id=uuid.uuid4(), message=turn, context=context)
                )
                context.recent.append(
                    HistoryTurn(MessageRole.USER, turn, result.analysis.standalone_query if result.analysis else None)
                )
                context.recent.append(
                    HistoryTurn(MessageRole.ASSISTANT, result.content, answer_status=result.status.value)
                )
            results.append(score_case(case, result, k))
        return aggregate(dataset["cases"], results), results
    finally:
        async with container.sessions() as session:
            await session.execute(delete(Company).where(Company.id == company.id))
            await session.commit()


def render(summary: dict[str, Any], results: list[CaseResult]) -> str:
    lines = ["", "RAG evaluation", "=" * 72]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"{mark}  {r.case_id:<34} expected={r.expected:<8} actual={r.actual:<8}")
        lines.extend(f"      - {f}" for f in r.failures)
    lines += [
        "-" * 72,
        f"Passed {summary['passed']}/{summary['cases']}  decision accuracy {summary['decision_accuracy']}",
    ]
    for group in ("retrieval", "generation", "safety"):
        lines.append(f"{group:<11} " + "  ".join(f"{k}={v}" for k, v in summary[group].items()))
    lines.append("by category " + "  ".join(f"{k}={v}" for k, v in summary["by_category"].items()))
    return "\n".join(lines)


async def run_evaluation(container: Container, dataset: Path | None = None, output: Path | None = None) -> int:
    data = json.loads((dataset or DEFAULT_DATASET).read_text(encoding="utf-8"))
    summary, results = await evaluate(container, data)
    violations = check_thresholds(summary, data.get("thresholds", {}))
    print(render(summary, results))
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "llm_provider": container.settings.llm_provider,
        "embedding_model": container.embedder.model,
        "summary": summary,
        "threshold_violations": violations,
        "results": [r.__dict__ for r in results],
    }
    target = output or BACKEND / "reports" / "eval-latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to {target}")
    if violations:
        print("THRESHOLD VIOLATIONS:\n  " + "\n  ".join(violations))
        return 1
    return 0
