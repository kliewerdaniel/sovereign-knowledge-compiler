"""Artifact writer/reader: persist compiled memory as versioned bundles.

This is where SKC compounds on knowledge-compiler-sdk: bundles are written
through the SDK's ``ArtifactStore``, so each compile batch is an immutable,
content-hashed, provenance-tracked artifact on disk -- exactly the "inspectable
artifacts" the blog post argues for. We map one SDK artifact type per version
(``skc-memory-v1``, ``skc-memory-v2``, ...) so incremental rebuilds add new
versions without mutating old ones.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from ..artifacts.types import ArtifactBundle, Fact

# Make the SDK importable when SKC is installed standalone or sits beside it.
_HERE_FILE = os.path.abspath(__file__)
# candidates: sibling dir, parent-of-parent, and GOPATH-style
_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(_HERE_FILE))))),  # .../<sibling>/knowledge-compiler-sdk
    os.path.expanduser("~/knowledge-compiler-sdk"),
    "/Users/danielkliewer/knowledge-compiler-sdk",
]
for _SDK in _CANDIDATES:
    if os.path.isdir(os.path.join(_SDK, "compiler", "core")):
        if _SDK not in sys.path:
            sys.path.insert(0, _SDK)
        break
else:
    _SDK = None  # type: ignore

try:
    if _SDK:
        from compiler.core import ArtifactStore  # type: ignore
    else:
        ArtifactStore = None  # type: ignore
except Exception:  # pragma: no cover - SDK optional at import time
    ArtifactStore = None  # type: ignore


def _artifact_type(version: str) -> str:
    return f"skc-memory-{version}"


def write_bundle(output_dir: str, bundle: ArtifactBundle) -> str:
    """Write a versioned bundle to ``output_dir``.

    Falls back to a plain JSONL+JSON layout under ``output_dir/<version>/`` if
    the SDK is unavailable, so the package is usable standalone; when the SDK
    is present the same data is also recorded as an immutable artifact via
    ``ArtifactStore``.
    """
    out = Path(output_dir) / bundle.version
    out.mkdir(parents=True, exist_ok=True)

    facts_file = out / "facts.jsonl"
    with open(facts_file, "w", encoding="utf-8") as fh:
        for f in bundle.facts:
            fh.write(json.dumps(f.to_dict(), ensure_ascii=False) + "\n")

    index_file = out / "index.json"
    with open(index_file, "w", encoding="utf-8") as fh:
        json.dump(bundle.index, fh, ensure_ascii=False, indent=2)

    manifest_file = out / "manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as fh:
        json.dump(bundle.manifest, fh, ensure_ascii=False, indent=2)

    # Record as an immutable SDK artifact if available.
    if ArtifactStore is not None:
        store = ArtifactStore(os.path.join(output_dir, ".build"))
        store.write(
            _artifact_type(bundle.version),
            bundle.to_dict(),
            pass_id=f"skc:compile:{bundle.version}",
            source_artifacts=[],
            schema_id="skc-memory-bundle",
        )
    return str(out)


def read_bundle(version_dir: str) -> ArtifactBundle:
    """Read a previously written versioned bundle back into memory."""
    version_dir = Path(version_dir)
    version = version_dir.name
    facts: List[Fact] = []
    facts_file = version_dir / "facts.jsonl"
    if facts_file.is_file():
        for line in facts_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                facts.append(Fact.from_dict(json.loads(line)))
    index = {}
    index_file = version_dir / "index.json"
    if index_file.is_file():
        index = json.loads(index_file.read_text(encoding="utf-8"))
    manifest = {}
    manifest_file = version_dir / "manifest.json"
    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    return ArtifactBundle(version=version, facts=facts, index=index,
                          manifest=manifest)
