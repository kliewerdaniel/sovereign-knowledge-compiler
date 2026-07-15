"""Example: compile raw transcripts into compiled memory artifacts, then query.

Run from the repo root:
    python examples/basic_compile.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sovereign_knowledge_compiler.compiler.frontend import compile_material
from sovereign_knowledge_compiler.runtime.api import MemoryRuntime
from sovereign_knowledge_compiler.privacy.guard import guard as privacy_guard


def main() -> None:
    raw_material = [
        {
            "type": "transcript",
            "date": "2024-01-15",
            "content": (
                "We decided to use PostgreSQL for the user database. "
                "Alice argued for MongoDB but the team agreed PostgreSQL "
                "is better for relational data. The API will use FastAPI. "
                "Deadline is March 1st."
            ),
        },
        {
            "type": "note",
            "date": "2024-01-16",
            "content": (
                "Remember: PostgreSQL connection pooling is important. "
                "Use SQLAlchemy with async support."
            ),
        },
        {
            "type": "decision",
            "date": "2024-01-20",
            "content": (
                "Decision: Use FastAPI for the REST API. "
                "Rationale: better async support than Flask. Owner: Bob."
            ),
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "memory"

        # Privacy Guard runs before compilation (the sovereign boundary).
        print("Running Privacy Guard + compiling raw material...")
        clean = privacy_guard(raw_material)
        manifest = compile_material(clean, output_dir, version="v1")
        print(f"Compiled {manifest['fact_count']} facts, "
              f"{manifest['decision_count']} decisions")

        # The runtime does cheap lookups -- no reasoning, no retrieval.
        print("\nQuerying compiled memory...")
        runtime = MemoryRuntime(str(output_dir))

        results = runtime.query("database")
        print(f"\nQuery 'database': {len(results)} results")
        for r in results:
            print(f"  - [{r.get('source', 'fact')}] {r['content'][:80]}")

        results = runtime.query_by_tag("technology")
        print(f"\nTag 'technology': {len(results)} results")
        for r in results:
            print(f"  - {r['content'][:80]}")

        results = runtime.query_date_range("2024-01-15", "2024-01-17")
        print(f"\nDate range 2024-01-15..17: {len(results)} results")
        for r in results:
            print(f"  - [{r['date']}] {r['content'][:80]}")


if __name__ == "__main__":
    main()
