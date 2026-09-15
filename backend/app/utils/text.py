"""Deterministic text utilities shared by chunking, retrieval, reranking and validation.

Everything here is dependency-free (English-oriented) on purpose: exactly the same
normalization must be applied at index time, ranking time and validation time.
"""

from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "can't",
        "cannot",
        "could",
        "couldn't",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "have",
        "haven't",
        "having",
        "he",
        "he'd",
        "he'll",
        "he's",
        "her",
        "here",
        "here's",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "how's",
        "i",
        "i'd",
        "i'll",
        "i'm",
        "i've",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "it's",
        "its",
        "itself",
        "let's",
        "me",
        "more",
        "most",
        "mustn't",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "she'd",
        "she'll",
        "she's",
        "should",
        "shouldn't",
        "so",
        "some",
        "such",
        "than",
        "that",
        "that's",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "there's",
        "these",
        "they",
        "they'd",
        "they'll",
        "they're",
        "they've",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "wasn't",
        "we",
        "we'd",
        "we'll",
        "we're",
        "we've",
        "were",
        "weren't",
        "what",
        "what's",
        "when",
        "when's",
        "where",
        "where's",
        "which",
        "while",
        "who",
        "who's",
        "whom",
        "why",
        "why's",
        "with",
        "won't",
        "would",
        "wouldn't",
        "you",
        "you'd",
        "you'll",
        "you're",
        "you've",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "please",
        "thanks",
        "thank",
        "hi",
        "hello",
        "hey",
        "tell",
        "know",
        "need",
        "want",
        "like",
        "get",
        "got",
        "also",
        "just",
        "us",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "much",
        "many",
        "anything",
        "something",
        "ok",
        "okay",
        "yes",
    ]
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_NUMBER_RE = re.compile(r"(?<![a-z])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![a-z])\d+(?:\.\d+)?")
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}  # fmt: skip
_SUFFIXES = ("ing", "edly", "ed", "ly")
# "-es" is a plural ending only after these (boxes, taxes, watches); elsewhere just "-s" is
# (purchases -> purchase, prices -> price).
_ES_PLURALS = ("xes", "zes", "ches", "shes")


# En/em dash and minus sign -> hyphen; curly quotes -> straight quotes.
_TYPOGRAPHY = str.maketrans({0x2013: "-", 0x2014: "-", 0x2212: "-", 0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'})


def normalize_unicode(text: str) -> str:
    """NFKC plus unified dashes/quotes, so an en-dash range and a hyphen range compare equal."""
    return unicodedata.normalize("NFKC", text).translate(_TYPOGRAPHY)


def stem(word: str) -> str:
    """Tiny suffix stripper: consistent rather than linguistically perfect."""
    if len(word) <= 3 or word.isdigit():
        return word
    # Plural first ("meanings" -> "meaning"), then verb/adverb endings ("meaning" -> "mean").
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith(("sses", *_ES_PLURALS)):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    stripped = False
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            stripped = True
            break
    if stripped and len(word) > 3 and word[-1] == word[-2] and word[-1] not in "lsz":
        word = word[:-1]  # shipping -> shipp -> ship
    return word


def words(text: str) -> list[str]:
    return _WORD_RE.findall(normalize_unicode(text).lower())


def content_terms(text: str) -> list[str]:
    """Stemmed, stopword-free terms in order (duplicates kept)."""
    terms = []
    for word in words(text):
        if word in STOPWORDS:
            continue
        word = word.removesuffix("'s")
        if len(word) > 1:
            terms.append(stem(word))
    return terms


def term_set(text: str) -> set[str]:
    return set(content_terms(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def split_sentences(text: str) -> list[str]:
    """Split into sentences; each line (list item, heading, table row) is its own unit."""
    sentences: list[str] = []
    for raw_line in normalize_unicode(text).splitlines():
        line = _LIST_MARKER_RE.sub("", raw_line)
        line = re.sub(r"^#+\s*", "", line).strip()
        if line:
            sentences.extend(s.strip() for s in _SENTENCE_RE.split(line) if s.strip())
    return sentences


def extract_numbers(text: str) -> set[str]:
    """Canonical numeric values: '3-5 days' -> {'3','5'}, '$1,200.00' -> {'1200'}, 'thirty' -> {'30'}."""
    normalized = normalize_unicode(text).lower()
    found: set[str] = set()
    for match in _NUMBER_RE.findall(normalized):
        value = float(match.replace(",", ""))
        found.add(str(int(value)) if value.is_integer() else str(value))
    for word in words(normalized):
        if word in _NUMBER_WORDS:
            found.add(str(_NUMBER_WORDS[word]))
    return found


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English; used for budgeting, not billing."""
    return max(1, len(text) // 4)


def truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"
