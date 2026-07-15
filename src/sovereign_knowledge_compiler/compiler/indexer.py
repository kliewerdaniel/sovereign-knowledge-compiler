"""Indexer: build the cheap lookup structures the runtime queries.

The whole point of compile-time memory is that the runtime does O(1)-ish
lookups against static artifacts instead of re-reasoning over raw material.
This module builds an inverted index keyed by tag, plus a keyword index over
fact content, so ``query``/``query_by_tag``/``query_date_range`` stay cheap.
"""

from __future__ import annotations

import re
from typing import Dict, List

from ..artifacts.types import Fact

_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.I)


def build_index(facts: List[Fact]) -> Dict:
    """Return ``{"tags": {tag: [fact_idx...]}, "facts": [fact...]}``.

    ``tags`` is the inverted index the runtime filters on; ``facts`` is the
    positional store so indices can be resolved back to Fact records.
    """
    tags_index: Dict[str, List[int]] = {}
    for idx, f in enumerate(facts):
        for tag in f.tags:
            tags_index.setdefault(tag, []).append(idx)

    return {
        "tags": tags_index,
        "facts": [
            {
                "content": f.content,
                "tags": f.tags,
                "date": f.date,
                "is_decision": f.is_decision,
            }
            for f in facts
        ],
    }


def keyword_tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())
