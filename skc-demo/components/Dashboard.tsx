"use client";

import { useEffect, useRef, useState } from "react";

export function useCountUp(target: number, dur = 1400) {
  const [val, setVal] = useState(0);
  const ref = useRef<HTMLSpanElement>(null);
  const started = useRef(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !started.current) {
        started.current = true;
        const t0 = performance.now();
        const tick = (t: number) => {
          const p = Math.min(1, (t - t0) / dur);
          const eased = 1 - Math.pow(1 - p, 3);
          setVal(Math.round(target * eased));
          if (p < 1) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [target, dur]);
  return { val, ref };
}

export function Stat({
  label,
  value,
  suffix = "",
  accent = "#5eead4",
}: {
  label: string;
  value: number;
  suffix?: string;
  accent?: string;
}) {
  const { val, ref } = useCountUp(value);
  return (
    <div className="card p-5 section-fade">
      <div
        className="mono glow-text"
        style={{ fontSize: 40, fontWeight: 700, color: accent, lineHeight: 1 }}
      >
        <span ref={ref}>{val.toLocaleString()}</span>
        {suffix}
      </div>
      <div style={{ marginTop: 8, color: "#8b96a8", fontSize: 13 }}>{label}</div>
    </div>
  );
}

export function ThemeBars({
  themes,
}: {
  themes: { theme: string; count: number }[];
}) {
  const max = Math.max(...themes.map((t) => t.count), 1);
  const colors: Record<string, string> = {
    "local-first & sovereignty": "#5eead4",
    "architecture & compiler": "#818cf8",
    "models & inference": "#f472b6",
    "agents & orchestration": "#fbbf24",
    "data & annotation": "#34d399",
    "web & deployment": "#60a5fa",
    other: "#64748b",
  };
  return (
    <div className="flex flex-col gap-3">
      {themes.map((t) => (
        <div key={t.theme}>
          <div className="flex justify-between mb-1" style={{ fontSize: 13 }}>
            <span style={{ color: "#c7d0dd" }}>{t.theme}</span>
            <span className="mono" style={{ color: colors[t.theme] || "#64748b" }}>
              {t.count}
            </span>
          </div>
          <div style={{ height: 8, background: "#111725", borderRadius: 6, overflow: "hidden" }}>
            <div
              style={{
                height: "100%",
                width: `${(t.count / max) * 100}%`,
                background: colors[t.theme] || "#64748b",
                borderRadius: 6,
                transition: "width 1s ease",
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export function Timeline({
  data,
}: {
  data: { month: string; facts: number; decisions: number }[];
}) {
  const max = Math.max(...data.map((d) => d.facts), 1);
  return (
    <div className="flex items-end gap-1" style={{ height: 160 }}>
      {data.map((d) => (
        <div key={d.month} className="flex-1 flex flex-col items-center justify-end group" style={{ height: "100%" }}>
          <div className="relative w-full flex flex-col justify-end" style={{ height: "100%" }}>
            <div
              title={`${d.month}: ${d.facts} facts, ${d.decisions} decisions`}
              style={{
                height: `${(d.facts / max) * 100}%`,
                background: "linear-gradient(180deg,#818cf8,#5eead4)",
                borderRadius: "3px 3px 0 0",
                minHeight: 2,
                position: "relative",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  bottom: 0,
                  width: "100%",
                  height: `${(d.decisions / Math.max(d.facts, 1)) * 100}%`,
                  background: "#f472b6",
                  borderRadius: "0 0 0 0",
                  opacity: 0.85,
                }}
              />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function ReinforcedList({
  items,
}: {
  items: {
    content: string;
    reinforcements: number;
    concept_recurrence: number;
    tags: string[];
    is_decision: boolean;
  }[];
}) {
  const max = Math.max(...items.map((i) => i.concept_recurrence), 1);
  return (
    <div className="flex flex-col gap-2">
      {items.slice(0, 14).map((it, i) => (
        <div key={i} className="card p-3 flex items-center gap-3">
          <div
            className="mono"
            style={{
              minWidth: 52,
              textAlign: "center",
              color: "#05060a",
              background: `hsl(${168 - (it.concept_recurrence / max) * 40}, 70%, ${45 + (it.concept_recurrence / max) * 20}%)`,
              borderRadius: 8,
              padding: "4px 6px",
              fontWeight: 700,
              fontSize: 13,
            }}
            title="cross-post concept recurrence"
          >
            {it.concept_recurrence}×
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, color: "#dbe2ec", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {it.is_decision && (
                <span className="pill" style={{ color: "#f472b6", borderColor: "#3a2233", marginRight: 6 }}>
                  decision
                </span>
              )}
              {it.content}
            </div>
            <div style={{ marginTop: 3, display: "flex", gap: 4, flexWrap: "wrap" }}>
              {it.tags.filter((t) => t !== "rationale").slice(0, 4).map((t) => (
                <span key={t} className="pill mono">{t}</span>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function DecisionCards({
  themes,
}: {
  themes: { theme: string; count: number; decisions: { content: string; rationale: string; tags: string[]; date: string }[] }[];
}) {
  const [active, setActive] = useState(0);
  const colors: Record<string, string> = {
    "local-first & sovereignty": "#5eead4",
    "architecture & compiler": "#818cf8",
    "models & inference": "#f472b6",
    "agents & orchestration": "#fbbf24",
    "data & annotation": "#34d399",
    "web & deployment": "#60a5fa",
    other: "#64748b",
  };
  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-5">
        {themes.map((t, i) => (
          <button
            key={t.theme}
            onClick={() => setActive(i)}
            className="mono"
            style={{
              fontSize: 12,
              padding: "6px 12px",
              borderRadius: 999,
              border: `1px solid ${active === i ? colors[t.theme] : "#1b2230"}`,
              background: active === i ? `${colors[t.theme]}22` : "transparent",
              color: active === i ? colors[t.theme] : "#8b96a8",
              cursor: "pointer",
            }}
          >
            {t.theme} · {t.count}
          </button>
        ))}
      </div>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))" }}>
        {themes[active].decisions.map((d, i) => (
          <div key={i} className="card p-4">
            <div style={{ fontSize: 14, color: "#e6edf3", lineHeight: 1.5 }}>{d.content}</div>
            {d.rationale && (
              <div style={{ marginTop: 8, fontSize: 12.5, color: "#8b96a8", borderLeft: `2px solid ${colors[themes[active].theme]}`, paddingLeft: 8 }}>
                {d.rationale}
              </div>
            )}
            <div style={{ marginTop: 10, display: "flex", gap: 4, flexWrap: "wrap" }}>
              {d.tags.filter((t) => t !== "rationale").slice(0, 4).map((t) => (
                <span key={t} className="pill mono">{t}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
