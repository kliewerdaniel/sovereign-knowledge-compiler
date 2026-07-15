"""Tests for the Sovereign Knowledge Compiler core pipeline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from sovereign_knowledge_compiler.compiler.extractor import extract_facts
from sovereign_knowledge_compiler.compiler.consolidator import consolidate
from sovereign_knowledge_compiler.compiler.indexer import build_index
from sovereign_knowledge_compiler.compiler.frontend import compile_material
from sovereign_knowledge_compiler.artifacts.types import Fact, Decision
from sovereign_knowledge_compiler.privacy.guard import scan, redact, guard


class TestExtractor:
    def test_extract_facts_from_transcript(self):
        material = [
            {
                "type": "transcript",
                "date": "2024-01-15",
                "content": (
                    "We decided to use PostgreSQL for the user database. "
                    "Alice argued for MongoDB but the team agreed PostgreSQL "
                    "is better for relational data."
                ),
            }
        ]
        facts = extract_facts(material)
        assert len(facts) >= 2
        assert all(isinstance(f, Fact) for f in facts)
        postgres_facts = [f for f in facts if "PostgreSQL" in f.content]
        assert len(postgres_facts) >= 1
        # at least one decision was flagged
        assert any(f.is_decision for f in facts)

    def test_extract_decisions(self):
        material = [
            {
                "type": "decision",
                "date": "2024-01-20",
                "content": "Decision: Use FastAPI. Rationale: better async support.",
            }
        ]
        facts = extract_facts(material)
        decisions = [f for f in facts if f.is_decision]
        assert len(decisions) >= 1
        assert "FastAPI" in decisions[0].content

    def test_extract_empty_material(self):
        assert extract_facts([]) == []


class TestConsolidator:
    def test_deduplicate_identical_facts(self):
        facts = [
            Fact(content="Use PostgreSQL", tags=["database"], date="2024-01-15"),
            Fact(content="Use PostgreSQL", tags=["database"], date="2024-01-16"),
        ]
        consolidated = consolidate(facts)
        assert len(consolidated) == 1

    def test_merge_complementary_facts(self):
        facts = [
            Fact(content="Use PostgreSQL", tags=["database"], date="2024-01-15"),
            Fact(content="PostgreSQL is good for relational data", tags=["database"],
                 date="2024-01-16"),
        ]
        consolidated = consolidate(facts)
        assert len(consolidated) >= 1
        merged = consolidated[0]
        assert "database" in merged.tags

    def test_empty_input(self):
        assert consolidate([]) == []


class TestIndexer:
    def test_build_inverted_index(self):
        facts = [
            Fact(content="Use PostgreSQL", tags=["database"], date="2024-01-15"),
            Fact(content="Use FastAPI", tags=["api"], date="2024-01-16"),
        ]
        index = build_index(facts)
        assert "database" in index["tags"]
        assert "api" in index["tags"]
        assert len(index["tags"]["database"]) == 1

    def test_empty_input(self):
        index = build_index([])
        assert index["tags"] == {}
        assert index["facts"] == []


class TestCompileMaterial:
    def test_full_pipeline(self):
        material = [
            {
                "type": "transcript",
                "date": "2024-01-15",
                "content": "We decided to use PostgreSQL for the database.",
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "memory"
            manifest = compile_material(material, output_dir, version="v1")
            assert manifest["fact_count"] >= 1
            assert output_dir.exists()
            assert (output_dir / "v1").exists()
            facts_file = output_dir / "v1" / "facts.jsonl"
            assert facts_file.exists()
            data = [json.loads(l) for l in facts_file.read_text().splitlines() if l.strip()]
            assert len(data) >= 1

    def test_versioned_output(self):
        material = [{"type": "note", "date": "2024-01-15", "content": "First note"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "memory"
            compile_material(material, output_dir, "v1")
            compile_material(material, output_dir, "v2")
            assert (output_dir / "v1").exists()
            assert (output_dir / "v2").exists()


class TestPrivacyGuard:
    def test_scan_detects_email(self):
        material = [{"type": "note", "content": "Contact me at a@b.com please"}]
        findings = scan(material)
        assert "0" in findings
        assert any("email" in h for h in findings["0"])

    def test_redact_removes_pii(self):
        text = "email john@x.com call 555-123-4567 key=SECRETABCD1234"
        out = redact(text)
        assert "john@x.com" not in out
        assert "555-123-4567" not in out

    def test_guard_returns_redacted_copy(self):
        material = [{"type": "note", "content": "reach me at a@b.com"}]
        out = guard(material)
        assert "a@b.com" not in out[0]["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
