"""Deep synthesis: an optional local-LLM compile pass.

The deterministic ``extractor`` is the cheap, always-on default -- one Fact per
sentence, keyword-tagged. It is fast and dependency-free, but shallow: it cannot
merge two sentences into one insight, infer an implicit decision, or name the
rationale behind a choice. That deeper work is what the blog post reserves for
the local "Knowledge Compiler" pass.

This module runs that pass **locally** (Ollama or any OpenAI-compatible endpoint
on localhost) -- never a cloud API, honouring the sovereignty invariant. It is:

* **Optional.** Compilation works fully without it (deterministic extractor).
* **Gracefully degrading.** If no local model is reachable (offline, CI), it
  logs a note and returns the deterministic facts unchanged. It never fabricates
  synthesised facts when the model is unavailable.
* **Injectable.** The client is an interface, so tests run a deterministic mock
  with no network. CI never touches a real model.

The output is merged with (not a replacement for) the deterministic facts, then
de-duplicated by content, so deep synthesis only ever *adds* signal.
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Protocol

from ..artifacts.types import Fact, classify


# --------------------------------------------------------------------------- #
# Client interface + a local (Ollama / OpenAI-compatible) implementation.
# --------------------------------------------------------------------------- #
class LLMClient(Protocol):
    """Minimal text-completion interface. Any object with ``complete`` works."""

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2048) -> str:
        ...


class LocalLLMClient:
    """Talks to a local OpenAI-compatible or Ollama endpoint. No cloud.

    Defaults to Ollama's native ``/api/generate`` on ``http://localhost:11434``.
    Set ``api="openai"`` to use an OpenAI-compatible ``/v1/chat/completions``
    endpoint (e.g. llama.cpp server, LM Studio, vLLM) on localhost.
    """

    def __init__(
        self,
        model: str = "llama3.1",
        endpoint: str = "http://localhost:11434",
        api: str = "ollama",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.endpoint = endpoint.rstrip("/")
        self.api = api
        self.timeout = timeout

    def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 2048) -> str:
        if self.api == "openai":
            body = {
                "model": self.model,
                "messages": (
                    ([{"role": "system", "content": system}] if system else [])
                    + [{"role": "user", "content": prompt}]
                ),
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "stream": False,
            }
            out = self._post(f"{self.endpoint}/v1/chat/completions", body)
            return out["choices"][0]["message"]["content"]
        # default: Ollama native
        body = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
        }
        out = self._post(f"{self.endpoint}/api/generate", body)
        return out.get("response", "")

    def available(self) -> bool:
        """True if the local endpoint answers. Never raises."""
        try:
            probe = "/api/tags" if self.api == "ollama" else "/v1/models"
            req = urllib.request.Request(f"{self.endpoint}{probe}")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# Prompting + robust parsing.
# --------------------------------------------------------------------------- #
_SYSTEM = (
    "You are a knowledge compiler. You read raw source material and extract "
    "durable, atomic facts and decisions. Merge related sentences into a single "
    "clear fact. Surface implicit decisions and their rationale. Do NOT invent "
    "information that is not supported by the material."
)

_PROMPT_TEMPLATE = """\
Extract the durable facts and decisions from the material below.

Return ONLY a JSON array. Each element is an object:
  {{"content": "<one clear atomic fact or decision>",
    "tags": ["<lowercase topical tags>"],
    "is_decision": <true|false>,
    "rationale": "<why, if this is a decision; else empty string>"}}

Rules:
- Merge related statements into one fact; prefer few strong facts over many weak ones.
- Mark is_decision=true only for resolved choices ("we chose X", "adopt Y").
- Never invent facts not grounded in the material.
- Output must be valid JSON, nothing before or after the array.

MATERIAL:
{material}
"""


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    """Pull the first JSON array out of a model response, tolerating chatter.

    Returns [] if nothing parseable is found -- never raises. This is the
    honesty guard: a malformed model response yields no synthesised facts
    rather than garbage.
    """
    if not text:
        return []
    # Fast path: whole thing is JSON.
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    except Exception:
        pass
    # Strip common code fences.
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fenced:
        try:
            val = json.loads(fenced.group(1))
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        except Exception:
            pass
    # Balanced-bracket scan for the first [...] array.
    start = text.find("[")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    chunk = text[start : i + 1]
                    try:
                        val = json.loads(chunk)
                        if isinstance(val, list):
                            return [x for x in val if isinstance(x, dict)]
                    except Exception:
                        break
        start = text.find("[", start + 1)
    return []


def _to_facts(items: List[Dict[str, Any]], date: Optional[str], source: str) -> List[Fact]:
    facts: List[Fact] = []
    for it in items:
        content = str(it.get("content", "")).strip()
        if not content:
            continue
        tags = [str(t).lower() for t in it.get("tags", []) if str(t).strip()]
        rationale = str(it.get("rationale", "")).strip()
        if rationale:
            tags = tags + ["rationale"]
            content = f"{content} (rationale: {rationale})"
        is_decision = bool(it.get("is_decision", False)) or classify(content)
        facts.append(
            Fact(
                content=content,
                tags=tags,
                date=date,
                source=f"{source}:synth",
                is_decision=is_decision,
                confidence=0.9,  # model-derived, slightly below deterministic 1.0
            )
        )
    return facts


# --------------------------------------------------------------------------- #
# Public entry point.
# --------------------------------------------------------------------------- #
def deep_synthesize(
    material: List[Dict],
    base_facts: List[Fact],
    client: Optional[LLMClient] = None,
    *,
    max_chars: int = 6000,
    reinforce_sync=None,
) -> List[Fact]:
    """Augment ``base_facts`` with a local-LLM synthesis pass.

    ``base_facts`` are the deterministic extractor's output. If ``client`` is
    None or unavailable, the base facts are returned unchanged (graceful
    degradation -- no fabrication). Otherwise the model's synthesised facts are
    merged in and de-duplicated by normalised content.

    If ``reinforce_sync`` (a ``MemorySync``) is provided, every base fact the
    model actually drew on during synthesis is *reinforced* in that store,
    raising its resistance to decay. This closes the loop between the two
    newest layers: the compiler's reasoning becomes a usage signal for
    compaction. Reinforcement is best-effort and never affects the returned
    facts.
    """
    if client is None:
        return base_facts

    # Availability probe for the real client (mock clients omit it).
    check = getattr(client, "available", None)
    if callable(check) and not check():
        return base_facts

    synth: List[Fact] = []
    for item in material or []:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        prompt = _PROMPT_TEMPLATE.format(material=content[:max_chars])
        try:
            raw = client.complete(prompt, system=_SYSTEM, max_tokens=2048)
        except Exception:
            # a failing call on one item must not abort the whole compile
            continue
        items = _extract_json_array(raw)
        synth.extend(_to_facts(items, item.get("date"), item.get("type", "material")))

    if reinforce_sync is not None and synth:
        _reinforce_cited(reinforce_sync, base_facts, synth)

    return _merge_dedup(base_facts, synth)


def _reinforce_cited(sync, base_facts: List[Fact], synth: List[Fact]) -> int:
    """Reinforce the MemorySync entities whose value matches a cited base fact.

    Returns the number of entities reinforced. Best-effort: an entity is matched
    by normalised content between the base fact and the store's provenance
    values. Never raises.
    """
    try:
        cited_idx = cited_base_facts(base_facts, synth)
    except Exception:
        return 0
    if not cited_idx:
        return 0
    # map normalised content -> entity_id in the sync store
    content_to_eid = {}
    for eid, rec in getattr(sync, "provenance", {}).items():
        val = rec.get("value")
        text = val.get("content") if isinstance(val, dict) else val
        if isinstance(text, str):
            content_to_eid[_norm(text)] = eid
    reinforced = 0
    for i in cited_idx:
        key = _norm(base_facts[i].content)
        eid = content_to_eid.get(key)
        if eid is not None:
            sync.reinforce(eid)
            reinforced += 1
    return reinforced


def _norm(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip().lower())


def concept_recurrence(sync) -> Dict[str, int]:
    """Corpus-wide concept signal: for each entity in the store, how many
    *distinct source posts* share at least one of its tags.

    This is the cross-post generalisation of ``cited_base_facts``: instead of
    only rewarding verbatim reuse within one post, it rewards a *concept* that
    the corpus keeps returning to in different words. A fact tagged ``ollama``
    that appears (by tag) across 30 posts is far more load-bearing than a
    sentence copied into two posts.

    Returns ``{entity_id: recurrence_count}`` where recurrence is the number of
    distinct sources (post slugs) that carry any of the entity's tags, minus 1
    (its own), floored at 0. Requires provenance ``value`` dicts to carry
    ``tags`` and a ``slug``/``source`` for the originating post.
    """
    prov = getattr(sync, "provenance", {})

    def _slug(val, rec):
        if isinstance(val, dict) and val.get("slug"):
            return val["slug"]
        return rec.get("source") or rec.get("writer")

    # tag -> set of distinct source slugs that carry it
    tag_sources: Dict[str, set] = {}
    for eid, rec in prov.items():
        val = rec.get("value")
        tags = val.get("tags", []) if isinstance(val, dict) else []
        slug = _slug(val, rec)
        for t in tags:
            tag_sources.setdefault(t, set()).add(slug)

    out: Dict[str, int] = {}
    for eid, rec in prov.items():
        val = rec.get("value")
        tags = val.get("tags", []) if isinstance(val, dict) else []
        slug = _slug(val, rec)
        sources: set = set()
        for t in tags:
            sources |= tag_sources.get(t, set())
        sources.discard(slug)
        out[eid] = len(sources)
    return out


def reinforce_by_concept(sync, scale: int = 1, max_bonus: int = 50) -> int:
    """Reinforce every entity by its corpus-wide concept recurrence.

    Applies ``concept_recurrence`` and calls ``sync.reinforce`` so facts whose
    concepts recur across many posts gain decay resistance proportional to that
    recurrence (capped by ``max_bonus``). Returns the number of entities that
    received a bonus. This is the cross-post reinforcement that makes decay
    track "what the corpus keeps returning to", not just duplicated sentences.
    """
    rec_map = concept_recurrence(sync)
    touched = 0
    for eid, recurrence in rec_map.items():
        if recurrence <= 0:
            continue
        bonus = min(recurrence * scale, max_bonus)
        sync.reinforce(eid, by=bonus)
        touched += 1
    return touched


_STOP = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "was", "were", "be", "we", "our", "it", "its", "as", "with",
    "that", "this", "will", "has", "have", "had", "chose", "use", "using",
}


def _tokens(content: str) -> set:
    words = re.findall(r"[a-z0-9]+", content.lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def cited_base_facts(
    base: List[Fact], synth: List[Fact], threshold: float = 0.34
) -> List[int]:
    """Return indices into ``base`` of facts the synthesised facts drew on.

    A base fact is considered *cited* if any synthesised fact shares enough
    content tokens with it (overlap = shared / base_tokens). This is the usage
    signal that feeds decay resistance: facts the compiler actually reasoned
    over are more valuable and should decay more slowly.
    """
    synth_token_sets = [_tokens(f.content) for f in synth]
    cited: List[int] = []
    for i, bf in enumerate(base):
        bt = _tokens(bf.content)
        if not bt:
            continue
        for st in synth_token_sets:
            if not st:
                continue
            overlap = len(bt & st) / len(bt)
            if overlap >= threshold:
                cited.append(i)
                break
    return cited


def _merge_dedup(base: List[Fact], synth: List[Fact]) -> List[Fact]:
    """Merge synthesised facts into the base set, de-duplicating by content.

    Synthesised facts that duplicate a base fact are dropped (base wins, since
    it is verbatim from source). Genuinely new synthesised facts are appended.
    """
    seen = {_norm(f.content) for f in base}
    out = list(base)
    for f in synth:
        key = _norm(f.content)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
