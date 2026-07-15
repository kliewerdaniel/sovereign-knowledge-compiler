"""Sync subpackage: conflict-free multi-device memory sync (no cloud).

Local-first memory that lives on one machine is fragile. Sovereign memory is
replicated across the user's devices -- laptop, phone, server -- without a
central server. This module provides the replication primitive: a state-based
CRDT (OR-Set over content-addressed facts + version vectors) plus a
last-writer-wins register with provenance for the rare case of conflicting
edits. All operations are commutative, associative, and idempotent, so two
devices can exchange state in any order, any number of times, and always
converge to the same memory.
"""

from .crdt import ORSet, VersionVector  # noqa: F401
from .memory_sync import MemorySync, sync_to_file, sync_from_file  # noqa: F401
