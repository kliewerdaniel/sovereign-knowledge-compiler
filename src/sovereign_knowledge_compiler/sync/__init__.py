"""Sync subpackage: conflict-free multi-device memory sync (no cloud).

Local-first memory that lives on one machine is fragile. Sovereign memory is
replicated across the user's devices -- laptop, phone, server -- without a
central server. This module provides the replication primitive: a Remove-Wins
Set CRDT over content-addressed facts, a Lamport clock for causal LWW ordering,
and a conflict ledger so edit conflicts are inspectable and overridable.
"""

from .crdt import FactSet, VersionVector, LamportClock  # noqa: F401
from .memory_sync import (  # noqa: F401
    MemorySync, sync_to_file, sync_from_file,
)
from .compaction import CompactionPolicy, archive_key  # noqa: F401
