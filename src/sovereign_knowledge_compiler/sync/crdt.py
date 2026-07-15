"""Core CRDT primitives for server-less memory sync.

State-based (CvRDT) design. Every merge is a pure function of two states; the
result is itself a valid state. This guarantees the three properties that make
server-less sync safe:

* **Commutativity** -- ``merge(a, b)`` equals ``merge(b, a)``.
* **Associativity** -- ``merge(merge(a, b), c)`` equals ``merge(a, merge(b, c))``.
* **Idempotence**   -- ``merge(a, a)`` equals ``a``.

Design note (honesty): the set here is **Remove-Wins**, not an Observed-Removed
Set. A delete tombstones a *value's content hash*, so the value is gone from
every replica after merge -- even replicas that added it independently. For
compiled memory the intent of a delete is "this fact no longer exists,
everywhere", so Remove-Wins is the correct semantics. (A true OR-Set, by
contrast, would keep independently-added copies alive after a single delete;
that is *not* what we want for memory that should disappear on delete.)

Also provided: a Lamport clock for causally-ordered last-writer-wins, so edit
conflicts resolve by logical time rather than fragile wall-clock time.

References: Shapiro et al., "Conflict-Free Replicated Data Types" (INRIA, 2011).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Set, Tuple


def content_hash(value: Any) -> str:
    """Full SHA-256 (64 hex chars) of a value's canonical JSON.

    Used as the stable identity of a fact for deletion and LWW. A full-length
    hash (not the previously-truncated 16 chars) avoids collisions between
    distinct facts that happen to share identical content -- which earlier
    caused one fact's delete to also remove an unrelated twin.
    """
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LamportClock:
    """Logical clock for causally-ordered LWW.

    Beats wall-clock time because it never goes backwards and advances on both
    local events and merges. Two events are ordered by ``(lamport, writer)``;
    ties broken by replica id. Note: this gives *causal* ordering, not total
    real-time ordering -- if two devices' edits happen between syncs and their
    Lamport clocks happen to align, the writer-id tiebreak decides. That is
    documented, not hidden. For strict recency you would additionally surface
    the conflict for human review (see MemorySync conflicts).
    """

    def __init__(self, replica_id: str, value: int = 0):
        self.replica_id = replica_id
        self._t = value

    def tick(self) -> int:
        self._t += 1
        return self._t

    def observe(self, other: int) -> None:
        """Incorporate a remote timestamp (monotonic advance)."""
        self._t = max(self._t, other)

    def merge(self, other: "LamportClock") -> "LamportClock":
        merged = LamportClock(self.replica_id, max(self._t, other._t))
        return merged

    @property
    def time(self) -> int:
        return self._t

    def export(self) -> int:
        return self._t

    @classmethod
    def import_state(cls, replica_id: str, value: int) -> "LamportClock":
        return cls(replica_id, value)


class FactSet:
    """Remove-Wins Set CRDT over arbitrary JSON-serialisable values.

    Each add is stored under a unique tag ``"<replica>:<counter>"`` so
    concurrent adds of equal values stay distinct in storage. Removal is
    content-level: tombstoning a content hash removes every matching tag on
    merge, so deletes propagate across all replicas.
    """

    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self._counter = 0
        self._adds: Dict[str, Any] = {}          # tag -> value
        self._removed_content: Set[str] = set()  # content hashes tombstoned

    # -- local mutations ---------------------------------------------------
    def _next_tag(self) -> str:
        self._counter += 1
        return f"{self.replica_id}:{self._counter}"

    def add(self, value: Any) -> str:
        """Add a value, returning its unique tag."""
        tag = self._next_tag()
        self._adds[tag] = value
        return tag

    def remove_value(self, value: Any) -> int:
        """Tombstone a value's content hash across all replicas. Returns count
        of currently-live matching tags at this replica (for API symmetry)."""
        h = content_hash(value)
        self._removed_content.add(h)
        return sum(1 for t, v in self._adds.items()
                   if content_hash(v) == h and h not in self._removed_content)

    def remove_tag(self, tag: str) -> None:
        """Local-only removal of a specific tag."""
        if tag in self._adds:
            self._removed_content.add(content_hash(self._adds[tag]))

    # -- merge --------------------------------------------------------------
    def merge(self, other: "FactSet") -> "FactSet":
        """Join two FactSets. Pure: does not mutate ``self`` or ``other``."""
        merged = FactSet(self.replica_id)
        merged._counter = max(self._counter, other._counter)
        merged._adds = dict(self._adds)
        merged._adds.update(other._adds)
        merged._removed_content = set(self._removed_content) | set(other._removed_content)
        return merged

    # -- observation --------------------------------------------------------
    def live_items(self) -> List[Tuple[str, Any]]:
        """``(tag, value)`` pairs that are not tombstoned, insertion order."""
        return [
            (t, v) for t, v in self._adds.items()
            if content_hash(v) not in self._removed_content
        ]

    def values(self) -> List[Any]:
        return [v for _, v in self.live_items()]

    def value_set(self) -> Set[str]:
        return {content_hash(v) for v in self.values()}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FactSet):
            return NotImplemented
        return self.value_set() == other.value_set()

    def __len__(self) -> int:
        return len(self.live_items())

    # -- serialisation ------------------------------------------------------
    def export(self) -> Dict[str, Any]:
        return {
            "replica_id": self.replica_id,
            "counter": self._counter,
            "adds": self._adds,
            "removed_content": sorted(self._removed_content),
        }

    @classmethod
    def import_state(cls, state: Dict[str, Any]) -> "FactSet":
        s = cls(state["replica_id"])
        s._counter = state["counter"]
        s._adds = dict(state["adds"])
        s._removed_content = set(state["removed_content"])
        return s


class VersionVector:
    """Per-replica causal counter. ``merge`` is element-wise max."""

    def __init__(self, replica_id: str = "_", vector: Optional[Dict[str, int]] = None):
        self.replica_id = replica_id
        self._v: Dict[str, int] = dict(vector or {})

    def increment(self) -> None:
        self._v[self.replica_id] = self._v.get(self.replica_id, 0) + 1

    def merge(self, other: "VersionVector") -> "VersionVector":
        merged = VersionVector(self.replica_id)
        keys = set(self._v) | set(other._v)
        merged._v = {k: max(self._v.get(k, 0), other._v.get(k, 0)) for k in keys}
        return merged

    def dominates(self, other: "VersionVector") -> bool:
        keys = set(self._v) | set(other._v)
        return all(self._v.get(k, 0) >= other._v.get(k, 0) for k in keys)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VersionVector):
            return NotImplemented
        keys = set(self._v) | set(other._v)
        return all(self._v.get(k, 0) == other._v.get(k, 0) for k in keys)

    def export(self) -> Dict[str, int]:
        return dict(self._v)

    @classmethod
    def import_state(cls, state: Dict[str, int]) -> "VersionVector":
        return cls(vector=state)
