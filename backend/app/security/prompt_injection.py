"""Heuristic prompt-injection detection.

This is one layer of defense, not the defense. The architecture assumes detection will
miss things: retrieved text is always wrapped and labelled as untrusted data, the model
has no tools or secrets to leak, outputs are schema-constrained and grounding-validated,
and a canary check blocks any response that echoes system instructions.
"""

from __future__ import annotations

import re

_PATTERNS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|system)\b"
        r"[^.\n]{0,20}\b(instructions?|prompts?|rules?|directions?|guidelines?)\b",
        re.IGNORECASE,
    ),
    "reveal_prompt": re.compile(
        r"\b(reveal|show|print|display|repeat|output|leak|tell me)\b[^.\n]{0,40}"
        r"\b(system prompt|hidden prompt|instructions|developer message|initial prompt)\b",
        re.IGNORECASE,
    ),
    "role_hijack": re.compile(
        r"\b(you are now|act as|pretend to be|from now on you|new persona|developer mode|jailbreak|DAN)\b",
        re.IGNORECASE,
    ),
    "fake_system_turn": re.compile(
        r"(^|\n)\s*(system|assistant|developer)\s*:|<\s*/?\s*(system|instructions?)\s*>",
        re.IGNORECASE,
    ),
    "secret_exfiltration": re.compile(
        r"\b(api[_ ]?key|password|secret|credentials?|access token)s?\b[^.\n]{0,30}"
        r"\b(send|give|reveal|share|print|show)\b|\b(send|give|reveal|share|print|show)\b[^.\n]{0,30}"
        r"\b(api[_ ]?keys?|passwords?|secrets?|credentials?|access tokens?)\b",
        re.IGNORECASE,
    ),
}


def detect_injection(text: str) -> list[str]:
    """Return the names of injection patterns found in `text` (empty when clean)."""
    return [name for name, pattern in _PATTERNS.items() if pattern.search(text)]
