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
working and tested (`pytest tests/`, 56 passing). The **CRDT sync layer**
(`sovereign_knowledge_compiler.sync`) is implemented and tested: Remove-Wins
Set + Lamport-ordered LWW + a human-overridable conflict ledger + reversible
decay/compaction. The **local-LLM deep-synthesis pass**
(`sovereign_knowledge_compiler.compiler.synthesizer`) is implemented and tested
against a real local model — all roadmap items are now landed.

## Multi-device sync (no cloud)

Compiled memory is replicated across the user's own devices with a
state-based CRDT. Every merge is commutative, associative, and idempotent, so
two devices can exchange state in any order, any number of times, and always
converge. Deletion uses **Remove-Wins** semantics (a delete tombstones a fact's
content hash, so it disappears from every replica after merge -- the correct
behaviour for "this fact no longer exists, anywhere").

Concurrent edits to the same entity (grouped by `entity_id`) resolve
**last-writer-wins by a Lamport clock**, not wall-clock time -- so a device
with a lagging system clock does not silently lose a later edit. The loser is
**kept in a conflict ledger**, so every auto-resolution is inspectable and
**overridable by a human** (the conflict-resolution surface the blog post
calls for). This is the human-in-the-loop guarantee made concrete.

```python
from sovereign_knowledge_compiler.sync import MemorySync

laptop = MemorySync("laptop")
phone = MemorySync("phone")
laptop.put({"content": "use postgres", "tags": ["db"]})
phone.put({"content": "use sqlite for mobile", "tags": ["db"]})

# exchange once, in either direction
laptop = laptop.merge(phone)
phone = phone.merge(laptop)
assert laptop.converged_with(phone)   # True, no server involved

# concurrent edits of the same entity resolve LWW by Lamport time
laptop.put({"content": "pool: 5"},  lamport=100, writer="laptop", entity_id="pool")
phone.put({"content": "pool: 20"}, lamport=200, writer="phone",  entity_id="pool")
merged = laptop.merge(phone)
assert merged.pending_conflicts()      # the losing value is recorded, not dropped
merged.resolve("pool", {"content": "pool: 5"})   # human override
assert merged.pending_conflicts() == {}         # conflict cleared
```

CLI: `skc sync --file-a A.sync.json --file-b B.sync.json` converges two
replicas; `skc conflicts --file A.sync.json` lists pending resolutions and
`--resolve-entity/--resolve-value` applies a human override. Run
`pytest tests/test_sync.py -v` to see the CRDT-law and conflict-review tests.

## Decay & compaction (reversible)

Compiled memory is not append-only forever. Old, unused facts fade so the
runtime stays sharp -- but sovereign memory never *silently* drops anything.
Decay is a **reversible overlay**, not destructive mutation:

* `CompactionPolicy` scores each fact's *relevance* from age, recency of use,
  and reinforcement count (how often it has been cited/queried). Facts below a
  threshold become compaction candidates.
* `compact()` moves candidates into an **archive register** (an LWW toggle per
  entity, keyed by Lamport clock) so two devices that compact or revive
  independently still converge. Archived facts leave `live_facts()` but stay
  fully present in the CRDT and the archive -- `revive()` brings them back, and
  `purge()` is the only irreversible step (and only works on archived facts).
* Reinforced or `protected_tags` facts never decay.

```python
from sovereign_knowledge_compiler.sync import CompactionPolicy
policy = CompactionPolicy()           # 90-day half-life by default
eids = sync.compact(policy, now=...)  # archive aged/unused facts
sync.revive(eid)                      # restore one
sync.purge(eid)                       # permanently delete (irreversible)
```

CLI: `skc decay --file A.sync.json` (dry-run candidates) / `--apply`;
`skc revive --file A.sync.json --entity <id>`; `skc purge ...`. Run
`pytest tests/test_compaction.py -v` for the decay/revive/convergence tests.

## Deep synthesis (optional local-LLM pass)

The deterministic extractor is the cheap, always-on default (one fact per
sentence, keyword-tagged). The **deep-synthesis pass** adds a *local* model on
top to do what heuristics can't: merge related sentences into one insight,
surface implicit decisions, and name the rationale behind a choice.

* **Local only.** Talks to Ollama (`/api/generate`) or any OpenAI-compatible
  endpoint (`/v1/chat/completions`) on localhost. Never a cloud API.
* **Gracefully degrading.** If no local model is reachable (offline, CI), it
  falls back to the deterministic facts unchanged — it never fabricates.
* **Additive + de-duplicated.** Synthesised facts are merged on top of the
  deterministic ones and de-duped by content, so deep synthesis only adds
  signal. Synthesised facts are tagged `source=<type>:synth`, `confidence=0.9`.

```python
from sovereign_knowledge_compiler.compiler.synthesizer import LocalLLMClient
from sovereign_knowledge_compiler.compiler.frontend import compile_material
client = LocalLLMClient(model="llama3.1", endpoint="http://localhost:11434")
compile_material(material, "out/", deep=True, client=client)
```

CLI: `skc compile --material notes.json --output out/ --deep --model llama3.1`
(add `--endpoint` / `--api openai` for other local servers). If the model is
unreachable the command logs a note to stderr and compiles deterministically.
Run `pytest tests/test_synthesizer.py -v` for the offline (mock-client) tests.
