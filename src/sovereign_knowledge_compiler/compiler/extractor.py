"""Extractor: turn raw material into Fact records.

Deterministic, dependency-free extraction (regex + keyword heuristics). The
blog post is explicit that not everything needs an LLM pass -- cheap structured
extraction is the right default, with the local-model "Knowledge Compiler" step
reserved for deeper synthesis. This module is the cheap, always-on extractor.
"""

from __future__ import annotations

import re
from typing import Dict, List

from ..artifacts.types import Fact, classify

# Heuristic tag vocabulary mapped to trigger keywords.
_TAG_KEYWORDS: Dict[str, List[str]] = {
    "technology": ["postgres", "mongodb", "fastapi", "flask", "sqlalchemy",
                   "python", "rust", "react", "kubernetes", "docker", "ollama",
                   "chromadb", "qdrant", "llm", "api", "database", "redis"],
    "architecture": ["architecture", "pipeline", "compiler", "runtime", "layer",
                     "service", "module", "graph", "index", "sync"],
    "process": ["deadline", "sprint", "roadmap", "milestone", "review",
                "decision", "agreed", "meeting"],
    "people": ["alice", "bob", "team", "owner", "user", "client"],
}


def _sentences(text: str) -> List[str]:
    # split on sentence boundaries, keep them non-empty
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _tag_sentence(sentence: str) -> List[str]:
    low = sentence.lower()
    tags = []
    for tag, kws in _TAG_KEYWORDS.items():
        if any(kw in low for kw in kws):
            tags.append(tag)
    return tags


def extract_facts(material: List[Dict]) -> List[Fact]:
    """Extract Fact records from a list of material dicts.

    Each material item is ``{"type": str, "date": str, "content": str}``.
    Returns one Fact per sentence, tagged heuristically, with decisions flagged.
    """
    facts: List[Fact] = []
    for item in material or []:
        content = item.get("content", "")
        date = item.get("date")
        source = item.get("type", "material")
        for sent in _sentences(content):
            if not sent:
                continue
            tags = _tag_sentence(sent)
            facts.append(
                Fact(
                    content=sent,
                    tags=tags,
                    date=date,
                    source=source,
                    is_decision=classify(sent),
                )
            )
    return facts
