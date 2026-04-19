"use client";

import { useState, useTransition } from "react";
import { ComparePlayersResponse } from "../../_lib/api";

type CompareState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "done"; data: ComparePlayersResponse }
  | { status: "error"; message: string };

function PlayerColumn({
  label,
  data,
}: {
  label: "A" | "B";
  data: Record<string, unknown>;
}) {
  const name = (data.name as string) ?? "—";
  const team = (data.team as string) ?? "—";
  const position = (data.position as string | null) ?? "—";
  const basic = (data.basic as Record<string, number | null>) ?? {};
  const advanced = (data.advanced as Record<string, number | null>) ?? {};

  function fmt(v: number | null | undefined, decimals = 1) {
    if (v === null || v === undefined) return "—";
    return v.toFixed(decimals);
  }
  function pct(v: number | null | undefined) {
    if (v === null || v === undefined) return "—";
    return (v * 100).toFixed(1) + "%";
  }

  return (
    <div className="glass" style={{ padding: "20px 24px", flex: 1, minWidth: 0 }}>
      <div className="label" style={{ color: "var(--neon)", marginBottom: 8 }}>
        Player {label}
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, color: "var(--bone)" }}>{name}</div>
      <div style={{ color: "var(--ash)", fontSize: 13, marginTop: 4 }}>
        {team} · {position}
      </div>

      {/* Basic */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 1,
          marginTop: 16,
          borderRadius: 8,
          overflow: "hidden",
          border: "1px solid var(--wire)",
        }}
      >
        {[
          { label: "PTS", value: fmt(basic.pts) },
          { label: "REB", value: fmt(basic.reb) },
          { label: "AST", value: fmt(basic.ast) },
        ].map(({ label: l, value: v }) => (
          <div
            key={l}
            style={{ padding: "10px 0", textAlign: "center", background: "var(--graphite)" }}
          >
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 16,
                fontWeight: 600,
                color: "var(--bone)",
              }}
            >
              {v}
            </div>
            <div className="label" style={{ marginTop: 3 }}>
              {l}
            </div>
          </div>
        ))}
      </div>

      {/* Advanced snippet */}
      <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 0 }}>
        {[
          { label: "TS%", value: pct(advanced.ts_pct) },
          { label: "Usage", value: pct(advanced.usage_pct) },
          { label: "ORtg", value: fmt(advanced.off_rtg) },
          { label: "DRtg", value: fmt(advanced.def_rtg) },
          { label: "PIE", value: pct(advanced.pie) },
        ].map(({ label: l, value: v }) => (
          <div
            key={l}
            style={{
              display: "flex",
              justifyContent: "space-between",
              padding: "5px 0",
              borderBottom: "1px solid var(--wire)",
            }}
          >
            <span className="label">{l}</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--bone)" }}>
              {v}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function CompareForm() {
  const [playerA, setPlayerA] = useState("");
  const [playerB, setPlayerB] = useState("");
  const [state, setState] = useState<CompareState>({ status: "idle" });
  const [isPending, startTransition] = useTransition();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const a = playerA.trim();
    const b = playerB.trim();
    if (!a || !b) return;

    setState({ status: "loading" });

    startTransition(async () => {
      try {
        const params = new URLSearchParams({ player_a: a, player_b: b, season: "2025" });
        const res = await fetch(`/api/v1/analysis/compare?${params}`);
        if (!res.ok) {
          const body = await res.text();
          setState({ status: "error", message: `${res.status}: ${body}` });
          return;
        }
        const data: ComparePlayersResponse = await res.json();
        setState({ status: "done", data });
      } catch (e) {
        setState({
          status: "error",
          message: e instanceof Error ? e.message : "Request failed",
        });
      }
    });
  }

  const isLoading = isPending || state.status === "loading";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Inputs */}
      <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          type="text"
          value={playerA}
          onChange={(e) => setPlayerA(e.target.value)}
          placeholder="Player A…"
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
        <span style={{ color: "var(--smoke)", fontSize: 13 }}>vs</span>
        <input
          type="text"
          value={playerB}
          onChange={(e) => setPlayerB(e.target.value)}
          placeholder="Player B…"
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
          disabled={isLoading}
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
            opacity: isLoading ? 0.5 : 1,
          }}
        >
          {isLoading ? "…" : "Compare"}
        </button>
      </form>

      {/* Error */}
      {state.status === "error" && (
        <div className="glass" style={{ padding: 16, color: "var(--danger)", fontSize: 13 }}>
          {state.message}
        </div>
      )}

      {/* Results */}
      {state.status === "done" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Columns */}
          <div style={{ display: "flex", gap: 12, alignItems: "start" }}>
            <PlayerColumn label="A" data={state.data.player_a} />
            <PlayerColumn label="B" data={state.data.player_b} />
          </div>

          {/* Verdict */}
          <div className="glass" style={{ padding: "20px 24px" }}>
            <div className="label" style={{ marginBottom: 12 }}>
              Verdict
            </div>
            <div style={{ color: "var(--bone)", fontSize: 14, marginBottom: 12 }}>
              {state.data.structured.better_for_context}
            </div>
            <p style={{ color: "var(--ash)", fontSize: 13, lineHeight: 1.6 }}>
              {state.data.structured.reasoning}
            </p>
          </div>

          {/* Key differences */}
          {state.data.structured.key_differences.length > 0 && (
            <div className="glass" style={{ padding: "20px 24px" }}>
              <div className="label" style={{ marginBottom: 12 }}>
                Key Differences
              </div>
              <ul style={{ display: "flex", flexDirection: "column", gap: 8, paddingLeft: 0 }}>
                {state.data.structured.key_differences.map((d, i) => (
                  <li
                    key={i}
                    style={{
                      color: "var(--ash)",
                      fontSize: 13,
                      lineHeight: 1.5,
                      listStyle: "none",
                      paddingLeft: 14,
                      position: "relative",
                    }}
                  >
                    <span
                      style={{
                        position: "absolute",
                        left: 0,
                        color: "var(--neon)",
                      }}
                    >
                      ·
                    </span>
                    {d}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Full analysis */}
          <div className="glass" style={{ padding: "20px 24px" }}>
            <div className="label" style={{ marginBottom: 12 }}>
              Full Analysis
            </div>
            <p
              style={{
                color: "var(--ash)",
                fontSize: 13,
                lineHeight: 1.7,
                whiteSpace: "pre-wrap",
              }}
            >
              {state.data.analysis}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
