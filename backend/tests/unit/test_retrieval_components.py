import math
from datetime import date

from app.models.enums import ConfidenceLevel
from app.rag.confidence import assess_confidence
from app.rag.conflicts import detect_conflicts, resolve_conflicts
from app.rag.embeddings.hashing import HashingEmbeddingProvider
from app.rag.evidence import select_evidence
from app.rag.query_analysis import key_terms_for
from app.rag.reranker import HeuristicReranker
from tests.helpers import make_chunk

TODAY = date(2026, 9, 15)


async def test_hashing_embeddings_are_deterministic_normalized_and_topical():
    embedder = HashingEmbeddingProvider(384)
    a, b = await embedder.embed_documents(["Standard shipping takes 3-5 days", "Standard shipping takes 3-5 days"])
    assert a == b
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-6)
    query = await embedder.embed_query("how long does standard shipping take")
    shipping = await embedder.embed_query("Standard shipping takes 3-5 business days")
    password = await embedder.embed_query("Reset your password from the sign-in page")

    def cosine(x, y):
        return sum(i * j for i, j in zip(x, y, strict=True))

    assert cosine(query, shipping) > cosine(query, password)


async def test_reranker_prefers_coverage_and_removes_duplicates(settings):
    relevant = make_chunk("Shipping Policy", "Standard shipping takes 3-5 business days.", section="Standard Shipping")
    duplicate = make_chunk(
        "Shipping Policy (copy)", "Standard shipping takes 3-5 business days.", section="Standard Shipping"
    )
    unrelated = make_chunk(
        "Account FAQ", "Reset your password from the sign-in page.", section="Passwords", authority="FAQ"
    )
    relevant.vector_similarity, duplicate.vector_similarity, unrelated.vector_similarity = 0.5, 0.5, 0.4
    ranked = await HeuristicReranker(settings).rerank(
        "standard shipping time",
        key_terms_for("What is your standard shipping time?"),
        [unrelated, relevant, duplicate],
    )
    assert ranked[0].content == relevant.content
    assert len([c for c in ranked if c.content == relevant.content]) == 1
    assert ranked[0].rerank_score > ranked[-1].rerank_score
    assert set(ranked[0].matched_terms) == {"standard", "ship"}


def test_evidence_sufficiency_outcomes(settings):
    keys = ["standard", "ship", "time"]
    assert select_evidence([], keys, settings).reason_code == "NO_RELEVANT_EVIDENCE"

    weak = make_chunk("Shipping Policy", "Standard shipping takes 3-5 business days.", section="Standard Shipping")
    weak.rerank_score = 0.1
    assert select_evidence([weak], keys, settings).reason_code == "LOW_RELEVANCE"

    off_topic = make_chunk("Account FAQ", "Your password reset link expires after 60 minutes.", section="Passwords")
    off_topic.rerank_score = 0.5
    result = select_evidence([off_topic], ["ceo", "favorite", "food"], settings)
    assert result.reason_code == "INSUFFICIENT_COVERAGE" and result.missing_terms == ["ceo", "favorite", "food"]

    good = make_chunk("Shipping Policy", "Standard shipping takes 3-5 business days.", section="Standard Shipping")
    good.rerank_score = 0.7
    result = select_evidence([good], keys, settings)
    assert result.sufficient and result.selected == [good] and round(result.coverage, 2) == 0.67


def _return_window_chunks(old_authority="FAQ", new_authority="OFFICIAL_POLICY", old_date=None, new_date=None):
    old = make_chunk(
        "Gadget Pro Returns FAQ",
        "Customers can return a Gadget Pro within 30 days of delivery for a full refund.",
        section="Gadget Pro Return Period",
        authority=old_authority,
        effective_date=old_date,
    )
    new = make_chunk(
        "Gadget Pro Returns Policy",
        "Customers can return a Gadget Pro within 14 days of delivery for a full refund.",
        section="Gadget Pro Return Period",
        authority=new_authority,
        effective_date=new_date,
    )
    return old, new  # fmt: skip


KEYS = key_terms_for("How many days do I have to return a Gadget Pro?")


def test_conflict_resolved_by_source_authority(settings):
    old, new = _return_window_chunks()
    conflicts = detect_conflicts([old, new], KEYS, settings)
    assert len(conflicts) == 1
    kept, conflicts = resolve_conflicts(conflicts, [old, new], settings, TODAY)
    assert kept == [new]
    assert conflicts[0].resolved and conflicts[0].resolution == "higher_source_authority"


def test_conflict_resolved_by_latest_effective_date_for_equal_authority(settings):
    old, new = _return_window_chunks("OFFICIAL_POLICY", "OFFICIAL_POLICY", date(2024, 3, 1), date(2026, 6, 1))
    kept, conflicts = resolve_conflicts(detect_conflicts([old, new], KEYS, settings), [old, new], settings, TODAY)
    assert kept == [new] and conflicts[0].resolution == "more_recent_effective_date"


def test_conflict_left_unresolved_without_distinguishing_metadata(settings):
    old, new = _return_window_chunks("OFFICIAL_POLICY", "OFFICIAL_POLICY")
    kept, conflicts = resolve_conflicts(detect_conflicts([old, new], KEYS, settings), [old, new], settings, TODAY)
    assert len(kept) == 2 and not conflicts[0].resolved


def test_future_policy_does_not_override_current_one(settings):
    old, new = _return_window_chunks("OFFICIAL_POLICY", "OFFICIAL_POLICY", date(2024, 3, 1), date(2027, 1, 1))
    kept, conflicts = resolve_conflicts(detect_conflicts([old, new], KEYS, settings), [old, new], settings, TODAY)
    assert kept == [old] and conflicts[0].resolution == "other_source_not_yet_effective"


def test_different_facts_with_different_numbers_are_not_conflicts(settings):
    request = make_chunk(
        "Returns Policy", "You can request a refund within 30 days of delivery.", section="Return Window"
    )
    timing = make_chunk(
        "Refund FAQ", "Refunds are processed within 5 business days after we receive your return.", section="Timing"
    )
    same_document = make_chunk("Returns Policy", "Opened software can be returned within 7 days of delivery.", section="Software",
                               document_id=request.document_id)  # fmt: skip
    keys = key_terms_for("How long do refunds take?")
    assert detect_conflicts([request, timing, same_document], [*keys, "return"], settings) == []


def test_confidence_levels(settings):
    high = assess_confidence(settings, top_relevance=0.8, coverage=1.0, authority_weight=1.0, claim_support=1.0,
                             validation_passed=True, unresolved_conflict=False)  # fmt: skip
    medium = assess_confidence(settings, top_relevance=0.5, coverage=0.67, authority_weight=0.6, claim_support=0.6,
                               validation_passed=True, unresolved_conflict=False)  # fmt: skip
    low = assess_confidence(settings, top_relevance=0.35, coverage=0.6, authority_weight=0.1, claim_support=0.6,
                            validation_passed=True, unresolved_conflict=False)  # fmt: skip
    failed = assess_confidence(settings, top_relevance=0.9, coverage=1.0, authority_weight=1.0, claim_support=1.0,
                               validation_passed=False, unresolved_conflict=False)  # fmt: skip
    assert (high.level, medium.level, low.level, failed.level) == (
        ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW, ConfidenceLevel.ABSTAIN,
    )  # fmt: skip
