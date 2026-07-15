"""Privacy Guard: enforce what gets compiled, retained, or discarded.

Runs before compilation. Detects likely PII (emails, phone numbers, API keys)
and redacts it, and gates anything that would leave the device. The guard is
the boundary that keeps compiled memory sovereign: it decides what the
compiler is even allowed to see.
"""

from __future__ import annotations

import re
from typing import Dict, List

_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE = re.compile(r"\b(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_API_KEY = re.compile(r"(?i)\b(?:api[_-]?key|token|secret)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{8,}")


def scan(material: List[Dict]) -> Dict[str, List[str]]:
    """Return detected PII per material item index (redaction candidates)."""
    findings: Dict[str, List[str]] = {}
    for i, item in enumerate(material or []):
        content = item.get("content", "")
        hits = []
        hits += [f"email:{m}" for m in _EMAIL.findall(content)]
        hits += [f"phone:{m}" for m in _PHONE.findall(content)]
        hits += [f"secret:{m.group(0)[:24]}" for m in _API_KEY.finditer(content)]
        if hits:
            findings[str(i)] = hits
    return findings


def redact(text: str) -> str:
    """Redact detected PII in a string."""
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _PHONE.sub("[REDACTED_PHONE]", text)
    text = _API_KEY.sub(lambda m: m.group(0).split(":")[0] + ":[REDACTED]", text)
    return text


def guard(material: List[Dict]) -> List[Dict]:
    """Return a redacted copy of the material (does not mutate the input)."""
    return [
        {**item, "content": redact(item.get("content", ""))}
        for item in (material or [])
    ]
