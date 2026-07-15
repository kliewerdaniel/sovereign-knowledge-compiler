"""Tests for the local-LLM deep-synthesis pass.

These tests are fully offline: they use a deterministic mock client and never
touch a network or a real model, so CI stays hermetic. They assert the honesty
guarantees that matter:
- synthesis ADDS facts on top of deterministic extraction (never replaces)
- duplicate synthesised facts are de-duplicated (base wins)
- malformed / chatty model output yields no fabricated facts
- a None or unavailable client degrades gracefully to deterministic facts
- decisions and rationale are captured
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sovereign_knowledge_compiler.artifacts.types import Fact
from sovereign_knowledge_compiler.compiler.extractor import extract_facts
from sovereign_knowledge_compiler.compiler.synthesizer import (
    deep_synthesize, _extract_json_array, _merge_dedup, LocalLLMClient,
)
from sovereign_knowledge_compiler.compiler.frontend import compile_material


class MockClient:
    """Returns a fixed response; records prompts. No network, no availability
    probe (so deep_synthesize treats it as always available)."""

    def __init__(self, response: str):
        self.response = response
        self.prompts = []

    def complete(self, prompt, *, system="", max_tokens=2048):
        self.prompts.append(prompt)
        return self.response


class UnavailableClient(MockClient):
    def available(self):
        return False


MATERIAL = [{
    "type": "notes",
    "date": "2026-07-15",
    "content": "We evaluated Postgres and MongoDB. We chose Postgres for its strong consistency.",
}]


class TestJSONParsing:
    def test_plain_array(self):
        out = _extract_json_array('[{"content":"a"},{"content":"b"}]')
        assert len(out) == 2

    def test_fenced_json(self):
        text = "Sure!\n```json\n[{\"content\":\"a\"}]\n```\nDone."
        out = _extract_json_array(text)
        assert out == [{"content": "a"}]

    def test_embedded_array_with_chatter(self):
        text = 'Here you go: [{"content":"x","tags":["db"]}] hope that helps'
        out = _extract_json_array(text)
        assert out[0]["content"] == "x"

    def test_malformed_yields_empty(self):
        assert _extract_json_array("not json at all") == []
        assert _extract_json_array("") == []
        assert _extract_json_array("[oops broken") == []


class TestDeepSynthesize:
    def test_synthesis_adds_facts(self):
        base = extract_facts(MATERIAL)
        resp = json.dumps([
            {"content": "The team standardized on Postgres as the primary datastore.",
             "tags": ["database", "architecture"],
             "is_decision": True,
             "rationale": "strong consistency guarantees"},
        ])
        client = MockClient(resp)
        out = deep_synthesize(MATERIAL, base, client=client)
        assert len(out) > len(base)
        synth = [f for f in out if f.source and f.source.endswith(":synth")]
        assert synth, "expected at least one synthesised fact"
        assert synth[0].is_decision is True
        assert "rationale" in synth[0].tags
        assert "rationale:" in synth[0].content

    def test_none_client_is_passthrough(self):
        base = extract_facts(MATERIAL)
        out = deep_synthesize(MATERIAL, base, client=None)
        assert out == base

    def test_unavailable_client_degrades(self):
        base = extract_facts(MATERIAL)
        client = UnavailableClient(json.dumps([{"content": "should not appear"}]))
        out = deep_synthesize(MATERIAL, base, client=client)
        assert out == base
        assert client.prompts == []  # never even prompted

    def test_malformed_response_no_fabrication(self):
        base = extract_facts(MATERIAL)
        client = MockClient("I cannot help with that.")
        out = deep_synthesize(MATERIAL, base, client=client)
        assert out == base  # nothing added

    def test_dedup_base_wins(self):
        base = [Fact(content="Postgres is the datastore.", tags=["db"])]
        synth = [Fact(content="  postgres IS the datastore.  ", tags=["db"], source="x:synth")]
        merged = _merge_dedup(base, synth)
        assert len(merged) == 1
        assert merged[0].source is None  # the base (verbatim) fact wins

    def test_failing_call_does_not_abort(self):
        class Boom(MockClient):
            def complete(self, prompt, *, system="", max_tokens=2048):
                raise RuntimeError("model exploded")
        base = extract_facts(MATERIAL)
        out = deep_synthesize(MATERIAL, base, client=Boom(""))
        assert out == base  # error swallowed, base preserved


class TestFrontendDeepFlag:
    def test_compile_deep_records_manifest(self):
        resp = json.dumps([
            {"content": "Adopt Postgres for strong consistency.", "tags": ["db"],
             "is_decision": True, "rationale": "consistency"},
        ])
        client = MockClient(resp)
        with tempfile.TemporaryDirectory() as d:
            manifest = compile_material(MATERIAL, d, version="v1", deep=True, client=client)
            assert manifest["manifest"]["deep_synthesis"] is True
            assert manifest["synthesized_facts_added"] >= 1
            # bundle is written and readable
            assert Path(manifest["path"]).exists()

    def test_compile_without_deep_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = compile_material(MATERIAL, d, version="v1")
            assert manifest["manifest"]["deep_synthesis"] is False
            assert manifest["synthesized_facts_added"] == 0


class TestLocalClientSafety:
    def test_available_never_raises_when_offline(self):
        # nothing is listening on this port; available() must return False, not raise
        client = LocalLLMClient(endpoint="http://localhost:1", api="ollama")
        assert client.available() is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
