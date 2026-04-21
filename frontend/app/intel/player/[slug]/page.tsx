"use client";

import { useState, useEffect } from "react";
import { useRouter, useParams } from "next/navigation";

// ── Brand tokens ──────────────────────────────────────────────────────────────

const SIG = "#39FF14";
const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://pivot-app-production-1eb4.up.railway.app/api/v1";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
  fontWeight: 700,
};
const HN: React.CSSProperties = {
  fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
  fontWeight: 400,
};
const MONO: React.CSSProperties = {
  fontFamily: '"JetBrains Mono", "Fira Code", ui-monospace, monospace',
  fontWeight: 400,
};
const MONO_B: React.CSSProperties = {
  fontFamily: '"JetBrains Mono", "Fira Code", ui-monospace, monospace',
  fontWeight: 700,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function slugToName(slug: string): string {
  return slug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function fmt(v: number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return v.toFixed(decimals);
}

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v * 100).toFixed(1) + "%";
}

// ── StatGrid ─────────────────────────────────────────────────────────────────

function StatGrid({ items }: { items: { label: string; value: string }[] }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${Math.min(items.length, 5)}, 1fr)`,
        gap: 1,
        borderRadius: 8,
        overflow: "hidden",
        border: "1px solid rgba(255,255,255,0.08)",
      }}
    >
      {items.map(({ label, value }) => (
        <div
          key={label}
          style={{
            padding: "12px 0",
            textAlign: "center",
            background: "rgba(255,255,255,0.04)",
          }}
        >
          <div style={{ ...MONO_B, fontSize: 18, color: "#fff" }}>{value}</div>
          <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)", letterSpacing: "0.1em", marginTop: 4 }}>
            {label}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── StatRow ───────────────────────────────────────────────────────────────────

function StatRow({ label, value }: { label: string; value: string }) {
  if (value === "—") return null;
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "baseline",
        padding: "7px 0",
        borderBottom: "0.5px solid rgba(255,255,255,0.06)",
      }}
    >
      <span style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.5)", letterSpacing: "0.06em" }}>{label}</span>
      <span style={{ ...MONO_B, fontSize: 13, color: "#fff" }}>{value}</span>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

interface PlayerPayload {
  name: string;
  team: string;
  position: string | null;
  is_active: boolean;
  basic: Record<string, number | null>;
  advanced: Record<string, number | null>;
}

interface AnalysisResponse {
  analysis: string;
  payload: {
    player: PlayerPayload;
    season_label: string;
    sample_warnings: string[];
  };
}

type PageState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "done"; data: AnalysisResponse };

export default function PlayerDetailPage() {
  const router = useRouter();
  const params = useParams();
  const slug = typeof params.slug === "string" ? params.slug : Array.isArray(params.slug) ? params.slug[0] : "";
  const playerName = slugToName(slug);

  const [state, setState] = useState<PageState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetch(`${BASE}/analysis/player?player_name=${encodeURIComponent(playerName)}&season=2025`)
      .then((r) => {
        if (!r.ok) return r.text().then((t) => { throw new Error(`${r.status}: ${t}`); });
        return r.json();
      })
      .then((d: AnalysisResponse) => {
        if (!cancelled) setState({ status: "done", data: d });
      })
      .catch((e: unknown) => {
        if (!cancelled) setState({ status: "error", message: e instanceof Error ? e.message : "Request failed" });
      });
    return () => { cancelled = true; };
  }, [playerName]);

  const p = state.status === "done" ? state.data.payload.player : null;
  const isUnavailable = p && p.basic.pts === null;

  return (
    <div style={{ background: "#000", minHeight: "100vh", color: "#fff", paddingBottom: 64 }}>

      {/* Back */}
      <div style={{ padding: "20px 24px 0" }}>
        <button
          onClick={() => router.push("/intel")}
          style={{
            ...HB,
            background: "none",
            border: "none",
            color: "rgba(255,255,255,0.4)",
            cursor: "pointer",
            fontSize: 11,
            letterSpacing: "0.12em",
            padding: 0,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          ← INTEL
        </button>
      </div>

      <div style={{ maxWidth: 760, margin: "0 auto", padding: "32px 24px" }}>

        {/* Loading */}
        {state.status === "loading" && (
          <div style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.3)", letterSpacing: "0.1em", textAlign: "center", paddingTop: 80 }}>
            LOADING...
          </div>
        )}

        {/* Error */}
        {state.status === "error" && (
          <div style={{ background: "rgba(255,107,74,0.08)", border: "1px solid rgba(255,107,74,0.2)", borderRadius: 10, padding: "20px 24px" }}>
            <div style={{ ...HB, fontSize: 12, letterSpacing: "0.1em", color: "#FF6B4A", marginBottom: 8 }}>ERROR</div>
            <div style={{ ...MONO, fontSize: 13, color: "rgba(255,255,255,0.5)" }}>{state.message}</div>
          </div>
        )}

        {/* Done */}
        {state.status === "done" && p && (
          <>
            {/* Header */}
            <div style={{ marginBottom: 32 }}>
              <div style={{ ...HB, fontSize: "clamp(28px, 5vw, 48px)", color: "#fff", marginBottom: 6, letterSpacing: "-0.01em" }}>
                {p.name}
              </div>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ ...MONO, fontSize: 13, color: "rgba(255,255,255,0.5)" }}>
                  {p.team} · {p.position ?? "—"}
                </span>
                <span
                  style={{
                    ...HB,
                    fontSize: 9,
                    letterSpacing: "0.14em",
                    color: p.is_active ? SIG : "rgba(255,255,255,0.3)",
                    background: p.is_active ? "rgba(57,255,20,0.08)" : "rgba(255,255,255,0.04)",
                    border: `1px solid ${p.is_active ? "rgba(57,255,20,0.2)" : "rgba(255,255,255,0.08)"}`,
                    borderRadius: 4,
                    padding: "3px 8px",
                  }}
                >
                  {p.is_active ? "ACTIVE" : "INACTIVE"}
                </span>
                <span style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)" }}>
                  {state.data.payload.season_label}
                </span>
              </div>
            </div>

            {/* Stats unavailable */}
            {isUnavailable && (
              <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 12, padding: "40px 24px", textAlign: "center", marginBottom: 24 }}>
                <div style={{ ...HB, fontSize: 12, letterSpacing: "0.1em", color: "rgba(255,255,255,0.3)" }}>STATS UNAVAILABLE</div>
                <div style={{ ...HN, fontSize: 13, color: "rgba(255,255,255,0.2)", marginTop: 8 }}>
                  This player has not recorded any games this season.
                </div>
              </div>
            )}

            {/* Basic stats */}
            {!isUnavailable && (
              <div style={{ marginBottom: 24 }}>
                <StatGrid items={[
                  { label: "PTS", value: fmt(p.basic.pts) },
                  { label: "REB", value: fmt(p.basic.reb) },
                  { label: "AST", value: fmt(p.basic.ast) },
                  { label: "STL", value: fmt(p.basic.stl) },
                  { label: "BLK", value: fmt(p.basic.blk) },
                ]} />
              </div>
            )}

            {/* Advanced stats */}
            {!isUnavailable && (
              <div
                style={{
                  background: "rgba(255,255,255,0.02)",
                  border: "1px solid rgba(255,255,255,0.06)",
                  borderRadius: 12,
                  padding: "20px 24px",
                  marginBottom: 24,
                }}
              >
                <div style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: "rgba(255,255,255,0.35)", marginBottom: 14 }}>
                  ADVANCED
                </div>
                <StatRow label="TS%" value={pct(p.advanced.ts_pct)} />
                <StatRow label="eFG%" value={pct(p.advanced.efg_pct)} />
                <StatRow label="Usage%" value={pct(p.advanced.usage_pct)} />
                <StatRow label="PIE" value={pct(p.advanced.pie)} />
                <StatRow label="ORtg" value={fmt(p.advanced.off_rtg)} />
                <StatRow label="DRtg" value={fmt(p.advanced.def_rtg)} />
                <StatRow label="NetRtg" value={fmt(p.advanced.net_rtg)} />
                <StatRow label="AST%" value={pct(p.advanced.ast_pct)} />
                <StatRow label="AST/TOV" value={fmt(p.advanced.ast_to_tov)} />
                <StatRow label="REB%" value={pct(p.advanced.reb_pct)} />
                {p.advanced.contested_fg_pct != null && <StatRow label="Contested FG%" value={pct(p.advanced.contested_fg_pct)} />}
                {p.advanced.deflections_pg != null && <StatRow label="Deflections/G" value={fmt(p.advanced.deflections_pg)} />}
                {p.advanced.screen_assists_pg != null && <StatRow label="Screen Ast/G" value={fmt(p.advanced.screen_assists_pg)} />}
              </div>
            )}

            {/* Sample warnings */}
            {state.data.payload.sample_warnings.length > 0 && (
              <div style={{ background: "rgba(255,107,74,0.06)", border: "1px solid rgba(255,107,74,0.15)", borderRadius: 8, padding: "12px 16px", marginBottom: 24 }}>
                {state.data.payload.sample_warnings.map((w, i) => (
                  <div key={i} style={{ ...MONO, fontSize: 12, color: "#FF6B4A" }}>{w}</div>
                ))}
              </div>
            )}

            {/* AI analysis */}
            <div
              style={{
                background: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 12,
                padding: "20px 24px",
              }}
            >
              <div style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: "rgba(255,255,255,0.35)", marginBottom: 14 }}>
                ANALYSIS
              </div>
              <p style={{ ...HN, fontSize: 14, color: "rgba(255,255,255,0.7)", lineHeight: 1.75, whiteSpace: "pre-wrap", margin: 0 }}>
                {state.data.analysis}
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
