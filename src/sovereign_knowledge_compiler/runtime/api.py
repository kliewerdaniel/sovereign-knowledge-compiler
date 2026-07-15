"""Runtime API: cheap lookups against compiled memory. No reasoning here.

The runtime is a lookup service, not an inference service. It reads the static
artifacts produced at compile time and answers queries in O(index) time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from ..artifacts.types import Fact
from ..artifacts.writer import read_bundle


class MemoryRuntime:
    """Serve compiled memory from a versioned output directory."""

    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self._versions: Dict[str, List[Fact]] = {}
        self._load()

    def _load(self) -> None:
        if not self.output_dir.is_dir():
            return
        for vdir in sorted(self.output_dir.iterdir()):
            if vdir.is_dir() and (vdir / "facts.jsonl").is_file():
                bundle = read_bundle(str(vdir))
                # later versions override earlier for the same fact content
                for f in bundle.facts:
                    self._versions.setdefault(vdir.name, []).append(f)

    @property
    def latest_version(self) -> str:
        return sorted(self._versions)[-1] if self._versions else ""

    def _all_facts(self) -> List[Fact]:
        out: List[Fact] = []
        for v in sorted(self._versions):
            out.extend(self._versions[v])
        return out

    def query(self, keyword: str) -> List[Dict]:
        """Keyword search over fact content (case-insensitive substring)."""
        kw = keyword.lower()
        return [
            f.to_dict()
            for f in self._all_facts()
            if kw in f.content.lower()
        ]

    def query_by_tag(self, tag: str) -> List[Dict]:
        return [
            f.to_dict()
            for f in self._all_facts()
            if tag in f.tags
        ]

    def query_date_range(self, start: str, end: str) -> List[Dict]:
        return [
            f.to_dict()
            for f in self._all_facts()
            if f.date and start <= f.date <= end
        ]
