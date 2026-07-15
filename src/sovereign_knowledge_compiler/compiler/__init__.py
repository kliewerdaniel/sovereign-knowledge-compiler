"""Compiler subpackage: extract -> consolidate -> index -> write."""

from .frontend import compile_material  # noqa: F401
from .extractor import extract_facts  # noqa: F401
from .consolidator import consolidate  # noqa: F401
from .indexer import build_index  # noqa: F401
