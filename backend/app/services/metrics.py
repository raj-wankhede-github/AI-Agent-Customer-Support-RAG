"""Operational and RAG-quality metrics computed from persisted data (no invented 'accuracy')."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.admin import CountItem, MetricsOut, UnansweredQuestion

_CONVERSATIONS = text("""
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'OPEN') AS open,
           count(*) FILTER (WHERE status = 'WAITING_FOR_HUMAN') AS waiting,
           count(*) FILTER (WHERE status = 'RESOLVED') AS resolved,
           count(*) FILTER (WHERE status = 'CLOSED') AS closed
    FROM conversations WHERE company_id = :cid AND created_at >= :since
""")
_RESPONSES = text("""
    SELECT count(*) AS total,
           count(*) FILTER (WHERE answer_status = 'ANSWERED' AND retrieval_metadata->>'response_kind' = 'RAG_ANSWER') AS answered,
           count(*) FILTER (WHERE answer_status = 'ABSTAINED') AS abstained,
           count(*) FILTER (WHERE answer_status = 'HANDOFF_REQUIRED') AS handoff,
           avg(latency_ms) AS avg_latency,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency,
           coalesce(sum(input_tokens), 0) AS input_tokens,
           coalesce(sum(output_tokens), 0) AS output_tokens
    FROM messages
    WHERE company_id = :cid AND role = 'ASSISTANT' AND created_at >= :since AND latency_ms IS NOT NULL
""")
_HANDED_OFF = text(
    "SELECT count(DISTINCT conversation_id) FROM handoffs WHERE company_id = :cid AND created_at >= :since"
)
_PENDING = text("SELECT count(*) FROM handoffs WHERE company_id = :cid AND status IN ('PENDING', 'ASSIGNED')")
_HANDOFF_REASONS = text("""
    SELECT reason_code AS key, count(*) AS count FROM handoffs
    WHERE company_id = :cid AND created_at >= :since GROUP BY reason_code ORDER BY count DESC
""")
_FEEDBACK = text("""
    SELECT rating, reason, count(*) AS count FROM feedback
    WHERE company_id = :cid AND created_at >= :since GROUP BY rating, reason
""")
_DOCUMENTS = text("""
    SELECT count(*) FILTER (WHERE status = 'ACTIVE') AS active,
           count(*) FILTER (WHERE status = 'INACTIVE') AS inactive,
           count(*) FILTER (WHERE status = 'ACTIVE' AND active_version_id IS NOT NULL) AS searchable
    FROM documents WHERE company_id = :cid
""")
_VERSIONS = text("""
    SELECT v.status AS key, count(*) AS count FROM document_versions v
    JOIN documents d ON d.id = v.document_id
    WHERE v.company_id = :cid AND d.status <> 'DELETED' GROUP BY v.status ORDER BY count DESC
""")
_TRACES = text("""
    SELECT count(*) FILTER (WHERE sufficiency->>'sufficient' IS NOT NULL) AS retrieval_attempts,
           count(*) FILTER (WHERE sufficiency->>'sufficient' = 'true') AS retrieval_success,
           count(*) FILTER (WHERE validation->>'passed' = 'false') AS validation_failures,
           count(*) FILTER (WHERE confidence->>'level' IN ('LOW', 'ABSTAIN')) AS low_confidence
    FROM rag_traces WHERE company_id = :cid AND created_at >= :since
""")
_ABSTENTION_REASONS = text("""
    SELECT coalesce(sufficiency->>'reason_code', CASE WHEN validation->>'passed' = 'false' THEN 'VALIDATION_FAILED'
                    ELSE 'OTHER' END) AS key, count(*) AS count
    FROM rag_traces
    WHERE company_id = :cid AND created_at >= :since AND decision = 'ABSTAINED'
    GROUP BY 1 ORDER BY count DESC
""")
_UNANSWERED = text("""
    SELECT id, conversation_id, original_query, decision, decision_reason, created_at FROM rag_traces
    WHERE company_id = :cid AND decision IN ('ABSTAINED', 'HANDOFF_REQUIRED')
    ORDER BY created_at DESC LIMIT 10
""")


async def compute_metrics(session: AsyncSession, company_id: uuid.UUID, days: int) -> MetricsOut:
    params: dict[str, Any] = {"cid": company_id, "since": datetime.now(UTC) - timedelta(days=days)}

    async def one(query: Any) -> Any:
        return (await session.execute(query, params)).mappings().one()

    conv = await one(_CONVERSATIONS)
    resp = await one(_RESPONSES)
    docs = await one(_DOCUMENTS)
    traces = await one(_TRACES)
    handed_off = await session.scalar(_HANDED_OFF, params) or 0
    pending = await session.scalar(_PENDING, params) or 0
    feedback_rows = (await session.execute(_FEEDBACK, params)).mappings().all()
    versions = [
        CountItem(key=r["key"], count=r["count"]) for r in (await session.execute(_VERSIONS, params)).mappings()
    ]

    reasons: dict[str, int] = {}
    for row in feedback_rows:
        if row["rating"] == "NOT_HELPFUL":
            key = row["reason"] or "UNSPECIFIED"
            reasons[key] = reasons.get(key, 0) + row["count"]
    total_responses = resp["total"] or 0
    return MetricsOut(
        window_days=days,
        conversations_total=conv["total"], conversations_open=conv["open"], conversations_waiting_for_human=conv["waiting"],
        conversations_resolved=conv["resolved"], conversations_closed=conv["closed"],
        assistant_responses=total_responses, answered=resp["answered"], abstained=resp["abstained"],
        handoff_responses=resp["handoff"],
        abstention_rate=round(resp["abstained"] / total_responses, 4) if total_responses else 0.0,
        handoff_rate=round(handed_off / conv["total"], 4) if conv["total"] else 0.0,
        pending_handoffs=pending,
        avg_response_latency_ms=round(float(resp["avg_latency"]), 1) if resp["avg_latency"] is not None else None,
        p95_response_latency_ms=round(float(resp["p95_latency"]), 1) if resp["p95_latency"] is not None else None,
        feedback_helpful=sum(r["count"] for r in feedback_rows if r["rating"] == "HELPFUL"),
        feedback_not_helpful=sum(r["count"] for r in feedback_rows if r["rating"] == "NOT_HELPFUL"),
        feedback_reasons=[CountItem(key=k, count=v) for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])],
        documents_active=docs["active"], documents_inactive=docs["inactive"], documents_searchable=docs["searchable"],
        versions_by_status=versions,
        ingestion_failures=next((v.count for v in versions if v.key == "FAILED"), 0),
        retrieval_success_rate=(
            round(traces["retrieval_success"] / traces["retrieval_attempts"], 4) if traces["retrieval_attempts"] else None
        ),
        citation_validation_failures=traces["validation_failures"],
        low_confidence_responses=traces["low_confidence"],
        handoff_reasons=[CountItem(**r) for r in (await session.execute(_HANDOFF_REASONS, params)).mappings()],
        abstention_reasons=[CountItem(**r) for r in (await session.execute(_ABSTENTION_REASONS, params)).mappings()],
        recent_unanswered=[
            UnansweredQuestion(trace_id=r["id"], conversation_id=r["conversation_id"], question=r["original_query"][:300],
                               decision=r["decision"], reason=r["decision_reason"], created_at=r["created_at"])
            for r in (await session.execute(_UNANSWERED, params)).mappings()
        ],
        input_tokens=int(resp["input_tokens"]), output_tokens=int(resp["output_tokens"]),
    )  # fmt: skip
