"""Data models for compiled memory artifacts.

A compiled memory batch is a set of ``Fact`` records (one of which may be a
``Decision``) plus a manifest. Everything is plain, serialisable data so the
bundles stay human-readable and versionable on disk.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# Patterns that mark a span as a decision rather than a plain fact.
_DECISION_PATTERNS = [
    re.compile(r"\bdecision\b", re.I),
    re.compile(r"\bwe (?:decided|agreed|chose|settled on)\b", re.I),
    re.compile(r"\b(?:use|adopt|implement|go with|pick)\b .+\bfor\b", re.I),
]


@dataclass
class Fact:
    """A single extracted unit of knowledge."""

    content: str
    tags: List[str] = field(default_factory=list)
    date: Optional[str] = None
    source: Optional[str] = None  # provenance: which material it came from
    is_decision: bool = False
    confidence: float = 1.0

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "Fact":
        return cls(
            content=d.get("content", ""),
            tags=list(d.get("tags", [])),
            date=d.get("date"),
            source=d.get("source"),
            is_decision=bool(d.get("is_decision", False)),
            confidence=float(d.get("confidence", 1.0)),
        )


@dataclass
class Decision(Fact):
    """A Fact that records a resolved choice."""

    is_decision: bool = True


@dataclass
class ArtifactBundle:
    """The result of a single compile pass: facts + index + manifest."""

    version: str
    facts: List[Fact] = field(default_factory=list)
    index: Dict = field(default_factory=dict)
    manifest: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "facts": [f.to_dict() for f in self.facts],
            "index": self.index,
            "manifest": self.manifest,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "ArtifactBundle":
        return cls(
            version=d.get("version", "v1"),
            facts=[Fact.from_dict(x) for x in d.get("facts", [])],
            index=d.get("index", {}),
            manifest=d.get("manifest", {}),
        )


def _looks_like_decision(content: str) -> bool:
    return any(p.search(content) for p in _DECISION_PATTERNS)


def classify(content: str) -> bool:
    """Return True if the span reads as a decision."""
    return _looks_like_decision(content)


def to_jsonl(facts: List[Fact]) -> str:
    return "\n".join(json.dumps(f.to_dict(), ensure_ascii=False) for f in facts)
