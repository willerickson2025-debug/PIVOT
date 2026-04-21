"use client";

import { useState, useEffect } from "react";

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

// ── Types ─────────────────────────────────────────────────────────────────────

interface Game {
  id: number;
  home_team: { abbreviation: string; full_name: string };
  visitor_team: { abbreviation: string; full_name: string };
  status: string;
  home_team_score: number;
  visitor_team_score: number;
}

type ActionType = "adjust" | "timeout" | "defense";

interface ActionConfig {
  id: ActionType;
  label: string;
  endpoint: string;
  desc: string;
}

const ACTIONS: ActionConfig[] = [
  { id: "adjust", label: "ADJUST ROTATION", endpoint: "/coach/adjust", desc: "Lineup and rotation recommendations based on current matchups." },
  { id: "timeout", label: "CALL TIMEOUT", endpoint: "/coach/timeout", desc: "Timeout strategy and messaging for the current game state." },
  { id: "defense", label: "DEFENSIVE SCHEME", endpoint: "/coach/defense", desc: "Defensive adjustments targeting opponent tendencies." },
];

// ── Page ──────────────────────────────────────────────────────────────────────

type ResponseState =
  | { status: "idle" }
  | { status: "loading"; action: ActionType }
  | { status: "done"; action: ActionType; text: string }
  | { status: "error"; message: string };

export default function CoachPage() {
  const [games, setGames] = useState<Game[]>([]);
  const [gamesLoading, setGamesLoading] = useState(true);
  const [selectedGameId, setSelectedGameId] = useState<number | null>(null);
  const [resp, setResp] = useState<ResponseState>({ status: "idle" });

  // Fetch today's games
  useEffect(() => {
    const today = new Date().toISOString().split("T")[0];
    fetch(`${BASE}/nba/games?date=${today}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const list: Game[] = d?.data ?? (Array.isArray(d) ? d : []);
        setGames(list);
        if (list.length > 0) setSelectedGameId(list[0].id);
      })
      .catch(() => {})
      .finally(() => setGamesLoading(false));
  }, []);

  async function runAction(action: ActionConfig) {
    if (selectedGameId == null) return;
    setResp({ status: "loading", action: action.id });
    try {
      const res = await fetch(`${BASE}${action.endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ game_id: selectedGameId }),
      });
      if (!res.ok) {
        const t = await res.text();
        setResp({ status: "error", message: `${res.status}: ${t}` });
        return;
      }
      const data = await res.json();
      // Response may be { analysis: "..." } or { advice: "..." } or plain text
      const text = data.analysis ?? data.advice ?? data.result ?? data.text ?? JSON.stringify(data, null, 2);
      setResp({ status: "done", action: action.id, text: String(text) });
    } catch (e) {
      setResp({ status: "error", message: e instanceof Error ? e.message : "Request failed" });
    }
  }

  const selectedGame = games.find((g) => g.id === selectedGameId) ?? null;
  const isLoading = resp.status === "loading";

  return (
    <div style={{ background: "#000", minHeight: "100vh", color: "#fff", padding: "32px 24px 80px", boxSizing: "border-box" }}>
      <div style={{ maxWidth: 760, margin: "0 auto" }}>

        {/* Header */}
        <div style={{ marginBottom: 32 }}>
          <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.4)", letterSpacing: "0.14em", marginBottom: 8 }}>PIVOT · COACH MODE</div>
          <h1 style={{ ...HB, fontSize: "clamp(24px, 4vw, 40px)", margin: "0 0 8px", textTransform: "uppercase", letterSpacing: "-0.01em" }}>
            IN-GAME ADJUSTMENTS
          </h1>
          <p style={{ ...HN, fontSize: 14, color: "rgba(255,255,255,0.4)", margin: 0, lineHeight: 1.6 }}>
            Select a game and an action to get real-time coaching analysis.
          </p>
        </div>

        {/* Game selector */}
        <div style={{ marginBottom: 28 }}>
          <div style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", marginBottom: 10 }}>SELECT GAME</div>
          {gamesLoading ? (
            <div style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.3)" }}>Loading games…</div>
          ) : games.length === 0 ? (
            <div
              style={{
                background: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.07)",
                borderRadius: 10,
                padding: "24px 20px",
                textAlign: "center",
              }}
            >
              <div style={{ ...HB, fontSize: 12, letterSpacing: "0.1em", color: "rgba(255,255,255,0.25)" }}>NO GAMES TODAY</div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {games.map((g) => {
                const active = g.id === selectedGameId;
                return (
                  <button
                    key={g.id}
                    onClick={() => { setSelectedGameId(g.id); setResp({ status: "idle" }); }}
                    style={{
                      ...HB,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      padding: "12px 18px",
                      background: active ? "rgba(57,255,20,0.06)" : "rgba(255,255,255,0.02)",
                      border: `1px solid ${active ? "rgba(57,255,20,0.25)" : "rgba(255,255,255,0.06)"}`,
                      borderRadius: 10,
                      color: "#fff",
                      cursor: "pointer",
                      textAlign: "left",
                      transition: "all 120ms",
                    }}
                  >
                    <span style={{ fontSize: 14 }}>
                      {g.visitor_team.abbreviation} <span style={{ color: "rgba(255,255,255,0.35)" }}>@</span> {g.home_team.abbreviation}
                    </span>
                    <span style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.45)" }}>
                      {g.visitor_team_score > 0 || g.home_team_score > 0
                        ? `${g.visitor_team_score}–${g.home_team_score}`
                        : g.status}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Action buttons */}
        {selectedGame && (
          <div style={{ marginBottom: 28 }}>
            <div style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", marginBottom: 10 }}>ACTION</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {ACTIONS.map((action) => {
                const active = resp.status === "done" && resp.action === action.id;
                const loading = resp.status === "loading" && resp.action === action.id;
                return (
                  <button
                    key={action.id}
                    onClick={() => runAction(action)}
                    disabled={isLoading}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 16,
                      padding: "14px 18px",
                      background: active ? "rgba(57,255,20,0.06)" : "rgba(255,255,255,0.02)",
                      border: `1px solid ${active ? "rgba(57,255,20,0.25)" : "rgba(255,255,255,0.06)"}`,
                      borderRadius: 10,
                      color: "#fff",
                      cursor: isLoading ? "not-allowed" : "pointer",
                      opacity: isLoading && !loading ? 0.4 : 1,
                      textAlign: "left",
                      transition: "all 120ms",
                    }}
                  >
                    <div
                      style={{
                        width: 6,
                        height: 6,
                        borderRadius: "50%",
                        background: loading ? SIG : active ? SIG : "rgba(255,255,255,0.2)",
                        flexShrink: 0,
                        marginTop: 5,
                        boxShadow: loading ? `0 0 6px ${SIG}` : "none",
                      }}
                    />
                    <div>
                      <div style={{ ...HB, fontSize: 11, letterSpacing: "0.1em", color: SIG, marginBottom: 4 }}>{action.label}</div>
                      <div style={{ ...HN, fontSize: 12, color: "rgba(255,255,255,0.4)", lineHeight: 1.5 }}>{action.desc}</div>
                    </div>
                    {loading && (
                      <span style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.3)", marginLeft: "auto" }}>…</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Error */}
        {resp.status === "error" && (
          <div style={{ background: "rgba(255,107,74,0.08)", border: "1px solid rgba(255,107,74,0.2)", borderRadius: 10, padding: "16px 20px", marginBottom: 20 }}>
            <div style={{ ...HB, fontSize: 11, letterSpacing: "0.1em", color: "#FF6B4A", marginBottom: 6 }}>ERROR</div>
            <div style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.4)" }}>{resp.message}</div>
          </div>
        )}

        {/* Response panel */}
        {resp.status === "done" && (
          <div
            style={{
              background: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.07)",
              borderRadius: 12,
              padding: "24px 24px",
            }}
          >
            <div style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: SIG, marginBottom: 16 }}>
              {ACTIONS.find((a) => a.id === resp.action)?.label ?? resp.action.toUpperCase()}
            </div>
            <p style={{ ...HN, fontSize: 14, color: "rgba(255,255,255,0.75)", lineHeight: 1.75, whiteSpace: "pre-wrap", margin: 0 }}>
              {resp.text}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
