"""Apply the FactSet CRDT to compiled memory bundles for device sync.

A ``MemorySync`` wraps a compiled memory store (the facts of a version) and
tracks, per fact, a *provenance* record (which replica last wrote it, at what
logical time, and under which *entity key*). Two kinds of convergence happen:

* **Add/delete** of distinct facts: pure Remove-Wins Set, always converges.
* **Concurrent edits of the same logical entity** (same ``entity_id``): resolved
  last-writer-wins by ``(lamport, writer)``, with the winning record recorded
  AND the losing record kept in a ``conflicts`` ledger so the resolution is
  *inspectable and overridable* -- never silently dropped. ``entity_id`` groups
  logically-related edits so concurrent edits of "the PostgreSQL decision"
  converge on one value instead of duplicating it.

Conflict ordering uses a Lamport clock (causal, never regresses) rather than
wall-clock time, so a device with a lagging system clock does not silently lose
a later edit. Where Lamport clocks tie (edits made between syncs), the
replica-id tiebreak decides and the conflict is recorded for human review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .crdt import FactSet, VersionVector, LamportClock, content_hash


class MemorySync:
    """Server-less sync over a set of compiled facts."""

    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.facts = FactSet(replica_id)
        # entity_id -> provenance record {writer, lamport, value, entity_id}
        self.provenance: Dict[str, Dict[str, Any]] = {}
        self.vv = VersionVector(replica_id)
        self.clock = LamportClock(replica_id)
        # entity_id -> list of losing records (for human review / override)
        self.conflicts: Dict[str, List[Dict[str, Any]]] = {}

    # -- local write --------------------------------------------------------
    def put(self, value: Any, lamport: Optional[int] = None,
            writer: Optional[str] = None, entity_id: Optional[str] = None) -> str:
        """Add (or replace) a fact locally.

        ``lamport`` is a logical timestamp (defaults to a fresh Lamport tick).
        ``entity_id`` groups logically-related edits so concurrent edits of the
        same entity resolve LWW instead of duplicating. Defaults to the fact's
        content hash (so different facts never collide).
        """
        writer = writer or self.replica_id
        if lamport is None:
            lamport = self.clock.tick()
        else:
            self.clock.observe(lamport)
        eid = entity_id or content_hash(value)

        prior = self.provenance.get(eid)
        if prior is not None:
            # replace: drop the old value from the set before adding the new
            old_value = prior.get("value")
            if old_value is not None and content_hash(old_value) != content_hash(value):
                self.facts.remove_value(old_value)

        self.facts.add(value)
        if prior is None or (lamport, writer) >= (prior["lamport"], prior["writer"]):
            self.provenance[eid] = {
                "writer": writer, "lamport": lamport,
                "value": value, "entity_id": eid,
            }
        self.vv.increment()
        return eid

    def delete(self, value: Any) -> int:
        removed = self.facts.remove_value(value)
        if removed:
            self.vv.increment()
        return removed

    # -- merge ---------------------------------------------------------------
    def merge(self, other: "MemorySync") -> "MemorySync":
        merged = MemorySync(self.replica_id)
        merged.facts = self.facts.merge(other.facts)
        merged.vv = self.vv.merge(other.vv)
        merged.clock = self.clock.merge(other.clock)

        # reconcile provenance: union, LWW per entity_id by (lamport, writer)
        prov = dict(self.provenance)
        conflicts: Dict[str, List[Dict[str, Any]]] = {}
        for local_eid, rec in list(self.provenance.items()) + list(other.provenance.items()):
            cur = prov.get(rec["entity_id"])
            if cur is None:
                prov[rec["entity_id"]] = rec
                continue
            # compare by causal ordering
            if (rec["lamport"], rec["writer"]) > (cur["lamport"], cur["writer"]):
                loser = cur
                prov[rec["entity_id"]] = rec
            elif (rec["lamport"], rec["writer"]) < (cur["lamport"], cur["writer"]):
                loser = rec
            else:
                # exact tie: keep deterministic winner, record both as conflict
                loser = rec if rec["writer"] > cur["writer"] else cur
                prov[rec["entity_id"]] = cur
            eid = rec["entity_id"]
            conflicts.setdefault(eid, [])
            if loser not in conflicts[eid]:
                conflicts[eid].append(loser)

        # For any entity_id whose LWW winner differs from what a replica also
        # carries, drop the losing value from the set so the live set and the
        # provenance record agree (true conflict resolution, not duplication).
        for eid, rec in prov.items():
            win_hash = content_hash(rec["value"])
            for other_rec in (self.provenance.get(eid), other.provenance.get(eid)):
                if other_rec is None:
                    continue
                lose_hash = content_hash(other_rec["value"])
                if lose_hash != win_hash:
                    merged.facts.remove_value(other_rec["value"])

        live = merged.facts.value_set()
        merged.provenance = {
            eid: r for eid, r in prov.items()
            if content_hash(r["value"]) in live
        }
        # keep only conflicts that are real losers relative to the winning record
        merged.conflicts = {}
        for eid, lst in conflicts.items():
            if eid not in merged.provenance:
                continue
            win_hash = content_hash(merged.provenance[eid]["value"])
            losers = [c for c in lst if content_hash(c["value"]) != win_hash]
            if losers:
                merged.conflicts[eid] = losers
        return merged

    # -- conflict review / override ----------------------------------------
    def pending_conflicts(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return entity_id -> list of losing records awaiting review."""
        return {eid: lst for eid, lst in self.conflicts.items() if lst}

    def resolve(self, entity_id: str, value: Any, writer: Optional[str] = None) -> None:
        """Human override: pin an entity's value, overriding LWW + any losers.

        Recorded in provenance with the override marker so it survives merges
        (its (lamport, writer) is set above any existing record).
        """
        writer = writer or f"override:{self.replica_id}"
        lamport = self.clock.tick()
        # bump above any competing record for this entity
        for rec in self.conflicts.get(entity_id, []) + [self.provenance.get(entity_id)]:
            if rec:
                lamport = max(lamport, rec["lamport"] + 1)
        self.clock.observe(lamport)
        old = self.provenance.get(entity_id, {}).get("value")
        if old is not None and content_hash(old) != content_hash(value):
            self.facts.remove_value(old)
        self.facts.add(value)
        self.provenance[entity_id] = {
            "writer": writer, "lamport": lamport,
            "value": value, "entity_id": entity_id, "overridden": True,
        }
        self.conflicts.pop(entity_id, None)

    # -- observation --------------------------------------------------------
    def live_facts(self) -> List[Any]:
        """Live facts ordered by provenance Lamport time (most recent last)."""
        prov_by_value = {
            content_hash(r["value"]): r for r in self.provenance.values()
        }
        return sorted(
            self.facts.values(),
            key=lambda v: prov_by_value.get(content_hash(v), {}).get("lamport", 0),
        )

    def converged_with(self, other: "MemorySync") -> bool:
        return (
            self.facts.value_set() == other.facts.value_set()
            and self.vv == other.vv
        )

    # -- serialisation ------------------------------------------------------
    def export(self) -> Dict[str, Any]:
        return {
            "replica_id": self.replica_id,
            "facts": self.facts.export(),
            "provenance": self.provenance,
            "vv": self.vv.export(),
            "clock": self.clock.export(),
            "conflicts": self.conflicts,
        }

    @classmethod
    def import_state(cls, state: Dict[str, Any]) -> "MemorySync":
        s = cls(state["replica_id"])
        s.facts = FactSet.import_state(state["facts"])
        s.provenance = state["provenance"]
        s.vv = VersionVector.import_state(state["vv"])
        s.clock = LamportClock.import_state(state["replica_id"], state.get("clock", 0))
        s.conflicts = state.get("conflicts", {})
        return s

    def save(self, path: str) -> None:
        Path(path).write_text(
            json.dumps(self.export(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str) -> "MemorySync":
        return cls.import_state(
            json.loads(Path(path).read_text(encoding="utf-8"))
        )


# -- convenience wrappers ------------------------------------------------------
def sync_to_file(sync: MemorySync, path: str) -> None:
    """Persist a replica's sync state to disk (its local copy of memory)."""
    sync.save(path)


def sync_from_file(path: str, replica_id: str) -> MemorySync:
    """Load a replica back from disk, re-tagging under ``replica_id``."""
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    state["replica_id"] = replica_id
    return MemorySync.import_state(state)
