#!/usr/bin/env python3
"""Compile the full blog corpus with the deep-synthesis pass and inspect the
resulting decision graph + reinforcement signal.

Pipeline per post:
  raw markdown -> strip frontmatter -> material {type,date,content}
  deterministic extract -> deep_synthesize (local llama) -> reinforce cited
  facts in a shared MemorySync store.

Outputs (written to --out dir):
  - bundle/ : the compiled ArtifactBundle (facts + index + manifest)
  - memory.sync.json : the MemorySync store (with reinforcement counts)
  - report.json : decision graph + top reinforced facts + stats
Prints a human summary to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from sovereign_knowledge_compiler.compiler.extractor import extract_facts
from sovereign_knowledge_compiler.compiler.synthesizer import (
    LocalLLMClient, deep_synthesize,
)
from sovereign_knowledge_compiler.compiler.consolidator import consolidate
from sovereign_knowledge_compiler.compiler.indexer import build_index
from sovereign_knowledge_compiler.artifacts.types import ArtifactBundle
from sovereign_knowledge_compiler.artifacts.writer import write_bundle
from sovereign_knowledge_compiler.sync import MemorySync

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_CODEISH = re.compile(r"[{}<>=;|`]|def |class |import |http|www\.|\.py|\.md|/\w+/")


def is_quality_fact(content: str) -> bool:
    """Keep human-prose facts; drop code lines, URLs, headings, fragments.

    The deterministic extractor emits one 'fact' per markdown line, most of
    which are noise for a decision graph. This filter keeps sentence-like prose
    so the corpus-scale run stays bounded and meaningful.
    """
    c = content.strip()
    if not (30 <= len(c) <= 300):
        return False
    if c[0] in "#-*>|[(" or c.startswith("!["):
        return False
    if "](" in c or "://" in c or c.count("#") > 0:
        return False
    if _CODEISH.search(c):
        return False
    letters = sum(ch.isalpha() or ch.isspace() for ch in c)
    if letters / max(len(c), 1) < 0.85:
        return False
    if len(c.split()) < 5:
        return False
    # require it to read like a sentence (ends with terminal punctuation)
    if c[-1] not in ".!?":
        return False
    return True


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def post_to_material(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    body = strip_frontmatter(raw)
    m = DATE_RE.search(path.name)
    date = m.group(1) if m else None
    return {"type": "blog", "date": date, "content": body, "slug": path.stem}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blog", default="/Users/danielkliewer/a10/sovereign-ai-site/content/blog")
    ap.add_argument("--out", default="/Users/danielkliewer/sovereign-knowledge-compiler/blog_compile_out")
    ap.add_argument("--model", default="llama3.1:8b")
    ap.add_argument("--endpoint", default="http://localhost:11434")
    ap.add_argument("--max-chars", type=int, default=6000, help="Per-post material cap sent to model")
    ap.add_argument("--limit", type=int, default=0, help="Only first N posts (0=all)")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    blog = Path(args.blog)
    posts = sorted(p for p in blog.glob("*.md") if p.name != "temp.md")
    if args.limit:
        posts = posts[: args.limit]
    print(f"[start] {len(posts)} posts | model={args.model} | endpoint={args.endpoint}", flush=True)

    client = LocalLLMClient(model=args.model, endpoint=args.endpoint, timeout=args.timeout)
    if not client.available():
        print(f"[fatal] local model unreachable at {args.endpoint}", file=sys.stderr)
        return 2

    store = MemorySync("blog-corpus")
    all_facts = []          # accumulate consolidated facts across posts
    per_post = []
    t0 = time.time()

    for i, path in enumerate(posts, 1):
        mat = post_to_material(path)
        material = [mat]
        raw_base = extract_facts(material)
        # quality-filter to human prose; the extractor emits one fact per line
        base = [f for f in raw_base if is_quality_fact(f.content)]
        # seed base facts into the store so they can be reinforced when cited
        for f in base:
            eid = store.put({"content": f.content, "tags": f.tags,
                             "is_decision": f.is_decision, "slug": mat["slug"]})
            store.provenance[eid].setdefault("reinforcements", 0)
        # deep synthesis + reinforcement of cited base facts
        out = deep_synthesize(material, base, client=client,
                              max_chars=args.max_chars, reinforce_sync=store)
        synth = [f for f in out if f.source and str(f.source).endswith(":synth")]
        synth_added = len(synth)
        # keep only distilled signal for the corpus bundle: model synth + decisions
        distilled = synth + [f for f in base if f.is_decision]
        all_facts.extend(distilled)
        per_post.append({"slug": mat["slug"], "date": mat["date"],
                         "base": len(base), "synth_added": synth_added})
        dt = time.time() - t0
        rate = dt / i
        eta = rate * (len(posts) - i)
        print(f"[{i}/{len(posts)}] {mat['slug'][:60]:60s} "
              f"base={len(base):3d} +synth={synth_added:2d} "
              f"| {dt:6.0f}s elapsed, ETA {eta:5.0f}s", flush=True)

    # consolidate the whole corpus into one bundle
    consolidated = consolidate(all_facts)
    index = build_index(consolidated)
    decisions = [f for f in consolidated if f.is_decision]
    bundle = ArtifactBundle(
        version="blog-deep-v1",
        facts=consolidated,
        index=index,
        manifest={
            "version": "blog-deep-v1",
            "fact_count": len(consolidated),
            "decision_count": len(decisions),
            "source_material_count": len(posts),
            "deep_synthesis": True,
            "model": args.model,
        },
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = write_bundle(str(out_dir / "bundle"), bundle)
    store.save(str(out_dir / "memory.sync.json"))

    # reinforcement report: which facts the compiler actually reasoned over
    reinforced = []
    for eid, rec in store.provenance.items():
        r = rec.get("reinforcements", 0)
        if r > 0:
            val = rec.get("value", {})
            reinforced.append({
                "reinforcements": r,
                "content": val.get("content", "") if isinstance(val, dict) else str(val),
                "slug": val.get("slug") if isinstance(val, dict) else None,
                "is_decision": val.get("is_decision") if isinstance(val, dict) else None,
            })
    reinforced.sort(key=lambda x: x["reinforcements"], reverse=True)

    report = {
        "posts": len(posts),
        "total_facts": len(consolidated),
        "decisions": len(decisions),
        "synth_facts_added_total": sum(p["synth_added"] for p in per_post),
        "reinforced_fact_count": len(reinforced),
        "top_reinforced": reinforced[:30],
        "decision_graph_sample": [
            {"content": f.content, "tags": f.tags, "date": f.date, "source": f.source}
            for f in decisions[:40]
        ],
        "elapsed_sec": round(time.time() - t0, 1),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"DONE in {report['elapsed_sec']}s")
    print(f"  posts compiled     : {report['posts']}")
    print(f"  total facts        : {report['total_facts']}")
    print(f"  decisions          : {report['decisions']}")
    print(f"  synth facts added  : {report['synth_facts_added_total']}")
    print(f"  facts reinforced   : {report['reinforced_fact_count']}")
    print(f"\nTop 15 reinforced facts (usage signal from deep synthesis):")
    for r in reinforced[:15]:
        d = "DECISION" if r["is_decision"] else "fact"
        print(f"  [{r['reinforcements']}x {d}] {r['content'][:90]}")
    print(f"\nartifacts: {bundle_path}")
    print(f"report:    {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
