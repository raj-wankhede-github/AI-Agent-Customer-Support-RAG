"""Query understanding: standalone rewrite, intent, sensitivity and handoff signals.

`RuleBasedQueryAnalyzer` is deterministic and always runs. `LLMQueryAnalyzer` adds a
model's reading of the message, but safety-relevant signals are merged with OR: the LLM
can add a handoff/sensitivity/frustration flag, never remove one the rules detected.
Prompt-injection flags come only from the deterministic detector.
"""

from __future__ import annotations

import re
from typing import Protocol

import structlog

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.prompts import get_prompt
from app.llm.structured import StructuredLLM
from app.rag.prompt_builder import build_understanding_prompt
from app.rag.types import (
    ConversationContext,
    Intent,
    QueryAnalysis,
    QueryUnderstanding,
    QuestionType,
    SensitiveCategory,
)
from app.security.prompt_injection import detect_injection
from app.utils.text import content_terms, normalize_unicode

log = structlog.get_logger(__name__)

# Stemmed words that describe the *asking* rather than the subject; they would only
# dilute evidence-coverage measurement.
_GENERIC_TERMS = frozenset(
    {"question", "information", "info", "detail", "explain", "describe", "anyone", "way",
     "possible", "exact", "regard", "curious", "wonder", "wondering", "someth", "anyth", "long"}
)  # fmt: skip

_I = re.IGNORECASE
_HANDOFF = re.compile(
    r"\b(human|real person|live (agent|person|chat|support)|(support|customer service) "
    r"(agent|rep|representative|team)|representative|talk to (someone|somebody|a person|an agent|support|a human)"
    r"|speak (to|with) (someone|somebody|a person|an agent|a manager|support|a human)"
    r"|connect me|transfer me|escalate|manager|supervisor|operator)\b",
    _I,
)
_SHORT_AGENT = re.compile(r"\bagent\b", _I)
_FRUSTRATION = re.compile(
    r"\b(useless|ridiculous|terrible|horrible|awful|worst|furious|angry|pissed|fed up|sick of"
    r"|waste of (my )?time|unacceptable|absurd|stupid|idiot|wtf|damn|crap)\b|!{3,}",
    _I,
)
_DISPUTE = re.compile(
    r"\b(that'?s|that is|this is|you'?re|you are|your answer is|it'?s|it is) "
    r"(wrong|incorrect|not (true|right|correct|accurate))\b|\b(no|nope),? (that'?s|it'?s) not\b"
    r"|\bthat'?s not what\b|\byou'?re mistaken\b|\bnot what (the|your) (policy|website|docs?|documentation) says\b",
    _I,
)
_SENSITIVE: list[tuple[SensitiveCategory, re.Pattern[str]]] = [
    ("security_incident", re.compile(
        r"\b(hack(ed|ing)?|compromised|data breach|breach|stolen|fraud(ulent)?|unauthori[sz]ed "
        r"(charge|access|login|transaction|purchase)s?|phishing|someone (logged|got) into|identity theft)\b", _I)),
    ("identity_verification", re.compile(
        r"\b(verify my identity|identity verification|prove (it'?s|that it'?s|i am) me|kyc)\b", _I)),
    ("account_ownership", re.compile(
        r"\b(transfer (my |the )?account|account owner(ship)?|change (the )?(account )?owner"
        r"|someone else'?s account|(deceased|late) (husband|wife|parent|father|mother)'?s? account)\b", _I)),
    ("legal", re.compile(
        r"\b(lawsuit|sue|suing|attorney|lawyer|legal action|court|small claims|liabilit(y|ies)|subpoena)\b", _I)),
    ("financial", re.compile(
        r"\b(investment advice|should i invest|tax advice|tax (deduction|implications?)|financial advice"
        r"|credit score|bankrupt(cy)?)\b", _I)),
    ("medical", re.compile(
        r"\b(medical advice|diagnos(is|e)|symptoms?|medication|allergic reaction|injur(y|ed|ies)"
        r"|health (condition|risk)|doctor)\b", _I)),
]  # fmt: skip
_ACCOUNT_STRONG = re.compile(
    r"\b(where('?s| is) my (order|package|parcel|delivery|shipment|refund)"
    r"|order\s*(#|no\.?|number)\s*[a-z0-9-]{3,}|i (was|have been|got) (double[- ])?charged"
    r"|(hasn'?t|has not|haven'?t|have not|didn'?t|did not|never) (arrived|been (delivered|refunded|shipped)))\b"
    r"|#\d{4,}",
    _I,
)
_ACCOUNT_WEAK = re.compile(
    r"\b((check|look up|look into|find|track) my (order|package|account|refund|payment|delivery|shipment)"
    r"|(cancel|refund|change|update|reset|delete|close|reactivate|unlock) my "
    r"(order|account|subscription|payment|address|email|password|card))\b",
    _I,
)
_PROCEDURAL = re.compile(
    r"^\s*(how (do|can|would|should|to)|what('?s| is| are) the (steps?|process|way)|can (i|we|you)"
    r"|is it possible|do you|does|is there a way|what happens)\b",
    _I,
)
_GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|greetings)( there)?[\s!.,]*$", _I)
_THANKS = re.compile(
    r"^\s*(thanks|thank you|thx|ty|great|perfect|awesome|got it|ok(ay)?|cool)"
    r"( (so much|very much|a lot))?[\s!.,]*$",
    _I,
)
_FOLLOW_UP = re.compile(
    r"^\s*(?:(?:what|how) about|and(?: what about)?|(?:same|also) for)\s+(?P<rest>.+?)[?.!\s]*$", _I
)
_PRONOUN = re.compile(r"\b(it|that|this|they|them|those|these|there|one)\b", _I)
_TRAILING_CLAUSE = re.compile(r"\s+(for|regarding|about|on|with|in)\s+(\S+\s*){1,5}$", _I)
_ENTITY = re.compile(
    r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)\b|\b([A-Za-z]+\d+[A-Za-z0-9]*)\b"
)


class QueryAnalyzer(Protocol):
    name: str

    async def analyze(self, message: str, context: ConversationContext) -> QueryAnalysis: ...


def key_terms_for(query: str) -> list[str]:
    return list(dict.fromkeys(t for t in content_terms(query) if t not in _GENERIC_TERMS))


def entity_terms(entities: list[str]) -> list[str]:
    """Terms naming the specific things a question is about; evidence must mention them."""
    return key_terms_for(" ".join(entities))


def _question_type(message: str) -> QuestionType:
    lowered = message.lower()
    if re.search(r"\b(not working|error|broken|won'?t|doesn'?t work|fail(s|ed|ing)?|troubleshoot)\b", lowered):
        return "TROUBLESHOOTING"
    if re.search(r"\b(how (do|can|to)|steps?|process|set ?up|install)\b", lowered):
        return "PROCEDURAL"
    if re.search(r"\b(polic(y|ies)|refund|return|warrant(y|ies)|terms|eligib|guarantee)\b", lowered):
        return "POLICY"
    return "FACTUAL"


class RuleBasedQueryAnalyzer:
    name = "rules"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze(self, message: str, context: ConversationContext) -> QueryAnalysis:
        return self.analyze_sync(message, context)

    def analyze_sync(self, message: str, context: ConversationContext) -> QueryAnalysis:
        text = normalize_unicode(message).strip()
        word_count = len(text.split())
        previous = context.last_user_query()

        standalone, needs_context = self._rewrite(text, previous)
        key_terms = key_terms_for(standalone)

        handoff_requested = bool(_HANDOFF.search(text)) or (word_count <= 6 and bool(_SHORT_AGENT.search(text)))
        letters = [c for c in text if c.isalpha()]
        shouting = len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.8
        frustration = bool(_FRUSTRATION.search(text)) or shouting
        last_assistant = context.last_assistant_turn()
        disputes = bool(_DISPUTE.search(text)) and last_assistant is not None
        sensitive: SensitiveCategory = next((cat for cat, rx in _SENSITIVE if rx.search(text)), "none")
        account_action = bool(_ACCOUNT_STRONG.search(text)) or (
            bool(_ACCOUNT_WEAK.search(text)) and not _PROCEDURAL.match(text)
        )

        if handoff_requested:
            intent = Intent.HANDOFF_REQUEST
        elif _GREETING.match(text):
            intent = Intent.GREETING
        elif _THANKS.match(text):
            intent = Intent.THANKS
        elif account_action:
            intent = Intent.ACCOUNT_ACTION
        else:
            intent = Intent.QUESTION

        # Nothing to search for, or only a pronoun ("How long does it take?") with no earlier
        # turn to resolve it against.
        unresolved_pronoun = previous is None and bool(_PRONOUN.search(text)) and len(key_terms) <= 1
        requires_clarification = intent is Intent.QUESTION and not disputes and (not key_terms or unresolved_pronoun)
        entities = list(dict.fromkeys(m.group(0) for m in _ENTITY.finditer(text)))[:10]
        return QueryAnalysis(
            intent=intent,
            standalone_query=standalone,
            needs_context=needs_context,
            requires_clarification=requires_clarification,
            clarification_question=(
                "Could you tell me a bit more about what you need help with?" if requires_clarification else None
            ),
            entities=entities,
            question_type="ACCOUNT" if account_action else _question_type(text),
            out_of_domain=False,
            frustration=frustration,
            disputes_previous_answer=disputes,
            sensitive_category=sensitive,
            handoff_requested=handoff_requested,
            injection_flags=detect_injection(text),
            key_terms=key_terms,
            analyzer=self.name,
        )

    @staticmethod
    def _rewrite(text: str, previous: str | None) -> tuple[str, bool]:
        """Resolve elliptical follow-ups against the previous standalone question."""
        if not previous:
            return text, False
        follow_up = _FOLLOW_UP.match(text)
        if follow_up:
            base = _TRAILING_CLAUSE.sub("", previous.strip().rstrip("?.! "))
            return f"{base} for {follow_up.group('rest')}?", True
        own_terms = key_terms_for(text)
        if len(own_terms) <= 3 and _PRONOUN.search(text):
            return f"{text.rstrip('?.! ')} (regarding: {previous.strip().rstrip('?.! ')})?", True
        return text, False


class LLMQueryAnalyzer:
    name = "llm"

    def __init__(self, llm: StructuredLLM, rules: RuleBasedQueryAnalyzer, settings: Settings) -> None:
        self.llm = llm
        self.rules = rules
        self.settings = settings
        self.last_usage: tuple[int, int, str | None] = (0, 0, None)

    async def analyze(self, message: str, context: ConversationContext) -> QueryAnalysis:
        rules = self.rules.analyze_sync(message, context)
        self.last_usage = (0, 0, None)
        try:
            result = await self.llm.generate(
                prompt=get_prompt("query_understanding"),
                user=build_understanding_prompt(message, context, self.settings),
                output_model=QueryUnderstanding,
                max_tokens=2000,
            )
        except LLMError as exc:
            log.warning("llm_query_understanding_failed_using_rules", error=exc.detail)
            return rules
        self.last_usage = (result.usage.input_tokens, result.usage.output_tokens, result.prompt_version)
        llm = result.value
        standalone = llm.standalone_query.strip() or rules.standalone_query
        sensitive = rules.sensitive_category if rules.sensitive_category != "none" else llm.sensitive_category
        intent = rules.intent if rules.intent in (Intent.HANDOFF_REQUEST, Intent.ACCOUNT_ACTION) else llm.intent
        return QueryAnalysis(
            intent=intent,
            standalone_query=standalone,
            needs_context=llm.needs_context or rules.needs_context,
            requires_clarification=llm.requires_clarification and not rules.handoff_requested,
            clarification_question=llm.clarification_question,
            entities=list(dict.fromkeys([*llm.entities, *rules.entities]))[:10],
            question_type=llm.question_type,
            out_of_domain=llm.out_of_domain,
            frustration=llm.frustration or rules.frustration,
            disputes_previous_answer=(llm.disputes_previous_answer or rules.disputes_previous_answer)
            and context.last_assistant_turn() is not None,
            sensitive_category=sensitive,
            handoff_requested=llm.handoff_requested or rules.handoff_requested,
            injection_flags=rules.injection_flags,
            key_terms=key_terms_for(standalone) or rules.key_terms,
            analyzer=self.name,
        )
