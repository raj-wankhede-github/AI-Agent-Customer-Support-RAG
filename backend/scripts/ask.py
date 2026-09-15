"""Ask the support agent questions from the command line (development / debugging).

Runs the exact production pipeline (query understanding, hybrid retrieval, reranking,
sufficiency, conflicts, generation, validation) against a tenant's knowledge base and prints
the decision, reason, confidence and citations. Nothing is persisted.

    uv run python scripts/ask.py "What is your standard shipping time?"
    uv run python scripts/ask.py --company globex "How long is the warranty?"
    uv run python scripts/ask.py "What is your refund policy?" "What about international purchases?"

Multiple questions are asked as consecutive turns of one conversation.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.agents.support_agent import AgentRequest
from app.core.config import get_settings
from app.core.container import build_container
from app.models import Company
from app.models.enums import AnswerStatus, MessageRole
from app.observability.logging import configure_logging
from app.rag.types import ConversationContext, HistoryTurn


async def main(company_slug: str, questions: list[str], verbose: bool) -> None:
    settings = get_settings()
    configure_logging("WARNING", json_logs=False, service="ask")
    container = build_container(settings)
    try:
        async with container.sessions() as session:
            company = (await session.execute(select(Company).where(Company.slug == company_slug))).scalar_one()
        context = ConversationContext()
        for question in questions:
            result = await container.agent.respond(
                AgentRequest(company_id=company.id, conversation_id=uuid.uuid4(), message=question, context=context)
            )
            print(f"\nQ: {question}")
            if result.analysis and result.analysis.standalone_query != question:
                print(f"   standalone: {result.analysis.standalone_query}")
            confidence = f"{result.confidence.level.value} {result.confidence.score:.2f}" if result.confidence else "-"
            print(f"   {result.status.value} / {result.kind}  confidence={confidence}  ({result.latency_ms} ms)")
            print(f"   reason: {result.reason}")
            if result.handoff:
                print(f"   handoff: {result.handoff.reason.value} [{result.handoff.priority.value}]")
            print(f"A: {result.content}")
            for c in result.citations:
                where = f"p.{c['page_number']}" if c["page_number"] else c["section_title"]
                print(f"   [{c['evidence_id']}] {c['document_title']} - {where}")
            if verbose:
                for cand in result.trace.get("candidates", [])[:5]:
                    print(
                        f"     cand {cand['rerank_score']:.3f} cov={cand['coverage']:.2f} vec={cand['vector_similarity']:.3f}"
                        f" lex={cand['lexical_rank']} {cand['document_title']} / {cand['section_title']}"
                    )
                if result.trace.get("validation"):
                    print(f"     validation: {result.trace['validation']}")
            context.recent.append(
                HistoryTurn(MessageRole.USER, question, result.analysis.standalone_query if result.analysis else None)
            )
            context.recent.append(HistoryTurn(MessageRole.ASSISTANT, result.content, answer_status=result.status.value))
            if result.status in (AnswerStatus.ABSTAINED,) or result.counts_as_failure:
                context.consecutive_failed_answers += 1
    finally:
        await container.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("questions", nargs="+")
    parser.add_argument("--company", default="acme")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.company, args.questions, args.verbose))
