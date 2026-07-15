"""Compiler Frontend: orchestrates the compile pipeline.

    raw material -> extract -> consolidate -> index -> write (versioned bundle)

This is the "pay once" step. It runs locally, once per batch of new material,
and produces static artifacts the runtime serves. No reasoning happens at
query time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..artifacts.types import ArtifactBundle, Fact
from ..artifacts.writer import write_bundle
from .extractor import extract_facts
from .consolidator import consolidate
from .indexer import build_index
from .synthesizer import deep_synthesize


def compile_material(
    material: List[Dict],
    output_dir,
    version: str = "v1",
    source_label: Optional[str] = None,
    deep: bool = False,
    client=None,
) -> Dict:
    """Compile raw material into a versioned artifact bundle.

    Returns a manifest dict with at least ``fact_count`` and
    ``decision_count``. Writes the bundle under ``output_dir/<version>/``.

    When ``deep=True`` and a local-LLM ``client`` is supplied (and reachable),
    a deep-synthesis pass augments the deterministic facts before consolidation.
    If the model is unavailable the deterministic facts are used unchanged.
    """
    raw_facts: List[Fact] = extract_facts(material)
    synth_added = 0
    if deep:
        before = len(raw_facts)
        raw_facts = deep_synthesize(material, raw_facts, client=client)
        synth_added = len(raw_facts) - before
    consolidated: List[Fact] = consolidate(raw_facts)
    index = build_index(consolidated)

    fact_count = len(consolidated)
    decision_count = sum(1 for f in consolidated if f.is_decision)

    bundle = ArtifactBundle(
        version=version,
        facts=consolidated,
        index=index,
        manifest={
            "version": version,
            "fact_count": fact_count,
            "decision_count": decision_count,
            "source_label": source_label or "manual",
            "source_material_count": len(material or []),
            "deep_synthesis": bool(deep),
            "synthesized_facts_added": synth_added,
        },
    )
    written = write_bundle(str(output_dir), bundle)
    return {
        "version": version,
        "fact_count": fact_count,
        "decision_count": decision_count,
        "synthesized_facts_added": synth_added,
        "path": written,
        "manifest": bundle.manifest,
    }
