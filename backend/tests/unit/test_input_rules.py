"""Request-schema limits, pagination maths and the small rules that decide user-visible text.

Unit level: no database, no network. Each rule is checked with a value that must be accepted
and a value that must be rejected, so neither an over-strict nor an over-permissive change
passes unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.enums import UserRole
from app.schemas.admin import AgentReplyRequest, RetrievalDebugRequest
from app.schemas.common import Page, error_responses
from app.schemas.conversation import ConversationCreate, FeedbackRequest, HandoffRequest, SendMessageRequest
from app.services.chat import _normalize_question
from app.services.lifecycle import display_name
from tests.conftest import make_settings


def test_message_length_limits():
    assert SendMessageRequest(content="a" * 4000).content
    for invalid in ("", "a" * 4001):
        with pytest.raises(ValidationError):
            SendMessageRequest(content=invalid)


def test_client_message_id_is_bounded():
    assert SendMessageRequest(content="hi", client_message_id="x" * 100).client_message_id
    with pytest.raises(ValidationError):
        SendMessageRequest(content="hi", client_message_id="x" * 101)


def test_conversation_and_handoff_fields_are_bounded():
    assert ConversationCreate(title="t" * 200, first_message="m" * 4000).title
    assert ConversationCreate().first_message is None
    with pytest.raises(ValidationError):
        ConversationCreate(title="t" * 201)
    with pytest.raises(ValidationError):
        ConversationCreate(first_message="m" * 4001)
    assert HandoffRequest(note="n" * 500).note
    with pytest.raises(ValidationError):
        HandoffRequest(note="n" * 501)


def test_feedback_requires_a_known_rating_and_reason():
    import uuid

    message_id = uuid.uuid4()
    assert FeedbackRequest(message_id=message_id, rating="HELPFUL").reason is None
    assert FeedbackRequest(message_id=message_id, rating="NOT_HELPFUL", reason="INCORRECT").reason
    for invalid in ({"rating": "MAYBE"}, {"rating": "HELPFUL", "reason": "BECAUSE"}, {}):
        with pytest.raises(ValidationError):
            FeedbackRequest(message_id=message_id, **invalid)
    with pytest.raises(ValidationError):
        FeedbackRequest(message_id=message_id, rating="HELPFUL", comment="c" * 1001)


def test_staff_input_is_bounded():
    assert AgentReplyRequest(content="a" * 4000).content
    assert RetrievalDebugRequest(query="q" * 1000).query
    for invalid in ("", "q" * 1001):
        with pytest.raises(ValidationError):
            RetrievalDebugRequest(query=invalid)
    with pytest.raises(ValidationError):
        AgentReplyRequest(content="")


@pytest.mark.parametrize(
    ("total", "page_size", "expected_pages"),
    [(0, 20, 1), (1, 20, 1), (20, 20, 1), (21, 20, 2), (100, 7, 15)],
)
def test_page_reports_the_number_of_pages(total: int, page_size: int, expected_pages: int):
    page = Page.build([], total, 1, page_size)
    assert page.pages == expected_pages and page.total == total and page.page_size == page_size


def test_error_responses_document_the_requested_codes():
    documented = error_responses(401, 404, 429)
    assert set(documented) == {401, 404, 429}
    assert all("model" in entry and entry["description"] for entry in documented.values())
    with pytest.raises(KeyError):
        error_responses(418)


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("How do I reset my password?", "  how do I reset my password  ", True),
        ("How do I reset my password?", "How do I reset my password!", True),
        ("How do I reset my password?", "How do I reset my PIN?", False),
        ("Where is my order?", "Where is my order number 12?", False),
    ],
)
def test_opening_questions_are_compared_ignoring_case_spacing_and_punctuation(first: str, second: str, same: bool):
    assert (_normalize_question(first) == _normalize_question(second)) is same


def test_staff_are_shown_by_first_name_and_customers_by_their_own_name():
    assert display_name("Sam Agent", UserRole.AGENT) == "Sam (Support)"
    assert display_name("Avery Admin", UserRole.ADMIN) == "Avery (Support)"
    assert display_name("Casey Customer", UserRole.CUSTOMER) == "Casey Customer"


def test_s3_storage_requires_a_bucket(tmp_path: Path):
    with pytest.raises(ValidationError):
        make_settings(tmp_path, storage_provider="s3", s3_bucket=None)
    assert make_settings(tmp_path, storage_provider="s3", s3_bucket="documents").s3_bucket == "documents"


def test_production_requires_a_strong_secret_and_explicit_origins(tmp_path: Path):
    base = {
        "app_env": "production",
        "database_url": "postgresql+asyncpg://u:p@db/support",
        "llm_provider": "none",
        "embedding_provider": "hashing",
        "storage_local_path": str(tmp_path),
        "auth_cookie_secure": True,
        "cors_allowed_origins": "https://support.example",
    }
    with pytest.raises(ValidationError):
        Settings(**base, jwt_secret="short")
    assert Settings(**base, jwt_secret="s" * 48).app_env == "production"
