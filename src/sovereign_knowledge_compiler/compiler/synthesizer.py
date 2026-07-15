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
) -> List[Fact]:
    """Augment ``base_facts`` with a local-LLM synthesis pass.

    ``base_facts`` are the deterministic extractor's output. If ``client`` is
    None or unavailable, the base facts are returned unchanged (graceful
    degradation -- no fabrication). Otherwise the model's synthesised facts are
    merged in and de-duplicated by normalised content.
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

    return _merge_dedup(base_facts, synth)


def _norm(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip().lower())


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
