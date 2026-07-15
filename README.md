# Sovereign Knowledge Compiler (SKC)

**Compile-time memory for local-first AI agents.**

Raw material (documents, transcripts, decisions, code) goes in **once**.
Expensive reasoning happens **once**, at compile time, producing a layered set
of static, inspectable, versioned artifacts. The runtime does cheap lookups
against those artifacts — no live retrieval, no per-query re-reasoning, no
cloud.

This is the reference implementation called for by the blog post
[*The Sovereign Knowledge Compiler: Compile-Time Memory for Local-First AI
Agents*](https://www.danielkliewer.com/blog/2026-07-15-sovereign-memory-bank-deepening-local-first-cognitive-memory).
It compounds on [`knowledge-compiler-sdk`](https://github.com/kliewerdaniel/knowledge-compiler-sdk): every
compiled memory batch is persisted as an immutable, content-hashed artifact via
the SDK's `ArtifactStore`.

```
Traditional agent memory:   documents → embeddings → vector store → agent query
This:                       raw material → compile once → static artifacts → cheap lookup
```

## Why

Retrieval-augmented generation re-derives meaning on every query: embed,
search, stuff top-k into context, re-reason over raw material. The cost is paid
every call. A compiler makes a different bet: pay the reasoning cost once, at
ingestion, and serve cheap, static artifacts at runtime. Local-first is a
*consequence* of the compiler and its output living on the user's machine — not
the headline.

## Install

```bash
pip install -e .
# optional: also links the SDK for immutable artifact persistence
pip install -e ".[sdk]"
```

## Usage

### As a library

```python
from sovereign_knowledge_compiler.compiler.frontend import compile_material
from sovereign_knowledge_compiler.privacy.guard import guard
from sovereign_knowledge_compiler.runtime.api import MemoryRuntime

raw = [
    {"type": "transcript", "date": "2024-01-15",
     "content": "We decided to use PostgreSQL for the user database."},
]

# Privacy Guard runs first — the sovereign boundary.
manifest = compile_material(guard(raw), "memory/", version="v1")
print(manifest["fact_count"], "facts compiled")

rt = MemoryRuntime("memory/")
for r in rt.query("database"):
    print(r["content"])
```

### As a CLI

```bash
# compile (with PII redaction)
skc compile --material notes.json --output memory/ --version v1 --redact

# query
skc query --output memory/ --keyword postgres
skc query --output memory/ --tag technology
skc query --output memory/ --since 2024-01-01 --until 2024-02-01
```

## Architecture

| Layer | Module | Role |
|---|---|---|
| Input | `privacy/guard.py` | PII detection + redaction before compilation |
| Frontend | `compiler/frontend.py` | Orchestrates extract → consolidate → index → write |
| Extractor | `compiler/extractor.py` | Deterministic fact/decision extraction (no LLM needed) |
| Consolidator | `compiler/consolidator.py` | Dedupe + complementary merge at compile time |
| Indexer | `compiler/indexer.py` | Inverted index over tags + content for O(1) lookup |
| Artifacts | `artifacts/writer.py` | Versioned bundles, persisted via SDK `ArtifactStore` |
| Runtime | `runtime/api.py` | Lookup-only query API (no reasoning) |

## Design notes

- **Compile once, query cheaply.** The expensive step is extraction +
  consolidation at ingest. The runtime is a lookup service.
- **Versioned, immutable bundles.** Each compile writes `memory/<version>/`
  plus an immutable SDK artifact (`skc-memory-<version>`). Old versions are
  never mutated — incremental rebuilds add new versions.
- **Privacy by default.** The guard runs before the compiler sees anything;
  it decides what the compiler is even allowed to ingest.
- **Not everything needs an LLM.** Cheap deterministic extraction covers the
  common case; the local-model "Knowledge Compiler" deep-synthesis pass is a
  future extension, not a dependency.

## Status

Reference skeleton: extract → consolidate → index → persist → query is fully
working and tested (`pytest tests/`, 28 passing). The **CRDT sync layer**
(`sovereign_knowledge_compiler.sync`) is implemented and tested: multi-device
memory converges under arbitrary exchange order with no server, and concurrent
edits of the same entity resolve last-writer-wins with provenance. Roadmap
items still open: decay/compaction policy, and the provenance-tagged
conflict-resolution *review* surface, and the local-LLM "deep synthesis"
compile step.

## Multi-device sync (no cloud)

Compiled memory is replicated across the user's own devices with a state-based
CRDT (OR-Set over facts + version vectors). Every merge is commutative,
associative, and idempotent, so two devices can exchange state in any order,
any number of times, and always converge. Concurrent edits to the same entity
(resolved by `entity_id`) collapse to one value via last-writer-wins, and the
winning record is kept for inspection — never silently dropped.

```python
from sovereign_knowledge_compiler.sync import MemorySync

laptop = MemorySync("laptop")
phone = MemorySync("phone")

laptop.put({"content": "use postgres", "tags": ["db"]})
phone.put({"content": "use sqlite for mobile", "tags": ["db"]})

# exchange once, in either direction
laptop = laptop.merge(phone)
phone = phone.merge(laptop)
assert laptop.converged_with(phone)  # True, no server involved

# persist each replica to disk
laptop.save("laptop.sync.json")
```

Run `pytest tests/test_sync.py -v` to see the CRDT-law tests (commutativity,
associativity, idempotence, convergence under out-of-order exchange, LWW, and
delete propagation) — these are the guarantees that make server-less sync safe.
