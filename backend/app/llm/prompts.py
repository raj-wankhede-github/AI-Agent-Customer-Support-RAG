"""Versioned prompt registry.

Prompts live in `app/prompts/<name>.md` with a small front-matter header (name, version,
purpose). The version is recorded on every message and RAG trace so any answer can be
traced back to the exact prompt text that produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    purpose: str
    system: str

    @property
    def version_id(self) -> str:
        return f"{self.name}@{self.version}"


def _parse(path: Path) -> PromptTemplate:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"Prompt {path.name} is missing its front-matter header")
    header, _, body = raw[4:].partition("\n---\n")
    meta = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    for key in ("name", "version", "purpose"):
        if key not in meta:
            raise ValueError(f"Prompt {path.name} front matter lacks '{key}'")
    return PromptTemplate(meta["name"], meta["version"], meta["purpose"], body.strip())


@lru_cache
def load_prompts() -> dict[str, PromptTemplate]:
    prompts = {}
    for path in sorted(PROMPT_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        template = _parse(path)
        prompts[template.name] = template
    return prompts


def get_prompt(name: str, canary: str | None = None) -> PromptTemplate:
    template = load_prompts()[name]
    if canary is None:
        return template
    # The canary lets the output filter detect a response that echoes system instructions.
    system = f"{template.system}\n\nConfidential marker (never output it): {canary}"
    return PromptTemplate(template.name, template.version, template.purpose, system)


def derive_canary(secret: str) -> str:
    return "CNRY-" + hashlib.sha256(f"canary:{secret}".encode()).hexdigest()[:16]
