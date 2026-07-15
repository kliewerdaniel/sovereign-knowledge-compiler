"""Apply the OR-Set CRDT to compiled memory bundles for device sync.

A ``MemorySync`` wraps a compiled memory store (the facts of a version) and
tracks, per fact, a *provenance* record (which replica last wrote it, at what
timestamp, and under which *entity key*). Two kinds of convergence happen:

* **Add/delete** of distinct facts: pure OR-Set, always converges.
* **Concurrent edits of the same logical entity** (same ``entity_id``): resolved
  last-writer-wins by ``(ts, writer)``, with the winning record recorded so the
  conflict is inspectable, never silently dropped. ``entity_id`` lets two
  devices edit "the PostgreSQL decision" and converge on one value instead of
  duplicating it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .crdt import ORSet, VersionVector, content_hash


def _now() -> float:
    return time.time()


class MemorySync:
    """Server-less sync over a set of compiled facts."""

    def __init__(self, replica_id: str):
        self.replica_id = replica_id
        self.facts = ORSet(replica_id)
        # entity_id -> provenance record {writer, ts, value, entity_id}
        self.provenance: Dict[str, Dict[str, Any]] = {}
        self.vv = VersionVector(replica_id)

    # -- local write --------------------------------------------------------
    def put(self, value: Any, ts: Optional[float] = None, writer: Optional[str] = None,
            entity_id: Optional[str] = None) -> str:
        """Add (or replace) a fact locally.

        ``entity_id`` groups logically-related edits so concurrent edits of the
        same entity resolve LWW instead of duplicating. Defaults to the fact's
        content hash (so different facts never collide).

        Re-putting an entity_id that already exists *replaces* its prior value
        in the OR-Set (content-level remove of the old value, add of the new),
        so the live fact set and the provenance record never disagree.
        """
        ts = ts if ts is not None else _now()
        writer = writer or self.replica_id
        eid = entity_id or content_hash(value)

        prior = self.provenance.get(eid)
        if prior is not None:
            # replace: drop the old value from the OR-Set before adding the new
            old_value = prior.get("value")
            if old_value is not None and content_hash(old_value) != content_hash(value):
                self.facts.remove_value(old_value)

        self.facts.add(value)
        if prior is None or (ts, writer) >= (prior["ts"], prior["writer"]):
            self.provenance[eid] = {
                "writer": writer, "ts": ts, "value": value, "entity_id": eid,
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

        # reconcile provenance: union, LWW per entity_id
        prov = dict(self.provenance)
        for eid, rec in other.provenance.items():
            cur = prov.get(eid)
            if cur is None or (rec["ts"], rec["writer"]) > (cur["ts"], cur["writer"]):
                prov[eid] = rec
        # For any entity_id whose LWW winner differs from what a replica also
        # carries, drop the losing value from the OR-Set so the live set and the
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
        # keep only provenance whose value survived the OR-Set merge
        merged.provenance = {
            eid: r for eid, r in prov.items()
            if content_hash(r["value"]) in live
        }
        return merged

    # -- observation --------------------------------------------------------
    def live_facts(self) -> List[Any]:
        """Live facts ordered by provenance timestamp (most recent last)."""
        prov_by_value = {
            content_hash(r["value"]): r for r in self.provenance.values()
        }
        return sorted(
            self.facts.values(),
            key=lambda v: prov_by_value.get(content_hash(v), {}).get("ts", 0.0),
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
        }

    @classmethod
    def import_state(cls, state: Dict[str, Any]) -> "MemorySync":
        s = cls(state["replica_id"])
        s.facts = ORSet.import_state(state["facts"])
        s.provenance = state["provenance"]
        s.vv = VersionVector.import_state(state["vv"])
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
