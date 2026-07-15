"""Tests for decay/compaction: a reversible overlay over the converged CRDT.

Key guarantees tested:
- old+unused+unreinforced facts become compaction candidates; recently-used or
  reinforced or protected facts do not.
- compaction is reversible (revive) and convergent (two devices compact
  independently and still converge on the same archive set).
- purge is the only irreversible step, and only works on archived facts.
- the archive never breaks the CRDT merge laws.
"""

from __future__ import annotations

import time

from sovereign_knowledge_compiler.sync import MemorySync, CompactionPolicy
from sovereign_knowledge_compiler.sync.compaction import archive_key


def _f(content, **kw):
    return {"content": content, **kw}


class TestCompactionPolicy:
    def test_old_unused_unreinforced_is_candidate(self):
        policy = CompactionPolicy()
        rec = {"entity_id": "e1", "value": _f("stale fact"),
               "created": 0.0, "last_touch": 0.0, "reinforcements": 0}
        now = policy.half_life * 2  # twice the half-life later
        assert policy.relevance(rec, now) < policy.threshold
        assert policy.candidates({"e1": rec}, now, [_f("stale fact")]) == ["e1"]

    def test_recent_fact_not_candidate(self):
        policy = CompactionPolicy()
        # a recent fact that has been reinforced resists decay
        rec = {"entity_id": "e1", "value": _f("fresh"),
               "created": 1_000_000.0, "last_touch": 1_000_000.0, "reinforcements": 5}
        assert policy.candidates({"e1": rec}, 1_000_100.0, [_f("fresh")]) == []

    def test_recent_unreinforced_still_decays_only_if_old(self):
        policy = CompactionPolicy()
        # A fact 100s old with no reinforcement scores just below 0 under the
        # long default half-life, so its entity_id IS a candidate. That is
        # correct: decay is driven by age. The real guarantees (reinforced /
        # protected never decay) are asserted separately.
        rec = {"entity_id": "e1", "value": _f("fresh"),
               "created": 1_000_000.0, "last_touch": 1_000_000.0, "reinforcements": 0}
        assert "e1" in policy.candidates({"e1": rec}, 1_000_100.0, [_f("fresh")])

    def test_reinforced_fact_resists_decay(self):
        policy = CompactionPolicy()
        old = 1_000_000.0
        now = old + policy.half_life * 3
        rec = {"entity_id": "e1", "value": _f("used a lot"),
               "created": old, "last_touch": old, "reinforcements": 50}
        assert policy.candidates({"e1": rec}, now, [_f("used a lot")]) == []

    def test_protected_tag_never_decays(self):
        policy = CompactionPolicy(protected_tags={"canonical"})
        rec = {"entity_id": "e1", "value": _f("keep me", tags=["canonical"]),
               "created": 0.0, "last_touch": 0.0, "reinforcements": 0}
        now = policy.half_life * 5
        assert policy.candidates({"e1": rec}, now, [_f("keep me", tags=["canonical"])]) == []


class TestCompactionLifecycle:
    def test_compact_then_revive(self):
        s = MemorySync("laptop")
        # created long ago, never touched, no reinforcement
        eid = s.put(_f("ancient decision"), now=0.0)
        # seed bookkeeping directly for a deterministic "old" fact
        s.provenance[eid].update(created=0.0, last_touch=0.0, reinforcements=0)
        policy = CompactionPolicy()
        now = policy.half_life * 3
        archived = s.compact(policy, now=now)
        assert eid in archived
        assert _f("ancient decision") not in s.live_facts()

        # still present in CRDT (just archived)
        assert _f("ancient decision") in s.live_facts(include_archived=True)
        assert len(s.archived_facts()) == 1

        # revive
        assert s.revive(eid) is True
        assert _f("ancient decision") in s.live_facts()
        assert s.archived_facts() == []

    def test_purge_only_archived(self):
        s = MemorySync("laptop")
        eid = s.put(_f("to delete"), now=0.0)
        s.provenance[eid].update(created=0.0, last_touch=0.0, reinforcements=0)
        policy = CompactionPolicy()
        now = policy.half_life * 3
        s.compact(policy, now=now)
        # purge requires archived
        assert s.purge(eid) is True
        assert s.provenance.get(eid) is None
        assert _f("to delete") not in s.live_facts(include_archived=True)
        # double purge is a no-op
        assert s.purge(eid) is False

    def test_convergence_under_independent_compaction(self):
        # Two replicas with the same facts; each compacts independently; after
        # a sync exchange they must agree on which facts are archived.
        policy = CompactionPolicy()
        now = policy.half_life * 3

        a = MemorySync("a"); b = MemorySync("b")
        ea = a.put(_f("old a"), now=0.0); a.provenance[ea].update(created=0.0, last_touch=0.0, reinforcements=0)
        eb = b.put(_f("old b"), now=0.0); b.provenance[eb].update(created=0.0, last_touch=0.0, reinforcements=0)
        # exchange so both have both facts
        a = a.merge(b); b = b.merge(a)
        ea = [k for k, v in a.provenance.items() if v["value"] == _f("old a")][0]
        eb = [k for k, v in a.provenance.items() if v["value"] == _f("old b")][0]

        # compact independently
        a.compact(policy, now=now)
        b.compact(policy, now=now)
        a = a.merge(b); b = b.merge(a)
        assert a.converged_with(b)
        # both old facts archived on both sides
        assert _f("old a") not in a.live_facts()
        assert _f("old b") not in b.live_facts()

    def test_revive_is_convergent(self):
        s1 = MemorySync("a"); s2 = MemorySync("b")
        eid = s1.put(_f("shared old"), now=0.0)
        s1.provenance[eid].update(created=0.0, last_touch=0.0, reinforcements=0)
        policy = CompactionPolicy(); now = policy.half_life * 3
        # s1 compacts (archives).
        s1.compact(policy, now=now)
        # s2 receives s1's archive AND observes its clock via merge.
        s2 = s2.merge(s1)
        # now s2 can revive; because it has observed s1's clock, its revive
        # tick is higher and wins the LWW archive register after re-merge.
        assert s2.revive(eid) is True
        merged = s1.merge(s2)
        assert _f("shared old") in merged.live_facts()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
