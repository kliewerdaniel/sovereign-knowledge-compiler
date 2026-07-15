"""Consolidator: deduplicate and merge facts at compile time.

The Knowledge Compiler pays the reasoning cost once. Part of that cost is
resolving redundancy and surface-level conflicts so the runtime never has to.
This module is the deterministic first pass: exact dedup + complementary merge
by content proximity; deeper conflict resolution is handled by the truth-
maintenance layer referenced in the paper (future work).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import List

from ..artifacts.types import Fact

_SIMILARITY_THRESHOLD = 0.85


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= _SIMILARITY_THRESHOLD


def consolidate(facts: List[Fact]) -> List[Fact]:
    """Return a consolidated list of facts.

    - Identical (or near-identical) facts collapse into one.
    - Complementary facts that share a tag and are similar merge their tags.
    - Empty input yields [].
    """
    if not facts:
        return []

    out: List[Fact] = []
    for f in facts:
        merged = False
        for i, existing in enumerate(out):
            if _similar(f.content, existing.content):
                # merge: keep the longer content, union tags, prefer a decision
                if len(f.content) > len(existing.content):
                    existing.content = f.content
                existing.tags = sorted(set(existing.tags) | set(f.tags))
                existing.is_decision = existing.is_decision or f.is_decision
                existing.date = existing.date or f.date
                merged = True
                break
        if not merged:
            out.append(
                Fact(
                    content=f.content,
                    tags=list(f.tags),
                    date=f.date,
                    source=f.source,
                    is_decision=f.is_decision,
                )
            )
    return out
