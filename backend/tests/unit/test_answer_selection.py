"""Answer selection regressions found by the golden evaluation (offline extractive mode)."""

from app.agents.support_agent import _best_excerpt
from app.models.enums import AnswerStatus
from app.utils.text import stem
from tests.helpers import build_agent, make_chunk
from tests.unit.test_support_agent import ask


def test_plural_is_removed_before_verb_endings():
    assert stem("meanings") == stem("meaning") == stem("mean") == "mean"
    assert stem("settings") == stem("setting") == "set"
    assert stem("blinking") == stem("blinks") == "blink"
    assert stem("processes") == stem("processing") == "process"


async def test_value_question_prefers_the_sentence_that_states_the_value(settings):
    channels = make_chunk(
        "Support Contact Policy",
        "Phone: 1-800-555-0199 during support hours.",
        section="CONTACT CHANNELS",
        authority="OFFICIAL_DOCUMENTATION",
    )
    never = make_chunk(
        "Support Contact Policy",
        "Acme Support will never ask for your full password or your full payment card number by email, chat or phone.",
        section="WHAT WE WILL NEVER ASK FOR",
        authority="OFFICIAL_DOCUMENTATION",
    )
    result = await build_agent(settings, [never, channels]).respond(ask("What is the support phone number?"))
    assert result.status is AnswerStatus.ANSWERED and "1-800-555-0199" in result.content


async def test_heading_naming_the_subject_is_matched_through_word_forms(settings):
    setup = make_chunk(
        "Acme SmartHub Product Guide",
        "Press and hold the pairing button on the back of the SmartHub for 5 seconds until the light blinks blue.",
        section="Setting Up Your SmartHub",
        authority="PRODUCT_DOCUMENTATION",
    )
    status = make_chunk(
        "Acme SmartHub Product Guide",
        "- Solid green: the SmartHub is connected and working normally.\n- Blinking blue: the SmartHub is in pairing mode.",
        section="Status Light Meanings",
        authority="PRODUCT_DOCUMENTATION",
    )
    result = await build_agent(settings, [setup, status]).respond(
        ask("What does a blinking blue light on the SmartHub mean?")
    )
    assert result.status is AnswerStatus.ANSWERED and "pairing mode" in result.content


def test_citation_excerpt_covers_every_claim_in_document_order():
    content = (
        "Standard shipping takes 3-5 business days after your order ships. "
        "Standard shipping is free for orders of $50 or more. Orders under $50 pay a flat fee of $5.99."
    )
    excerpt = _best_excerpt(
        content, ["Standard shipping is free for orders of $50 or more.", "Standard shipping takes 3-5 business days."]
    )
    assert excerpt.startswith("Standard shipping takes 3-5 business days") and "free for orders of $50" in excerpt
