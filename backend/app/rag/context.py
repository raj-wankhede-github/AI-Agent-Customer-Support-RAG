"""Conversation memory management: recent turns verbatim, older turns summarized.

The summary is conversation memory only. It is labelled low-authority in prompts and is
never used as evidence for company facts.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.prompts import get_prompt
from app.llm.structured import StructuredLLM
from app.models.enums import AnswerStatus, MessageRole
from app.rag.prompt_builder import build_summary_prompt
from app.rag.types import HistoryTurn
from app.utils.text import truncate

log = structlog.get_logger(__name__)

_OUTCOME = {
    AnswerStatus.ANSWERED.value: "answered",
    AnswerStatus.ABSTAINED.value: "not answered - insufficient information",
    AnswerStatus.HANDOFF_REQUIRED.value: "handed to a human",
}


class _Summary(BaseModel):
    summary: str


class ConversationSummarizer:
    def __init__(self, settings: Settings, llm: StructuredLLM | None) -> None:
        self.settings = settings
        self.llm = llm

    async def summarize(self, previous: str | None, turns: list[HistoryTurn]) -> str:
        if not turns:
            return previous or ""
        if self.llm is not None:
            transcript = "\n".join(
                f"{t.role.value}: {truncate(t.content, self.settings.conversation_message_max_chars)}" for t in turns
            )
            try:
                result = await self.llm.generate(
                    prompt=get_prompt("conversation_summarizer"),
                    user=build_summary_prompt(previous, transcript),
                    output_model=_Summary,
                    max_tokens=1500,
                )
                return truncate(result.value.summary, self.settings.conversation_summary_max_chars)
            except LLMError as exc:
                log.warning("summarizer_llm_failed_using_extractive", error=exc.detail)
        return self._extractive(previous, turns)

    def _extractive(self, previous: str | None, turns: list[HistoryTurn]) -> str:
        """Records what the customer asked and how each turn ended - not the answers."""
        parts = [previous] if previous else []
        pending: str | None = None
        for turn in turns:
            if turn.role is MessageRole.USER:
                pending = truncate(turn.standalone_query or turn.content, 160)
                parts.append(f'Customer asked: "{pending}"')
            elif turn.role is MessageRole.ASSISTANT and pending:
                parts.append(f"({_OUTCOME.get(turn.answer_status or '', 'responded')})")
                pending = None
            elif turn.role is MessageRole.HUMAN_AGENT:
                parts.append("(a human agent replied)")
        summary = " ".join(parts)
        limit = self.settings.conversation_summary_max_chars
        return summary if len(summary) <= limit else "..." + summary[-(limit - 3) :]
