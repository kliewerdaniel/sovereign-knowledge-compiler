"""CLI entry point: compile, query, and sync compiled memory from the terminal."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .compiler.frontend import compile_material
from .privacy.guard import guard as privacy_guard
from .runtime.api import MemoryRuntime
from .sync import MemorySync, sync_from_file, CompactionPolicy


def _cmd_compile(args) -> int:
    material = json.loads(Path(args.material).read_text(encoding="utf-8"))
    if args.redact:
        material = privacy_guard(material)
    manifest = compile_material(
        material, args.output, version=args.version,
        source_label=args.material,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


def _cmd_query(args) -> int:
    rt = MemoryRuntime(args.output)
    if args.tag:
        results = rt.query_by_tag(args.tag)
    elif args.since and args.until:
        results = rt.query_date_range(args.since, args.until)
    else:
        results = rt.query(args.keyword)
    for r in results:
        print(f"[{r.get('date')}] {r.get('content')}")
    return 0


def _cmd_sync(args) -> int:
    """Exchange two replica files so both converge (server-less sync)."""
    a = sync_from_file(args.file_a, "a")
    b = sync_from_file(args.file_b, "b")
    a2 = a.merge(b)
    b2 = b.merge(a)
    if args.out_a:
        a2.save(args.out_a)
    if args.out_b:
        b2.save(args.out_b)
    print(f"converged: {a2.converged_with(b2)}")
    print(f"live facts after sync: {len(a2.live_facts())}")
    pending = a2.pending_conflicts()
    if pending:
        print(f"pending conflicts: {len(pending)} entity(ies) awaiting review")
    return 0


def _cmd_conflicts(args) -> int:
    """List or resolve LWW conflicts in a replica file."""
    s = sync_from_file(args.file, "reviewer")
    if args.resolve_entity and args.resolve_value is not None:
        s.resolve(args.resolve_entity, json.loads(args.resolve_value))
        s.save(args.file)
        print(f"resolved {args.resolve_entity} -> {args.resolve_value}")
        return 0
    pending = s.pending_conflicts()
    if not pending:
        print("no pending conflicts")
        return 0
    for eid, losers in pending.items():
        winner = s.provenance.get(eid, {}).get("value")
        print(f"\nentity: {eid}")
        print(f"  winner : {winner}")
        for i, l in enumerate(losers):
            print(f"  loser[{i}]: {l['value']}  (writer={l['writer']}, lamport={l['lamport']})")
    return 0


def _policy_from_args(args) -> CompactionPolicy:
    protected = set(args.protect or [])
    return CompactionPolicy(
        half_life=args.half_life, stale_after=args.stale_after,
        age_weight=args.age_weight, stale_weight=args.stale_weight,
        reinforcement_weight=args.reinf_weight, threshold=args.threshold,
        protected_tags=protected,
    )


def _cmd_decay(args) -> int:
    """List or apply compaction candidates for a replica file."""
    s = sync_from_file(args.file, "decay")
    policy = _policy_from_args(args)
    now = time.time() if args.now is None else args.now
    cands = policy.candidates(s.provenance, now, s.facts.values())
    if args.apply:
        archived = s.compact(policy, now=now)
        s.save(args.file)
        print(f"archived {len(archived)} fact(s): {archived}")
    else:
        print(f"compaction candidates ({len(cands)}):")
        for eid in cands:
            rec = s.provenance.get(eid, {})
            print(f"  {eid}: {rec.get('value')}  (relevance={policy.relevance(rec, now):.3f})")
    return 0


def _cmd_revive(args) -> int:
    """Revive an archived (compacted) fact by entity id."""
    s = sync_from_file(args.file, "reviewer")
    ok = s.revive(args.entity)
    s.save(args.file)
    print(f"revived {args.entity}: {ok}")
    return 0


def _cmd_purge(args) -> int:
    """Permanently delete an archived fact (irreversible)."""
    s = sync_from_file(args.file, "reviewer")
    ok = s.purge(args.entity)
    s.save(args.file)
    print(f"purged {args.entity}: {ok}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="skc", description="Sovereign Knowledge Compiler")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("compile", help="Compile raw material into artifacts")
    c.add_argument("--material", required=True, help="JSON file: list of {type,date,content}")
    c.add_argument("--output", required=True, help="Output directory for versioned bundles")
    c.add_argument("--version", default="v1")
    c.add_argument("--redact", action="store_true", help="Run the Privacy Guard first")
    c.set_defaults(func=_cmd_compile)

    q = sub.add_parser("query", help="Query compiled memory")
    q.add_argument("--output", required=True)
    q.add_argument("--keyword", default="")
    q.add_argument("--tag", default="")
    q.add_argument("--since", default="")
    q.add_argument("--until", default="")
    q.set_defaults(func=_cmd_query)

    s = sub.add_parser("sync", help="Exchange two replica files so both converge")
    s.add_argument("--file-a", required=True, help="Path to replica A sync state")
    s.add_argument("--file-b", required=True, help="Path to replica B sync state")
    s.add_argument("--out-a", default="", help="Where to write merged replica A")
    s.add_argument("--out-b", default="", help="Where to write merged replica B")
    s.set_defaults(func=_cmd_sync)

    cf = sub.add_parser("conflicts", help="List or resolve LWW conflicts in a replica")
    cf.add_argument("--file", required=True, help="Path to a replica sync state")
    cf.add_argument("--resolve-entity", default="", help="Entity id to override")
    cf.add_argument("--resolve-value", default=None, help="JSON value to pin for that entity")
    cf.set_defaults(func=_cmd_conflicts)

    dc = sub.add_parser("decay", help="List/apply compaction candidates (decay)")
    dc.add_argument("--file", required=True, help="Path to a replica sync state")
    dc.add_argument("--apply", action="store_true", help="Archive the candidates")
    dc.add_argument("--now", type=float, default=None, help="Override 'now' (epoch seconds)")
    dc.add_argument("--half-life", type=float, default=90.0 * 86400.0)
    dc.add_argument("--stale-after", type=float, default=180.0 * 86400.0)
    dc.add_argument("--age-weight", type=float, default=1.0)
    dc.add_argument("--stale-weight", type=float, default=1.0)
    dc.add_argument("--reinf-weight", type=float, default=2.0)
    dc.add_argument("--threshold", type=float, default=0.0)
    dc.add_argument("--protect", action="append", default=[], help="Tag to never decay (repeatable)")
    dc.set_defaults(func=_cmd_decay)

    rv = sub.add_parser("revive", help="Revive an archived (compacted) fact")
    rv.add_argument("--file", required=True, help="Path to a replica sync state")
    rv.add_argument("--entity", required=True, help="Entity id to revive")
    rv.set_defaults(func=_cmd_revive)

    pg = sub.add_parser("purge", help="Permanently delete an archived fact (irreversible)")
    pg.add_argument("--file", required=True, help="Path to a replica sync state")
    pg.add_argument("--entity", required=True, help="Entity id to purge")
    pg.set_defaults(func=_cmd_purge)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
