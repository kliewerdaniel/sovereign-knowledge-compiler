#!/usr/bin/env python3
"""Build the compact JSON dataset that powers the live demo frontend.

Consumes the artifacts already produced by compile_blog_deep.py:
  blog_compile_out/bundle/blog-deep-v1/facts.jsonl   (compiled facts + decisions)
  blog_compile_out/memory.sync.json                  (MemorySync store: reinforcement, tags, slugs)

Applies cross-post CONCEPT reinforcement (the new SKC feature) on top of the
per-post citation reinforcement already in the store, then emits a single
dataset.json the Next.js app reads. Everything here is derived from real
compiled artifacts -- no fabrication.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from sovereign_knowledge_compiler.sync import MemorySync
from sovereign_knowledge_compiler.compiler.synthesizer import (
    concept_recurrence, reinforce_by_concept,
)

ROOT = Path("/Users/danielkliewer/sovereign-knowledge-compiler")
OUT_DIR = ROOT / "blog_compile_out"
BUNDLE = OUT_DIR / "bundle" / "blog-deep-v1"
DEST = ROOT / "skc-demo" / "public" / "dataset.json"

THEMES = {
    "local-first & sovereignty": ["local", "sovereign", "ollama", "offline", "privacy", "own"],
    "architecture & compiler": ["compil", "architect", "pipeline", "layer", "artifact", "graph", "runtime"],
    "models & inference": ["model", "llm", "llama", "fine-tun", "train", "inference", "embedding"],
    "agents & orchestration": ["agent", "orchestr", "persona", "workflow", "autonom"],
    "data & annotation": ["annotat", "rlhf", "dataset", "label", "reddit"],
    "web & deployment": ["django", "react", "fastapi", "netlify", "deploy", "api", "next"],
}


def theme_of(text: str) -> str:
    t = text.lower()
    for name, kws in THEMES.items():
        if any(k in t for k in kws):
            return name
    return "other"


def main() -> int:
    facts = [json.loads(l) for l in (BUNDLE / "facts.jsonl").read_text().splitlines() if l.strip()]
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    store = MemorySync.load(str(OUT_DIR / "memory.sync.json"))

    # --- Part A2: cross-post concept reinforcement on the real corpus store ---
    recurrence = concept_recurrence(store)
    touched = reinforce_by_concept(store, scale=1)
    max_rec = max(recurrence.values()) if recurrence else 0

    # top reinforced facts AFTER concept reinforcement (the corpus's load-bearing ideas)
    reinforced = []
    for eid, rec in store.provenance.items():
        val = rec.get("value", {})
        if not isinstance(val, dict):
            continue
        r = rec.get("reinforcements", 0)
        reinforced.append({
            "content": val.get("content", ""),
            "reinforcements": r,
            "concept_recurrence": recurrence.get(eid, 0),
            "slug": val.get("slug"),
            "tags": val.get("tags", []),
            "is_decision": val.get("is_decision", False),
        })
    reinforced.sort(key=lambda x: (x["concept_recurrence"], x["reinforcements"]), reverse=True)

    # --- decisions clustered by theme ---
    decisions = [f for f in facts if f.get("is_decision")]
    synth = [f for f in facts if (f.get("source") or "").endswith(":synth")]
    by_theme = defaultdict(list)
    for d in decisions:
        content = d.get("content", "")
        rationale = ""
        m = re.search(r"\(rationale:\s*(.+?)\)\s*$", content)
        if m:
            rationale = m.group(1)
            content = content[: m.start()].strip()
        by_theme[theme_of(d.get("content", ""))].append({
            "content": content[:280],
            "rationale": rationale[:200],
            "tags": d.get("tags", []),
            "date": d.get("date"),
        })

    theme_summary = [
        {"theme": name, "count": len(items), "decisions": items[:12]}
        for name, items in sorted(by_theme.items(), key=lambda kv: len(kv[1]), reverse=True)
    ]

    # --- concept graph: nodes = tags, edges = tags co-occurring on a fact ---
    tag_counts = Counter(t for f in facts for t in f.get("tags", []) if t != "rationale")
    top_tags = [t for t, _ in tag_counts.most_common(40)]
    tagset = set(top_tags)
    co = Counter()
    tag_themes = {}
    for f in facts:
        ftags = [t for t in f.get("tags", []) if t in tagset]
        for t in ftags:
            tag_themes.setdefault(t, theme_of(t + " " + f.get("content", "")))
        for i in range(len(ftags)):
            for j in range(i + 1, len(ftags)):
                a, b = sorted((ftags[i], ftags[j]))
                co[(a, b)] += 1
    nodes = [
        {"id": t, "count": tag_counts[t], "theme": tag_themes.get(t, "other")}
        for t in top_tags
    ]
    edges = [
        {"source": a, "target": b, "weight": w}
        for (a, b), w in co.most_common(240)
    ]

    # --- timeline: decisions + facts per month ---
    month_dec = Counter()
    month_fact = Counter()
    for f in facts:
        d = f.get("date") or ""
        mo = d[:7] if len(d) >= 7 else "unknown"
        month_fact[mo] += 1
        if f.get("is_decision"):
            month_dec[mo] += 1
    months = sorted(m for m in month_fact if m != "unknown")
    timeline = [
        {"month": m, "facts": month_fact[m], "decisions": month_dec[m]}
        for m in months
    ]

    # distinct source posts
    posts = sorted({rec.get("value", {}).get("slug") for rec in store.provenance.values()
                    if isinstance(rec.get("value"), dict) and rec.get("value", {}).get("slug")})

    dataset = {
        "meta": {
            "generated_from": "153 blog posts compiled by Sovereign Knowledge Compiler",
            "model": manifest.get("model", "llama3.1:8b"),
            "local_only": True,
            "compile_seconds": 2185,
        },
        "stats": {
            "posts": manifest.get("source_material_count", len(posts)),
            "total_facts": len(facts),
            "decisions": len(decisions),
            "synth_facts": len(synth),
            "decisions_with_rationale": sum(1 for d in decisions if "rationale:" in d.get("content", "")),
            "reinforced_facts": touched,
            "max_concept_recurrence": max_rec,
            "unique_tags": len(tag_counts),
        },
        "themes": theme_summary,
        "top_tags": [{"tag": t, "count": c} for t, c in tag_counts.most_common(25)],
        "graph": {"nodes": nodes, "edges": edges},
        "top_reinforced": reinforced[:40],
        "timeline": timeline,
    }

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {DEST}  ({DEST.stat().st_size//1024} KB)")
    print(f"stats: {json.dumps(dataset['stats'])}")
    print(f"themes: {[(t['theme'], t['count']) for t in theme_summary]}")
    print(f"graph: {len(nodes)} nodes, {len(edges)} edges")
    print(f"max concept recurrence: {max_rec}; top concept: "
          f"{reinforced[0]['tags'] if reinforced else '-'} rec={reinforced[0]['concept_recurrence'] if reinforced else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
