"use client";

import { useState, useTransition } from "react";
import { AnalyzePlayerResponse } from "../../_lib/api";

type AnalysisState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; data: AnalyzePlayerResponse }
  | { status: "error"; message: string };

function StatRow({ label, value }: { label: string; value: string | number | null }) {
  if (value === null || value === undefined) return null;
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "baseline",
        padding: "6px 0",
        borderBottom: "1px solid var(--wire)",
      }}
    >
      <span className="label">{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--bone)" }}>
        {value}
      </span>
    </div>
  );
}

function pct(v: number | null) {
  if (v === null) return null;
  return (v * 100).toFixed(1) + "%";
}

function fmt(v: number | null, decimals = 1) {
  if (v === null) return null;
  return v.toFixed(decimals);
}

export default function PlayerSearch() {
  const [query, setQuery] = useState("");
  const [analysis, setAnalysis] = useState<AnalysisState>({ status: "idle" });
  const [isPending, startTransition] = useTransition();

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const name = query.trim();
    if (!name) return;

    setAnalysis({ status: "loading" });

    startTransition(async () => {
      try {
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL}/analysis/player?player_name=${encodeURIComponent(name)}&season=2025`
        );
        if (!res.ok) {
          const body = await res.text();
          setAnalysis({ status: "error", message: `${res.status}: ${body}` });
          return;
        }
        const data: AnalyzePlayerResponse = await res.json();
        setAnalysis({ status: "done", data });
      } catch (e) {
        setAnalysis({
          status: "error",
          message: e instanceof Error ? e.message : "Request failed",
        });
      }
    });
  }

  const p = analysis.status === "done" ? analysis.data.payload.player : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Search bar */}
      <form onSubmit={handleSearch} style={{ display: "flex", gap: 8 }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search player…"
          style={{
            flex: 1,
            height: 40,
            background: "var(--panel)",
            border: "1px solid var(--wire)",
            borderRadius: 8,
            padding: "0 14px",
            color: "var(--bone)",
            fontSize: 14,
            outline: "none",
          }}
        />
        <button
          type="submit"
          disabled={isPending || analysis.status === "loading"}
          style={{
            height: 40,
            padding: "0 20px",
            background: "var(--neon)",
            color: "#000",
            border: "none",
            borderRadius: 8,
            fontWeight: 700,
            fontSize: 13,
            cursor: "pointer",
            opacity: isPending || analysis.status === "loading" ? 0.5 : 1,
          }}
        >
          {analysis.status === "loading" ? "…" : "Analyze"}
        </button>
      </form>

      {/* Error */}
      {analysis.status === "error" && (
        <div
          className="glass"
          style={{ padding: 16, color: "var(--danger)", fontSize: 13 }}
        >
          {analysis.message}
        </div>
      )}

      {/* Result */}
      {analysis.status === "done" && p && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Player header */}
          <div className="glass" style={{ padding: "20px 24px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
              <div>
                <div style={{ fontSize: 18, fontWeight: 600, color: "var(--bone)" }}>
                  {p.name}
                </div>
                <div
                  style={{ color: "var(--ash)", fontSize: 13, marginTop: 4 }}
                >
                  {p.team} · {p.position ?? "—"} ·{" "}
                  <span
                    className="label"
                    style={{ color: p.is_active ? "var(--live)" : "var(--smoke)" }}
                  >
                    {p.is_active ? "Active" : "Inactive"}
                  </span>
                </div>
              </div>
              <div className="label">{analysis.data.payload.season_label}</div>
            </div>

            {/* Basic stats */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(5, 1fr)",
                gap: 1,
                marginTop: 20,
                borderRadius: 8,
                overflow: "hidden",
                border: "1px solid var(--wire)",
              }}
            >
              {[
                { label: "PTS", value: fmt(p.basic.pts) },
                { label: "REB", value: fmt(p.basic.reb) },
                { label: "AST", value: fmt(p.basic.ast) },
                { label: "STL", value: fmt(p.basic.stl) },
                { label: "BLK", value: fmt(p.basic.blk) },
              ].map(({ label, value }) => (
                <div
                  key={label}
                  style={{
                    padding: "12px 0",
                    textAlign: "center",
                    background: "var(--graphite)",
                  }}
                >
                  <div
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 18,
                      fontWeight: 600,
                      color: "var(--bone)",
                    }}
                  >
                    {value ?? "—"}
                  </div>
                  <div className="label" style={{ marginTop: 4 }}>
                    {label}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Advanced stats */}
          <div className="glass" style={{ padding: "20px 24px" }}>
            <div className="label" style={{ marginBottom: 12 }}>
              Advanced
            </div>
            <StatRow label="TS%" value={pct(p.advanced.ts_pct)} />
            <StatRow label="eFG%" value={pct(p.advanced.efg_pct)} />
            <StatRow label="Usage" value={pct(p.advanced.usage_pct)} />
            <StatRow label="ORtg" value={fmt(p.advanced.off_rtg)} />
            <StatRow label="DRtg" value={fmt(p.advanced.def_rtg)} />
            <StatRow label="NetRtg" value={fmt(p.advanced.net_rtg)} />
            <StatRow label="AST%" value={pct(p.advanced.ast_pct)} />
            <StatRow label="AST/TOV" value={fmt(p.advanced.ast_to_tov)} />
            <StatRow label="REB%" value={pct(p.advanced.reb_pct)} />
            <StatRow label="PIE" value={pct(p.advanced.pie)} />
            {p.advanced.contested_fg_pct !== null && (
              <StatRow label="Contested FG%" value={pct(p.advanced.contested_fg_pct)} />
            )}
            {p.advanced.deflections_pg !== null && (
              <StatRow label="Deflections/G" value={fmt(p.advanced.deflections_pg)} />
            )}
            {p.advanced.screen_assists_pg !== null && (
              <StatRow label="Screen Ast/G" value={fmt(p.advanced.screen_assists_pg)} />
            )}
          </div>

          {/* AI analysis */}
          <div className="glass" style={{ padding: "20px 24px" }}>
            <div className="label" style={{ marginBottom: 12 }}>
              Analysis
            </div>
            <p
              style={{
                color: "var(--ash)",
                fontSize: 13,
                lineHeight: 1.7,
                whiteSpace: "pre-wrap",
              }}
            >
              {analysis.data.analysis}
            </p>
          </div>

          {/* Sample warnings */}
          {analysis.data.payload.sample_warnings.length > 0 && (
            <div
              className="glass"
              style={{ padding: "12px 16px", borderColor: "var(--warn)" }}
            >
              {analysis.data.payload.sample_warnings.map((w, i) => (
                <div key={i} style={{ color: "var(--warn)", fontSize: 12 }}>
                  {w}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
