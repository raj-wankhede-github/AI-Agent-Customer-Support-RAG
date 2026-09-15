"""Text normalization and metadata extraction applied to every extracted block."""

from __future__ import annotations

import re
from datetime import date, datetime

from app.utils.text import normalize_unicode

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile("[ 	" + chr(0xA0) + "]+")  # spaces, tabs, no-break spaces
_EFFECTIVE_DATE = re.compile(
    r"effective(?:\s+date)?(?:\s+(?:as of|on|from))?\s*[:\-]?\s*"
    r"(\d{4}-\d{2}-\d{2}|(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    text = normalize_unicode(text)
    text = _CONTROL.sub("", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_effective_date(text: str) -> date | None:
    match = _EFFECTIVE_DATE.search(text[:5000])
    if not match:
        return None
    raw = match.group(1)
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def title_from_filename(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename.replace("\\", "/").rsplit("/", 1)[-1])
    words = re.sub(r"[_\-]+", " ", stem).strip()
    return words.title() if words else "Untitled document"
