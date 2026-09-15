from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.normalize import extract_effective_date, normalize_text, title_from_filename
from app.ingestion.types import Block, ExtractedDocument
from app.utils.text import content_terms, extract_numbers, normalize_unicode, split_sentences, stem


def test_stemming_is_consistent_across_word_forms():
    assert stem("shipping") == stem("ships") == stem("shipped") == "ship"
    assert stem("policies") == "policy"
    assert stem("refunds") == stem("refund")


def test_content_terms_drop_stopwords_and_question_words():
    assert content_terms("What is your refund policy?") == ["refund", "policy"]


def test_numbers_are_canonicalized():
    assert extract_numbers("3" + chr(0x2013) + "5 business days") == {"3", "5"}
    assert extract_numbers("costs $1,200.00") == {"1200"}
    assert extract_numbers("within thirty days") == {"30"}


def test_unicode_dashes_and_quotes_are_unified():
    assert normalize_unicode("3" + chr(0x2013) + "5 " + chr(0x201C) + "days" + chr(0x201D)) == '3-5 "days"'


def test_sentences_split_across_list_items():
    assert split_sentences("- First item here.\n- Second item. Third sentence.") == [
        "First item here.",
        "Second item.",
        "Third sentence.",
    ]


def test_normalization_dehyphenates_and_collapses_whitespace():
    assert normalize_text("ship-\nping  takes\t3 days\n\n\n\nmore") == "shipping takes 3 days\n\nmore"


def test_effective_date_and_title_extraction():
    assert str(extract_effective_date("Effective date: 2026-01-01. Policy text")) == "2026-01-01"
    assert str(extract_effective_date("This policy is effective as of March 5, 2025.")) == "2025-03-05"
    assert title_from_filename("shipping-policy_v2.pdf") == "Shipping Policy V2"


def _doc(*blocks: Block) -> ExtractedDocument:
    return ExtractedDocument(blocks=list(blocks))


def test_chunks_never_cross_section_boundaries():
    document = _doc(
        Block("heading", "Policy", 1),
        Block("heading", "Shipping", 2),
        Block("paragraph", "Orders ship in 3 days."),
        Block("heading", "Returns", 2),
        Block("paragraph", "Return items within 30 days."),
    )
    chunks = StructureAwareChunker(350, 500, 50).chunk(document)
    assert [c.section_title for c in chunks] == ["Shipping", "Returns"]
    assert chunks[0].heading_path == "Policy > Shipping"
    assert "30 days" not in chunks[0].content
    assert [c.index for c in chunks] == [0, 1]


def test_oversized_paragraph_is_split_by_sentence_with_overlap_and_page():
    paragraph = " ".join(f"Rule number {i} explains a detailed requirement about deliveries." for i in range(80))
    document = _doc(Block("heading", "Rules", 1, page=1), Block("paragraph", paragraph, page=2))
    chunks = StructureAwareChunker(200, 300, 30).chunk(document)
    assert len(chunks) > 2
    assert all(c.token_count <= 300 for c in chunks)
    assert all(c.page_number == 2 for c in chunks)
    last_sentence = split_sentences(chunks[0].content)[-1]
    assert chunks[1].content.startswith(last_sentence)


def test_large_table_is_split_by_rows_repeating_the_header():
    rows = "\n".join(f"Item {i} | {i} days | a note describing this particular row" for i in range(120))
    document = _doc(Block("heading", "Rates", 1), Block("table", "Item | Days | Note\n" + rows))
    chunks = StructureAwareChunker(200, 300, 30).chunk(document)
    assert len(chunks) > 1
    assert all(c.content.startswith("Item | Days | Note") for c in chunks)


def test_small_list_stays_in_one_chunk():
    document = _doc(Block("heading", "Steps", 1), Block("list", "- Plug in\n- Open the app\n- Pair the device"))
    chunks = StructureAwareChunker(350, 500, 50).chunk(document)
    assert len(chunks) == 1
    assert chunks[0].metadata["block_kinds"] == ["list"]
