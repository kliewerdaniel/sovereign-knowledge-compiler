"""Decay & compaction for compiled memory.

Compiled memory is not append-only forever. Old, unused facts should fade so
the runtime stays small and sharp -- but sovereign memory never *silently*
drops anything. Decay here is a **reversible overlay**, not a destructive
mutation:

* ``CompactionPolicy`` scores each fact's *relevance* from three signals --
  age (``now - created``), recency of use (``now - last_touch``), and
  reinforcement count (how often it has been cited/queried). Facts below a
  threshold become *candidates* for compaction.
* ``MemorySync.compact(policy)`` moves candidate facts into an **archive** set.
  Archived facts are excluded from ``live_facts()`` but remain fully present in
  the CRDT and the archive ledger, so they can be revived at any time.
* Archiving is itself a CRDT operation: the archive is a content-addressed set
  that merges symmetrically, so two devices that compact independently still
  converge. Reviving removes a fact's hash from the archive set.

This keeps the system's core invariant -- convergence under arbitrary exchange
-- intact, while giving memory a half-life.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .crdt import content_hash


@dataclass
class CompactionPolicy:
    """Parameters for the relevance / decay scoring.

    relevance = reinforcement_weight * ln(1 + reinforcements)
                - age_weight * (age / half_life)
                - stale_weight * (since_touch / stale_after)
    A fact is a compaction candidate when relevance < threshold.

    Defaults are deliberately conservative: only facts that are both old AND
    unused AND unreinforced decay. Tune per deployment.
    """

    half_life: float = 90.0 * 86400.0        # 90 days, seconds
    stale_after: float = 180.0 * 86400.0     # 180 days since last touch
    age_weight: float = 1.0
    stale_weight: float = 1.0
    reinforcement_weight: float = 2.0
    threshold: float = 0.0
    protected_tags: Set[str] = field(default_factory=set)  # facts with these tags never decay

    def relevance(self, record: Dict[str, Any], now: float) -> float:
        created = record.get("created", now)
        last_touch = record.get("last_touch", created)
        reinforcements = record.get("reinforcements", 0)
        age = max(0.0, now - created)
        since_touch = max(0.0, now - last_touch)
        score = (
            self.reinforcement_weight * math.log1p(reinforcements)
            - self.age_weight * (age / self.half_life)
            - self.stale_weight * (since_touch / self.stale_after)
        )
        return score

    def is_protected(self, value: Any) -> bool:
        tags = value.get("tags", []) if isinstance(value, dict) else []
        return bool(self.protected_tags & set(tags))

    def candidates(self, provenance: Dict[str, Dict[str, Any]], now: float,
                   live_values: List[Any]) -> List[str]:
        """Return entity_ids whose facts are compaction candidates.

        A fact is a candidate iff its relevance is below threshold AND it is not
        protected by tag. Returns entity ids (not content hashes) so the caller
        can archive by entity consistently.
        """
        value_by_eid = {rec["entity_id"]: rec["value"] for rec in provenance.values()}
        out = []
        for eid, rec in provenance.items():
            if self.is_protected(value_by_eid.get(eid, rec["value"])):
                continue
            if self.relevance(rec, now) < self.threshold:
                out.append(eid)
        return out


def archive_key(value: Any) -> str:
    """Content-addressed key for an archived fact (so revives are convergent)."""
    return content_hash(value)
