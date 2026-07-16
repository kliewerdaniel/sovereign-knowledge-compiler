"""Tests for the synthesis -> decay feedback loop.

The compiler's deep-synthesis pass produces a usage signal: base facts the
model actually drew on ("cited") get reinforced in the MemorySync store, which
raises their resistance to decay. This closes the loop between the two newest
layers. These tests are fully offline (mock client, no network).
"""

from __future__ import annotations

import json

from sovereign_knowledge_compiler.artifacts.types import Fact
from sovereign_knowledge_compiler.compiler.synthesizer import (
    deep_synthesize, cited_base_facts, _reinforce_cited,
    concept_recurrence, reinforce_by_concept,
)
from sovereign_knowledge_compiler.sync import MemorySync, CompactionPolicy


class MockClient:
    def __init__(self, response):
        self.response = response
    def complete(self, prompt, *, system="", max_tokens=2048):
        return self.response


MATERIAL = [{
    "type": "notes", "date": "2026-07-15",
    "content": "We evaluated Postgres and MongoDB. We chose Postgres for strong consistency. The office coffee machine is broken.",
}]


class TestCitationDetection:
    def test_cited_facts_overlap(self):
        base = [
            Fact(content="We chose Postgres for strong consistency."),
            Fact(content="The office coffee machine is broken."),
        ]
        synth = [Fact(content="Postgres was selected as the datastore for its consistency guarantees.")]
        cited = cited_base_facts(base, synth)
        assert 0 in cited        # the postgres fact was drawn on
        assert 1 not in cited    # the coffee fact was not

    def test_no_synth_no_citations(self):
        base = [Fact(content="anything at all here")]
        assert cited_base_facts(base, []) == []


class TestReinforceLoop:
    def _store_with(self, contents):
        s = MemorySync("laptop")
        eids = [s.put({"content": c}, now=0.0) for c in contents]
        for eid in eids:
            s.provenance[eid].update(created=0.0, last_touch=0.0, reinforcements=0)
        return s, eids

    def test_reinforce_cited_raises_count(self):
        s, eids = self._store_with([
            "We chose Postgres for strong consistency.",
            "The office coffee machine is broken.",
        ])
        base = [Fact(content=c["value"]["content"]) for c in
                [{"value": {"content": "We chose Postgres for strong consistency."}},
                 {"value": {"content": "The office coffee machine is broken."}}]]
        synth = [Fact(content="Postgres was selected as the datastore for its consistency.")]
        n = _reinforce_cited(s, base, synth)
        assert n == 1
        assert s.provenance[eids[0]]["reinforcements"] == 1   # postgres reinforced
        assert s.provenance[eids[1]]["reinforcements"] == 0   # coffee untouched

    def test_cited_fact_survives_compaction(self):
        # Two equally-old facts; only the cited one should resist decay.
        s, eids = self._store_with([
            "We chose Postgres for strong consistency.",
            "The office coffee machine is broken.",
        ])
        base = [Fact(content="We chose Postgres for strong consistency."),
                Fact(content="The office coffee machine is broken.")]
        # crank reinforcement high enough to clearly cross the threshold
        for _ in range(20):
            _reinforce_cited(s, base, [Fact(content="Postgres selected as datastore for consistency.")])

        policy = CompactionPolicy()
        now = policy.half_life * 3
        # touch resets last_touch to time.time(); force old last_touch so age dominates
        for eid in eids:
            s.provenance[eid]["created"] = 0.0
            s.provenance[eid]["last_touch"] = 0.0
        cands = policy.candidates(s.provenance, now, s.facts.values())
        assert eids[1] in cands       # coffee (unreinforced) decays
        assert eids[0] not in cands   # postgres (reinforced 20x) survives

    def test_deep_synthesize_reinforces(self):
        s, eids = self._store_with(["We chose Postgres for strong consistency."])
        resp = json.dumps([
            {"content": "Postgres was adopted as the primary datastore for consistency.",
             "tags": ["db"], "is_decision": True, "rationale": "consistency"},
        ])
        base = [Fact(content="We chose Postgres for strong consistency.")]
        out = deep_synthesize(MATERIAL, base, client=MockClient(resp), reinforce_sync=s)
        # synthesised fact added AND the cited base fact reinforced in the store
        assert len(out) > len(base)
        assert s.provenance[eids[0]]["reinforcements"] >= 1

    def test_reinforce_never_raises_on_bad_store(self):
        # a store without provenance must not crash the loop
        class Dummy:
            pass
        base = [Fact(content="x y z content here")]
        synth = [Fact(content="x y z content here restated")]
        # _reinforce_cited uses getattr(sync,"provenance",{}) -> empty, returns 0
        assert _reinforce_cited(Dummy(), base, synth) == 0


class TestCrossPostConcept:
    def _corpus_store(self):
        """Build a store where 'ollama' recurs across 3 posts, 'coffee' in 1."""
        s = MemorySync("corpus")
        posts = [
            ("post-a", {"content": "Use Ollama locally.", "tags": ["ollama", "ai"]}),
            ("post-b", {"content": "Ollama serves the model.", "tags": ["ollama"]}),
            ("post-c", {"content": "We standardized on Ollama.", "tags": ["ollama", "ai"]}),
            ("post-d", {"content": "The coffee machine broke.", "tags": ["coffee"]}),
        ]
        eids = {}
        for slug, val in posts:
            val = dict(val, slug=slug)
            eid = s.put(val, now=0.0)
            s.provenance[eid].update(created=0.0, last_touch=0.0, reinforcements=0)
            eids[slug] = eid
        return s, eids

    def test_recurrence_counts_distinct_posts(self):
        s, eids = self._corpus_store()
        rec = concept_recurrence(s)
        # ollama posts see the other 2 ollama posts (+ ai overlap) -> >=2
        assert rec[eids["post-a"]] >= 2
        assert rec[eids["post-b"]] >= 2
        # coffee appears in exactly one post -> 0 recurrence
        assert rec[eids["post-d"]] == 0

    def test_reinforce_by_concept_rewards_recurring(self):
        s, eids = self._corpus_store()
        touched = reinforce_by_concept(s)
        assert touched >= 3  # the three ollama facts
        assert s.provenance[eids["post-a"]]["reinforcements"] >= 2
        assert s.provenance[eids["post-d"]]["reinforcements"] == 0

    def test_recurring_concept_survives_decay(self):
        s, eids = self._corpus_store()
        # scale reflects that real-corpus recurrence is large; here the tiny
        # fixture has recurrence 2-3, so scale up to cross the age penalty.
        reinforce_by_concept(s, scale=10)
        policy = CompactionPolicy()
        now = policy.half_life * 3
        for eid in eids.values():
            s.provenance[eid]["created"] = 0.0
            s.provenance[eid]["last_touch"] = 0.0
        cands = policy.candidates(s.provenance, now, s.facts.values())
        assert eids["post-d"] in cands       # one-off coffee fact decays
        assert eids["post-a"] not in cands   # recurring ollama concept survives


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
