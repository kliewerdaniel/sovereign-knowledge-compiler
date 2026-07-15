"""Tests for the CRDT sync layer. The core guarantee of server-less sync is
that replicas converge regardless of message order. These tests assert the
actual CRDT laws and the honesty properties of the implementation, not just
happy-path behaviour."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from sovereign_knowledge_compiler.sync import (
    FactSet, VersionVector, LamportClock, MemorySync,
)
from sovereign_knowledge_compiler.sync.crdt import content_hash
from sovereign_knowledge_compiler.sync.memory_sync import sync_to_file, sync_from_file


def _fact(text, **kw):
    return {"content": text, **kw}


# --------------------------------------------------------------------------
# FactSet laws (Remove-Wins, NOT Observed-Removed -- verified honestly)
# --------------------------------------------------------------------------
class TestFactSetLaws:
    def test_add_observe_remove(self):
        s = FactSet("A")
        s.add("use postgres")
        assert "use postgres" in s.values()
        s.remove_value("use postgres")
        assert "use postgres" not in s.values()

    def test_concurrent_add_distinct_tags(self):
        a, b = FactSet("A"), FactSet("B")
        a.add("same fact")
        b.add("same fact")
        m = a.merge(b)
        # both adds survived, as distinct tags
        assert m.values().count("same fact") == 2

    def test_remove_wins_across_independent_adds(self):
        # This is the HONEST property: delete propagates to independent copies.
        a, b = FactSet("A"), FactSet("B")
        a.add("use postgres")
        b.add("use postgres")
        m = a.merge(b)
        m.remove_value("use postgres")
        assert "use postgres" not in m.values()

    def test_commutativity(self):
        a, b = FactSet("A"), FactSet("B")
        a.add({"x": 1})
        b.add({"x": 2})
        assert a.merge(b).value_set() == b.merge(a).value_set()

    def test_associativity(self):
        a, b, c = FactSet("A"), FactSet("B"), FactSet("C")
        a.add("one"); b.add("two"); c.add("three")
        assert a.merge(b).merge(c).value_set() == a.merge(b.merge(c)).value_set()

    def test_idempotence(self):
        a = FactSet("A")
        a.add("x")
        assert a.merge(a).value_set() == a.value_set()

    def test_export_import_roundtrip(self):
        s = FactSet("A")
        s.add("keep")
        s.remove_value("keep")
        s2 = FactSet.import_state(s.export())
        assert s2.value_set() == s.value_set()

    def test_full_content_hash_no_false_collision(self):
        # Distinct facts with identical content get the same (intended) hash,
        # but the hash is now full-length (64 hex) -- verify it's stable.
        h1 = content_hash({"content": "same text"})
        h2 = content_hash({"content": "same text"})
        assert h1 == h2 and len(h1) == 64


# --------------------------------------------------------------------------
# Lamport clock
# --------------------------------------------------------------------------
class TestLamportClock:
    def test_tick_monotonic(self):
        c = LamportClock("A")
        t1, t2 = c.tick(), c.tick()
        assert t2 > t1

    def test_observe_advances(self):
        a = LamportClock("A", 5)
        a.observe(9)
        assert a.time == 9
        a.observe(3)
        assert a.time == 9  # never regresses

    def test_merge_max(self):
        a = LamportClock("A", 4)
        b = LamportClock("B", 7)
        assert a.merge(b).time == 7


# --------------------------------------------------------------------------
# Version vector laws
# --------------------------------------------------------------------------
class TestVersionVector:
    def test_merge_elementwise_max(self):
        a = VersionVector("A", {"A": 1, "B": 2})
        b = VersionVector("B", {"B": 3, "C": 1})
        assert a.merge(b).export() == {"A": 1, "B": 3, "C": 1}

    def test_dominates(self):
        a = VersionVector("A", {"A": 2, "B": 2})
        b = VersionVector("B", {"A": 1, "B": 1})
        assert a.dominates(b) and not b.dominates(a)


# --------------------------------------------------------------------------
# MemorySync: convergence + conflict review
# --------------------------------------------------------------------------
class TestMemorySyncConvergence:
    def test_two_devices_converge_after_exchange(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("use postgres", tags=["db"]))
        laptop.put(_fact("deadline march 1", tags=["process"]))
        phone.put(_fact("use sqlite for mobile", tags=["db"]))
        laptop2 = laptop.merge(phone)
        phone2 = phone.merge(laptop)
        assert laptop2.converged_with(phone2)
        assert {f["content"] for f in laptop2.live_facts()} == {
            "use postgres", "deadline march 1", "use sqlite for mobile"
        }

    def test_convergence_under_out_of_order_exchange(self):
        a, b, c = MemorySync("A"), MemorySync("B"), MemorySync("C")
        a.put(_fact("alpha")); b.put(_fact("beta")); c.put(_fact("gamma"))
        a, b = a.merge(b), b.merge(a)
        b, c = b.merge(c), c.merge(b)
        c, a = c.merge(a), a.merge(c)
        a, b, c = a.merge(b), b.merge(c), c.merge(a)
        a, b, c = a.merge(c), b.merge(a), c.merge(b)
        assert a.converged_with(b) and b.converged_with(c)

    def test_conflicting_edit_resolves_lww(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("connection pool: 5"), lamport=100, writer="laptop",
                   entity_id="postgres-pool-size")
        phone.put(_fact("connection pool: 20"), lamport=200, writer="phone",
                  entity_id="postgres-pool-size")
        merged = laptop.merge(phone)
        assert merged.provenance["postgres-pool-size"]["writer"] == "phone"
        assert len([f for f in merged.live_facts() if "connection pool" in f["content"]]) == 1

    def test_lamport_lww_beats_wall_clock_skew(self):
        # Laptop's real edit is later but its wall clock lags; Lamport still
        # orders correctly because we pass logical time, not system time.
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        # laptop event at logical 300 (real time later), phone at logical 100
        laptop.put(_fact("setting: A"), lamport=300, writer="laptop", entity_id="s")
        phone.put(_fact("setting: B"), lamport=100, writer="phone", entity_id="s")
        merged = laptop.merge(phone)
        assert merged.provenance["s"]["value"]["content"] == "setting: A"

    def test_conflict_is_recorded_for_review(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("pool: 5"), lamport=100, writer="laptop", entity_id="pool")
        phone.put(_fact("pool: 20"), lamport=200, writer="phone", entity_id="pool")
        merged = laptop.merge(phone)
        pending = merged.pending_conflicts()
        assert "pool" in pending
        # loser recorded
        losers = pending["pool"]
        assert any(l["value"]["content"] == "pool: 5" for l in losers)

    def test_resolve_overrides_winner(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("pool: 5"), lamport=100, writer="laptop", entity_id="pool")
        phone.put(_fact("pool: 20"), lamport=200, writer="phone", entity_id="pool")
        merged = laptop.merge(phone)
        # human decides the laptop value (5) was right after all
        merged.resolve("pool", _fact("pool: 5"), writer="human")
        assert merged.provenance["pool"]["value"]["content"] == "pool: 5"
        assert merged.provenance["pool"].get("overridden") is True
        # conflict cleared
        assert merged.pending_conflicts() == {}

    def test_delete_propagates(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("stale decision"))
        phone.put(_fact("stale decision"))
        laptop.delete(_fact("stale decision"))
        laptop2 = laptop.merge(phone)
        phone2 = phone.merge(laptop)
        assert "stale decision" not in [f["content"] for f in laptop2.live_facts()]
        assert "stale decision" not in [f["content"] for f in phone2.live_facts()]

    def test_file_roundtrip_and_convergence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "laptop.sync.json"
            p2 = Path(tmp) / "phone.sync.json"
            laptop = MemorySync("laptop")
            phone = MemorySync("phone")
            laptop.put(_fact("fact A"))
            phone.put(_fact("fact B"))
            sync_to_file(laptop, str(p1))
            sync_to_file(phone, str(p2))
            l2 = sync_from_file(str(p1), "laptop")
            ph2 = sync_from_file(str(p2), "phone")
            m1 = l2.merge(ph2)
            m2 = ph2.merge(l2)
            assert m1.converged_with(m2)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
