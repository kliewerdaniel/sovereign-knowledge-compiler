"""Sovereign Knowledge Compiler (SKC).

Compile-time memory for local-first AI agents.

Raw material (documents, transcripts, decisions, code) goes in once. Expensive
reasoning happens once, at compile time, producing a layered set of static,
inspectable, versioned artifacts. The runtime does cheap lookups against those
artifacts -- no live retrieval, no per-query re-reasoning, no cloud.

This package compounds on ``knowledge-compiler-sdk``: it reuses the SDK's
immutable ``ArtifactStore`` for the versioned bundle writes, so every compiled
memory batch is a content-hashed, provenance-tracked artifact on disk.

Architecture (mirrors the blog post "The Sovereign Knowledge Compiler"):
    INPUT  -> Privacy Guard -> Compiler Frontend -> Knowledge Compiler
            (extractor -> consolidator -> indexer) -> Runtime API (lookup only)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "compile_material",
    "Fact",
    "Decision",
    "MemoryRuntime",
    "ArtifactBundle",
]
