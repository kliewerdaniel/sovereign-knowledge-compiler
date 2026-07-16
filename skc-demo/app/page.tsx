"use client";

import { useEffect, useState } from "react";
import KnowledgeGraph3D from "../components/KnowledgeGraph3D";
import { Stat, ThemeBars, Timeline, ReinforcedList, DecisionCards } from "../components/Dashboard";

type Dataset = {
  meta: any;
  stats: {
    posts: number; total_facts: number; decisions: number; synth_facts: number;
    decisions_with_rationale: number; reinforced_facts: number;
    max_concept_recurrence: number; unique_tags: number;
  };
  themes: { theme: string; count: number; decisions: { content: string; rationale: string; tags: string[]; date: string }[] }[];
  top_tags: { tag: string; count: number }[];
  graph: { nodes: { id: string; count: number; theme: string }[]; edges: { source: string; target: string; weight: number }[] };
  top_reinforced: { content: string; reinforcements: number; concept_recurrence: number; tags: string[]; is_decision: boolean }[];
  timeline: { month: string; facts: number; decisions: number }[];
};

export default function Page() {
  const [data, setData] = useState<Dataset | null>(null);

  useEffect(() => {
    fetch("/dataset.json")
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData(null));
  }, []);

  return (
    <main className="grid-bg" style={{ minHeight: "100vh" }}>
      {/* top nav */}
      <nav className="mono" style={{
        position: "sticky", top: 0, zIndex: 50,
        display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "14px 28px", borderBottom: "1px solid #1a2130",
        background: "rgba(5,6,10,0.8)", backdropFilter: "blur(10px)",
      }}>
        <span style={{ color: "#5eead4", fontWeight: 700, letterSpacing: 0.5 }}>
          SKC<span style={{ color: "#e6edf3" }}> · compile-time memory</span>
        </span>
        <a href="https://github.com/kliewerdaniel/sovereign-knowledge-compiler"
           target="_blank" rel="noreferrer"
           className="pill" style={{ color: "#e6edf3", textDecoration: "none" }}>
          github ↗
        </a>
      </nav>

      {/* hero */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "64px 28px 36px" }} className="section-fade">
        <div className="pill mono" style={{ marginBottom: 18 }}>
          LIVE DEMO · local-first · 153 blog posts · 0 cloud calls
        </div>
        <h1 style={{ fontSize: "clamp(34px,6vw,68px)", lineHeight: 1.05, margin: 0, fontWeight: 800 }}>
          <span className="gradient-text">I compiled my own blog</span>
          <br />into a decision graph.
        </h1>
        <p style={{ marginTop: 22, maxWidth: 720, fontSize: 17, color: "#9aa6b8", lineHeight: 1.6 }}>
          The Sovereign Knowledge Compiler reads every post, runs a local LLM
          (llama3.1:8b) to distill decisions and surface the <em>why</em>, and
          stores the result in a convergent, decaying memory layer. This page is
          the live proof — built entirely from the artifacts it generated.
        </p>
        {data && (
          <div className="mono" style={{ marginTop: 18, fontSize: 12.5, color: "#5eead4" }}>
            compiled in {data.meta.compile_seconds}s · model {data.meta.model} · {data.stats.posts} posts → {data.stats.total_facts} facts
          </div>
        )}
      </section>

      {/* stats */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "12px 28px 36px" }}>
        {data ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: 16 }}>
            <Stat label="blog posts compiled" value={data.stats.posts} accent="#5eead4" />
            <Stat label="facts extracted" value={data.stats.total_facts} accent="#818cf8" />
            <Stat label="decisions distilled" value={data.stats.decisions} accent="#f472b6" />
            <Stat label="decisions w/ rationale" value={data.stats.decisions_with_rationale} accent="#fbbf24" />
            <Stat label="LLM-synthesized facts" value={data.stats.synth_facts} accent="#60a5fa" />
            <Stat label="facts reinforced" value={data.stats.reinforced_facts} accent="#34d399" />
          </div>
        ) : (
          <div className="mono" style={{ color: "#5eead4" }}>loading compiled corpus…</div>
        )}
      </section>

      {/* 3D graph */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 28px" }}>
        <div className="card" style={{ padding: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
            <h2 style={{ fontSize: 22, margin: 0 }}>Concept Graph <span style={{ color: "#5eead4" }}>· 3D</span></h2>
            <span className="pill mono">drag to rotate · scroll to zoom · hover a node</span>
          </div>
          <div style={{ height: 520, borderRadius: 12, overflow: "hidden", background: "#05060a", border: "1px solid #1a2130" }}>
            {data ? (
              <KnowledgeGraph3D nodes={data.graph.nodes} edges={data.graph.edges} />
            ) : (
              <div className="mono" style={{ display: "grid", placeItems: "center", height: "100%", color: "#5eead4" }}>
                loading graph…
              </div>
            )}
          </div>
          <div className="mono" style={{ marginTop: 10, fontSize: 12, color: "#7c8798" }}>
            nodes = tags · size = frequency · edge = tags co-occur on a fact · color = theme
          </div>
        </div>
      </section>

      {/* themes + timeline */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 28px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div className="card p-6">
          <h2 style={{ fontSize: 20, margin: "0 0 16px" }}>Decisions by theme</h2>
          {data && <ThemeBars themes={data.themes.map((t) => ({ theme: t.theme, count: t.count }))} />}
        </div>
        <div className="card p-6">
          <h2 style={{ fontSize: 20, margin: "0 0 16px" }}>Compiled over time</h2>
          {data ? (
            <>
              <Timeline data={data.timeline} />
              <div className="mono" style={{ marginTop: 8, fontSize: 11.5, color: "#7c8798", display: "flex", gap: 16 }}>
                <span><span style={{ color: "#818cf8" }}>■</span> facts</span>
                <span><span style={{ color: "#f472b6" }}>■</span> decisions</span>
                <span>per month</span>
              </div>
            </>
          ) : <div className="mono" style={{ color: "#5eead4" }}>loading…</div>}
        </div>
      </section>

      {/* reinforced */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 28px" }}>
        <div className="card p-6">
          <h2 style={{ fontSize: 20, margin: "0 0 6px" }}>What the corpus keeps returning to</h2>
          <p style={{ margin: "0 0 16px", color: "#8b96a8", fontSize: 13.5 }}>
            Cross-post concept reinforcement: facts tagged with concepts that recur
            across many posts gain decay resistance. The number is how many distinct
            posts carry the concept — the memory layer's usage signal.
          </p>
          {data && <ReinforcedList items={data.top_reinforced} />}
        </div>
      </section>

      {/* decision cards */}
      <section style={{ maxWidth: 1180, margin: "0 auto", padding: "24px 28px 64px" }}>
        <h2 style={{ fontSize: 24, margin: "0 0 18px" }}>
          The decision graph <span style={{ color: "#5eead4" }}>· {data ? data.stats.decisions : "…"} choices</span>
        </h2>
        {data && <DecisionCards themes={data.themes} />}
      </section>

      <footer className="mono" style={{ borderTop: "1px solid #1a2130", padding: "28px", textAlign: "center", color: "#5b6678", fontSize: 12 }}>
        Sovereign Knowledge Compiler · compiled locally on macOS ·{" "}
        <a href="https://github.com/kliewerdaniel/sovereign-knowledge-compiler" style={{ color: "#5eead4" }}>source</a>
      </footer>
    </main>
  );
}
