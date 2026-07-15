"""Tests for the CRDT sync layer. The core guarantee of server-less sync is
that replicas converge regardless of message order. These tests assert the
actual CRDT laws, not just happy-path behaviour."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sovereign_knowledge_compiler.sync import ORSet, VersionVector, MemorySync
from sovereign_knowledge_compiler.sync.crdt import content_hash
from sovereign_knowledge_compiler.sync.memory_sync import sync_to_file, sync_from_file


def _fact(text, **kw):
    return {"content": text, **kw}


# --------------------------------------------------------------------------
# OR-Set laws
# --------------------------------------------------------------------------
class TestORSetLaws:
    def test_add_observe_remove(self):
        s = ORSet("A")
        s.add("use postgres")
        assert "use postgres" in s.values()
        s.remove_value("use postgres")
        assert "use postgres" not in s.values()

    def test_concurrent_add_different_tags(self):
        a, b = ORSet("A"), ORSet("B")
        a.add("same fact")
        b.add("same fact")
        m = a.merge(b)
        # both adds survived, as distinct tags, so the value appears twice
        assert m.values().count("same fact") == 2

    def test_commutativity(self):
        a, b = ORSet("A"), ORSet("B")
        a.add({"x": 1})
        b.add({"x": 2})
        assert a.merge(b).value_set() == b.merge(a).value_set()

    def test_associativity(self):
        a, b, c = ORSet("A"), ORSet("B"), ORSet("C")
        a.add("one")
        b.add("two")
        c.add("three")
        left = a.merge(b).merge(c)
        right = a.merge(b.merge(c))
        assert left.value_set() == right.value_set()

    def test_idempotence(self):
        a = ORSet("A")
        a.add("x")
        b = a.merge(a)
        assert b.value_set() == a.value_set()
        assert len(b) == len(a)

    def test_remove_is_observed_not_add(self):
        a, b = ORSet("A"), ORSet("B")
        t = a.add("volatile")
        m = a.merge(b)
        m.remove_tag(t)  # only A observed this add
        assert "volatile" not in m.values()

    def test_export_import_roundtrip(self):
        s = ORSet("A")
        s.add("keep")
        s.remove_value("keep")
        s2 = ORSet.import_state(s.export())
        assert s2.value_set() == s.value_set()


# --------------------------------------------------------------------------
# Version vector laws
# --------------------------------------------------------------------------
class TestVersionVector:
    def test_merge_is_elementwise_max(self):
        a = VersionVector("A", {"A": 1, "B": 2})
        b = VersionVector("B", {"B": 3, "C": 1})
        m = a.merge(b)
        assert m.export() == {"A": 1, "B": 3, "C": 1}

    def test_dominates(self):
        a = VersionVector("A", {"A": 2, "B": 2})
        b = VersionVector("B", {"A": 1, "B": 1})
        assert a.dominates(b)
        assert not b.dominates(a)

    def test_commutativity(self):
        a = VersionVector("A", {"A": 1})
        b = VersionVector("B", {"B": 1})
        assert a.merge(b).export() == b.merge(a).export()


# --------------------------------------------------------------------------
# MemorySync: multi-device convergence
# --------------------------------------------------------------------------
class TestMemorySyncConvergence:
    def test_two_devices_converge_after_exchange(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")

        laptop.put(_fact("use postgres", tags=["db"]))
        laptop.put(_fact("deadline march 1", tags=["process"]))
        phone.put(_fact("use sqlite for mobile", tags=["db"]))

        # exchange once (each merges the other)
        laptop2 = laptop.merge(phone)
        phone2 = phone.merge(laptop)

        assert laptop2.converged_with(phone2)
        assert {f["content"] for f in laptop2.live_facts()} == {
            "use postgres", "deadline march 1", "use sqlite for mobile"
        }

    def test_convergence_under_out_of_order_exchange(self):
        # Three devices, arbitrary message order, must still converge.
        a, b, c = MemorySync("A"), MemorySync("B"), MemorySync("C")
        a.put(_fact("alpha"))
        b.put(_fact("beta"))
        c.put(_fact("gamma"))

        # a<->b, then b<->c, then c<->a (a ring, not all-pairs at once)
        a, b = a.merge(b), b.merge(a)
        b, c = b.merge(c), c.merge(b)
        c, a = c.merge(a), a.merge(c)
        # final full exchange to guarantee convergence
        a, b, c = a.merge(b), b.merge(c), c.merge(a)
        a, b, c = a.merge(c), b.merge(a), c.merge(b)

        assert a.converged_with(b) and b.converged_with(c)

    def test_conflicting_edit_resolves_lww(self):
        # Two devices edit the SAME entity ("postgres pool size") with different
        # values; entity_id groups them so LWW keeps exactly one.
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("connection pool: 5"), ts=100.0, writer="laptop",
                   entity_id="postgres-pool-size")
        phone.put(_fact("connection pool: 20"), ts=200.0, writer="phone",
                  entity_id="postgres-pool-size")
        merged = laptop.merge(phone)
        # higher ts wins -> phone's value is the surviving record
        assert merged.provenance["postgres-pool-size"]["writer"] == "phone"
        # LWW keeps exactly one connection-pool fact
        assert len([f for f in merged.live_facts() if "connection pool" in f["content"]]) == 1

    def test_delete_propagates(self):
        laptop = MemorySync("laptop")
        phone = MemorySync("phone")
        laptop.put(_fact("stale decision"))
        phone.put(_fact("stale decision"))  # same content, both devices
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
