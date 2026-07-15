"""CLI entry point: compile, query, and sync compiled memory from the terminal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compiler.frontend import compile_material
from .privacy.guard import guard as privacy_guard
from .runtime.api import MemoryRuntime
from .sync import MemorySync, sync_from_file


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

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
