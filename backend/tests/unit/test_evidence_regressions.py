"""Regression tests for grounding gaps found while running the golden evaluation."""

from app.models.enums import AnswerStatus
from app.rag.evidence import select_evidence
from app.rag.generator import ExtractiveAnswerGenerator, GenerationRequest
from app.rag.query_analysis import RuleBasedQueryAnalyzer, entity_terms, key_terms_for
from app.rag.types import ConversationContext, EvidenceItem
from app.utils.text import stem
from tests.helpers import build_agent, make_chunk
from tests.unit.test_support_agent import ask


def test_plural_stemming_matches_singular_forms():
    assert stem("purchases") == stem("purchase") == "purchase"
    assert stem("prices") == stem("price")
    assert stem("taxes") == stem("tax") == "tax"
    assert stem("addresses") == stem("address") == "address"
    assert stem("business") == "business" and stem("status") == "status"


def test_evidence_must_mention_the_specific_entity_asked_about(settings):
    shipping = make_chunk("Shipping Policy", "Standard shipping takes 3-5 business days.", section="Standard Shipping")
    shipping.rerank_score = 0.8
    analysis = RuleBasedQueryAnalyzer(settings).analyze_sync(
        "How long does shipping to Antarctica take?", ConversationContext()
    )
    assert "antarctica" in entity_terms(analysis.entities)
    result = select_evidence([shipping], analysis.key_terms, settings, required_terms=entity_terms(analysis.entities))
    assert not result.sufficient and result.reason_code == "MISSING_ENTITY"

    covered = select_evidence(
        [shipping], key_terms_for("How long does standard shipping take?"), settings, required_terms=[]
    )
    assert covered.sufficient


async def test_agent_abstains_for_an_unknown_destination(settings):
    result = await build_agent(settings).respond(ask("How long does shipping to Antarctica take?"))
    assert result.status is AnswerStatus.ABSTAINED and "business days" not in result.content


def test_pronoun_question_without_context_asks_for_clarification(settings):
    analysis = RuleBasedQueryAnalyzer(settings).analyze_sync("How long does it take?", ConversationContext())
    assert analysis.requires_clarification


async def test_short_list_item_answer_keeps_its_heading():
    chunk = make_chunk(
        "Refund and Returns Policy",
        "- Gift cards\n- Items marked as final sale",
        section="Items That Cannot Be Returned",
    )
    chunk.rerank_score = 0.6
    question = "Can gift cards be returned?"
    request = GenerationRequest(
        question, question, key_terms_for(question), [EvidenceItem("S1", chunk)], ConversationContext()
    )
    result = await ExtractiveAnswerGenerator().generate(request)
    assert result.answer.answer == "Items That Cannot Be Returned: Gift cards."


async def test_heading_that_names_the_subject_beats_an_incidental_mention(settings):
    hours = make_chunk("Support Contact Policy", "Acme Support is available Monday to Friday from 8:00 a.m. to 8:00 p.m.",
                       section="SUPPORT HOURS", authority="OFFICIAL_DOCUMENTATION")  # fmt: skip
    channels = make_chunk("Support Contact Policy", "Phone: 1-800-555-0199 during support hours.", section="CONTACT CHANNELS",
                          authority="OFFICIAL_DOCUMENTATION")  # fmt: skip
    result = await build_agent(settings, [channels, hours]).respond(ask("What are your support hours?"))
    assert result.status is AnswerStatus.ANSWERED and "8:00 a.m." in result.content
