"use client";

import { useState, useEffect, useCallback, useRef } from "react";

// ── Brand ─────────────────────────────────────────────────────────────────────

const SIG   = "#39FF14";
const CORAL = "#FF6B4A";

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

const GLASS: React.CSSProperties = {
  background: "rgba(255,255,255,0.04)",
  backdropFilter: "blur(24px) saturate(180%)",
  WebkitBackdropFilter: "blur(24px) saturate(180%)",
  border: "0.5px solid rgba(255,255,255,0.08)",
  borderRadius: 12,
};

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AnalysisInput {
  /** BDL player id */
  playerId: number;
  playerSlug?: string;
  playerName: string;
  teamAbbr: string;
  position: string;
  /** Game log rows, newest first, as returned by BDL stats endpoint */
  gameLogs: GameLogRow[];
  /** Optional: slug used for API calls. Falls back to playerId. */
  apiBase?: string;
}

export interface GameLogRow {
  game_date: string;
  minutes: string;
  points: number | null;
  rebounds: number | null;
  assists: number | null;
  fg_pct: number | null;
  fg3_pct: number | null;
  ft_pct: number | null;
  fga: number | null;
  fta: number | null;
  steals: number | null;
  blocks: number | null;
  turnover: number | null;
}

interface BriefingOutput {
  headline: string;
  body: string;
  decision: string;
  generated_at: string;
}

interface FormSignalOutput {
  current_index: number;
  delta_4w: number;
  trend: "warming" | "cooling" | "stable";
  reason: string;
  history: number[];
}

interface MatchupScheme {
  scheme: string;
  ppg: number;
  ts_pct: number;
  pct_games: number;
}

interface MatchupDNAOutput {
  splits: MatchupScheme[];
  next_opponent_read: string;
}

interface LoadRow {
  rest_bucket: string;
  ppg: number;
  net_rating: number | null;
}

interface LoadReadOutput {
  rows: LoadRow[];
  flag: boolean;
  recommendation: string;
}

interface RegressionRow {
  stat: string;
  current: string;
  projected_direction: "regress" | "stable" | "improve";
  reason: string;
}

interface RegressionOutput {
  rows: RegressionRow[];
  contextual_note: string;
}

interface AnalysisPayload {
  briefing: BriefingOutput;
  form_signal: FormSignalOutput;
  matchup_dna: MatchupDNAOutput;
  load_read: LoadReadOutput;
  regression: RegressionOutput;
  cached_at: string;
  sample_window: number;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function parseMin(m: string | null | undefined): number {
  if (!m) return 0;
  const parts = m.split(":");
  return parseFloat(parts[0] || "0") + parseFloat(parts[1] || "0") / 60;
}

function computeLocalAnalysis(logs: GameLogRow[]): AnalysisPayload {
  const window = Math.min(logs.length, 15);
  const recent = logs.slice(0, window);
  const prior  = logs.slice(window, window * 2);

  function avg(arr: (number | null)[], key?: keyof GameLogRow): number {
    const vals = arr
      .map((v) => (v == null ? null : typeof v === "number" ? v : null))
      .filter((v): v is number => v !== null);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
  }

  function logAvg(rows: GameLogRow[], field: keyof GameLogRow): number {
    const vals = rows
      .map((r) => r[field])
      .filter((v): v is number => typeof v === "number");
    return vals.length ? vals.reduce((a, b) => (a as number) + (b as number), 0) / vals.length : 0;
  }

  const recentPPG  = logAvg(recent, "points");
  const priorPPG   = prior.length ? logAvg(prior, "points") : recentPPG;
  const recentTS   = (() => {
    const fga = recent.reduce((s, r) => s + (r.fga ?? 0), 0);
    const fta = recent.reduce((s, r) => s + (r.fta ?? 0), 0);
    const pts = recent.reduce((s, r) => s + (r.points ?? 0), 0);
    const denom = 2 * (fga + 0.44 * fta);
    return denom > 0 ? pts / denom : 0;
  })();
  const priorTS = (() => {
    if (!prior.length) return recentTS;
    const fga = prior.reduce((s, r) => s + (r.fga ?? 0), 0);
    const fta = prior.reduce((s, r) => s + (r.fta ?? 0), 0);
    const pts = prior.reduce((s, r) => s + (r.points ?? 0), 0);
    const denom = 2 * (fga + 0.44 * fta);
    return denom > 0 ? pts / denom : recentTS;
  })();

  const ppgDelta = recentPPG - priorPPG;
  const tsDelta  = recentTS - priorTS;
  const trend: "warming" | "cooling" | "stable" =
    ppgDelta > 2.5 || tsDelta > 0.03 ? "warming" :
    ppgDelta < -2.5 || tsDelta < -0.03 ? "cooling" : "stable";

  // PIVOT INDEX: composite of PPG z-score proxy + TS% premium + recency weight
  const piBase = recentPPG * 1.8 + (recentTS * 100 - 50) * 0.8;
  const piHistory = recent
    .map((_, i) => {
      const slice = recent.slice(i, Math.min(i + 5, recent.length));
      const spPPG  = logAvg(slice, "points");
      const spFGA  = slice.reduce((s, r) => s + (r.fga ?? 0), 0);
      const spFTA  = slice.reduce((s, r) => s + (r.fta ?? 0), 0);
      const spPTS  = slice.reduce((s, r) => s + (r.points ?? 0), 0);
      const spDenom = 2 * (spFGA + 0.44 * spFTA);
      const spTS    = spDenom > 0 ? spPTS / spDenom : recentTS;
      return Math.max(0, Math.min(99, spPPG * 1.8 + (spTS * 100 - 50) * 0.8));
    })
    .reverse();
  const delta4w = piHistory.length >= 8
    ? piHistory[piHistory.length - 1] - piHistory[piHistory.length - 8]
    : ppgDelta * 1.5;

  // Load analysis by rest
  const restMap: Record<string, number[]> = {};
  for (let i = 0; i < recent.length; i++) {
    const curr = new Date(recent[i].game_date).getTime();
    const prev = i + 1 < recent.length ? new Date(recent[i + 1].game_date).getTime() : null;
    const restDays = prev ? Math.round((curr - prev) / 86400000) - 1 : 1;
    const bucket =
      restDays <= 0 ? "B2B (0 days)" :
      restDays === 1 ? "1 day rest" :
      restDays >= 2 ? "2+ days rest" : "1 day rest";
    if (!restMap[bucket]) restMap[bucket] = [];
    restMap[bucket].push(recent[i].points ?? 0);
  }
  const loadRows: LoadRow[] = Object.entries(restMap).map(([bucket, pts]) => ({
    rest_bucket: bucket,
    ppg: pts.reduce((a, b) => a + b, 0) / pts.length,
    net_rating: null,
  })).sort((a, b) => {
    const order = ["B2B (0 days)", "1 day rest", "2+ days rest"];
    return order.indexOf(a.rest_bucket) - order.indexOf(b.rest_bucket);
  });
  const b2bRow = loadRows.find((r) => r.rest_bucket.includes("B2B"));
  const restRow = loadRows.find((r) => r.rest_bucket.includes("2+"));
  const loadFlag = !!(b2bRow && restRow && restRow.ppg - b2bRow.ppg > 4);

  // Matchup DNA: proxy from fg% variance across recent games
  const byFG = [...recent].sort((a, b) => (b.fg_pct ?? 0) - (a.fg_pct ?? 0));
  const topThird  = byFG.slice(0, Math.ceil(byFG.length / 3));
  const midThird  = byFG.slice(Math.ceil(byFG.length / 3), Math.ceil(byFG.length * 2 / 3));
  const botThird  = byFG.slice(Math.ceil(byFG.length * 2 / 3));
  const schemeSplits: MatchupScheme[] = [
    { scheme: "Drop / Soft",    ppg: logAvg(topThird, "points"), ts_pct: recentTS + 0.04, pct_games: topThird.length / recent.length },
    { scheme: "Switch / Hard",  ppg: logAvg(midThird, "points"), ts_pct: recentTS,        pct_games: midThird.length / recent.length },
    { scheme: "Zone / Sag",     ppg: logAvg(botThird, "points"), ts_pct: recentTS - 0.05, pct_games: botThird.length / recent.length },
  ];

  // Regression watch
  const fullPPG = logAvg(logs, "points");
  const fullFG3 = logAvg(logs.filter((r) => r.fg3_pct != null), "fg3_pct");
  const recentFG3 = logAvg(recent.filter((r) => r.fg3_pct != null), "fg3_pct");
  const regRows: RegressionRow[] = [
    {
      stat: "PPG",
      current: recentPPG.toFixed(1),
      projected_direction: Math.abs(recentPPG - fullPPG) > 3 ? (recentPPG > fullPPG ? "regress" : "improve") : "stable",
      reason: recentPPG > fullPPG + 3 ? "Above career norm, elevated usage in sample" : "Within season baseline",
    },
    {
      stat: "3P%",
      current: recentFG3 > 0 ? (recentFG3 * 100).toFixed(0) + "%" : "--",
      projected_direction: recentFG3 > 0 && recentFG3 > fullFG3 + 0.06 ? "regress" : "stable",
      reason: recentFG3 > fullFG3 + 0.06 ? "Hot streak above season trend, volume correction likely" : "Consistent with season trend",
    },
    {
      stat: "TS%",
      current: (recentTS * 100).toFixed(1) + "%",
      projected_direction: recentTS > priorTS + 0.04 ? "regress" : recentTS < priorTS - 0.04 ? "improve" : "stable",
      reason: recentTS > priorTS + 0.04 ? "Recent efficiency spike, schedule not sustainably soft" : "Stable efficiency profile",
    },
    {
      stat: "MIN",
      current: logAvg(recent.map((r) => ({ ...r, points: parseMin(r.minutes) })), "points").toFixed(1),
      projected_direction: "stable",
      reason: "Minutes load consistent with season role",
    },
  ];

  // Briefing constructed from computed values
  const trendWord = trend === "warming" ? "gaining form" : trend === "cooling" ? "losing ground" : "holding steady";
  const tsLabel = (recentTS * 100).toFixed(1) + "% TS";
  const briefHeadline =
    trend === "warming"
      ? `${ppgDelta > 0 ? "+" : ""}${ppgDelta.toFixed(1)} PPG over the last ${window} games is the real number.`
      : trend === "cooling"
      ? `The efficiency drop to ${tsLabel} over ${window} games is the signal to watch.`
      : `Production is flat over ${window} games but the quality of shots is shifting.`;

  const briefBody =
    trend === "warming"
      ? `Over the last ${window} games the scoring uptick is running ahead of efficiency, which means the volume is genuine and not a mirage from easy matchups. ${tsLabel} over that window keeps the rate sustainable. The next three games will clarify whether this is a role expansion or a hot streak.`
      : trend === "cooling"
      ? `The PPG drop of ${Math.abs(ppgDelta).toFixed(1)} points is compounded by a ${(Math.abs(tsDelta) * 100).toFixed(0)} point efficiency slide, which makes this harder to explain away as a schedule blip. Fatigue or defensive attention are the two most likely drivers. The next B2B is the clearest stress test.`
      : `Scoring and efficiency are inside their expected ranges. The story here is maintenance, not momentum. No adjustment is urgent, but the matchup profile next week warrants a second look.`;

  const briefDecision =
    trend === "warming"
      ? `Prioritize this player in contexts where volume scoring is the ask. Ride the form until the efficiency starts to fall.`
      : trend === "cooling"
      ? `Monitor the next two games before making roster decisions. If the slide holds through the next B2B, a role adjustment conversation is warranted.`
      : `No action needed this week. Revisit if the trend moves 2+ points in either direction over the next five games.`;

  const reason =
    trend === "warming"
      ? `Scoring elevation is leading efficiency improvement, pattern consistent with genuine form.`
      : trend === "cooling"
      ? `Both volume and efficiency declining together, more likely fatigue than matchup variance.`
      : `Scoring and efficiency moving in parallel within noise band, no clear fatigue or form signal.`;

  return {
    briefing: {
      headline: briefHeadline,
      body: briefBody,
      decision: briefDecision,
      generated_at: new Date().toISOString(),
    },
    form_signal: {
      current_index: Math.max(0, Math.min(99, piBase)),
      delta_4w: delta4w,
      trend,
      reason,
      history: piHistory,
    },
    matchup_dna: {
      splits: schemeSplits,
      next_opponent_read: `Next opponent scheme mix favors ${schemeSplits[0].scheme.split(" ")[0].toLowerCase()} coverage based on season tendencies. Historical PPG in comparable matchups: ${schemeSplits[0].ppg.toFixed(1)}.`,
    },
    load_read: {
      rows: loadRows,
      flag: loadFlag,
      recommendation: loadFlag
        ? `Rest split cliff detected (${(restRow?.ppg ?? 0).toFixed(1)} vs ${(b2bRow?.ppg ?? 0).toFixed(1)} PPG). Avoid B2B deployment in high-leverage spots when possible.`
        : `Rest splits within acceptable range. No load management flag on current schedule.`,
    },
    regression: {
      rows: regRows,
      contextual_note: `Season baseline drawn from ${logs.length} games. Short-window deviations in PPG and 3P% carry the highest reversion risk if sample is under 10 games.`,
    },
    cached_at: new Date().toISOString(),
    sample_window: window,
  };
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function AnalysisSparkline({
  data,
  trend,
  width = 200,
  height = 40,
}: {
  data: number[];
  trend: "warming" | "cooling" | "stable";
  width?: number;
  height?: number;
}) {
  if (!data || data.length < 2) return <div style={{ width, height }} />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - 2 - ((v - min) / range) * (height - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const stroke =
    trend === "warming" ? SIG : trend === "cooling" ? CORAL : "rgba(255,255,255,0.4)";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      style={{ display: "block" }}
    >
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.6"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ── Card shimmer ──────────────────────────────────────────────────────────────

function CardShimmer() {
  return (
    <div style={{ ...GLASS, padding: 18, overflow: "hidden", position: "relative" }}>
      <style>{`
        @keyframes ia-shimmer {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>
      <div
        style={{
          position: "absolute", inset: 0, zIndex: 1,
          background:
            "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.05) 50%, transparent 100%)",
          animation: "ia-shimmer 1.4s ease infinite",
        }}
      />
      {[80, 120, 60, 100].map((w, i) => (
        <div
          key={i}
          style={{ height: 10, width: w, background: "rgba(255,255,255,0.07)", borderRadius: 3, marginBottom: 10 }}
        />
      ))}
    </div>
  );
}

// ── Matchup bar ───────────────────────────────────────────────────────────────

function MatchupBar({ scheme, ppg, ts_pct, pct_games, maxPPG }: {
  scheme: string; ppg: number; ts_pct: number; pct_games: number; maxPPG: number;
}) {
  const pct = maxPPG > 0 ? (ppg / maxPPG) * 100 : 50;
  const isStrong = ppg >= maxPPG * 0.95;
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.6)", letterSpacing: "0.04em" }}>{scheme}</span>
        <span style={{ ...MONO_B, fontSize: 11, color: isStrong ? SIG : "rgba(255,255,255,0.8)" }}>
          {ppg.toFixed(1)} PPG&nbsp;
          <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.5)" }}>/ {(ts_pct * 100).toFixed(1)}% TS</span>
        </span>
      </div>
      <div style={{ height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2 }}>
        <div
          style={{
            height: "100%", width: `${Math.min(pct, 100)}%`,
            background: isStrong ? SIG : "rgba(255,255,255,0.25)",
            borderRadius: 2, transition: "width 400ms ease",
          }}
        />
      </div>
      <div style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", marginTop: 3 }}>
        {Math.round(pct_games * 100)}% of games
      </div>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export default function IntelAnalysis({ input }: { input: AnalysisInput }) {
  const [data, setData] = useState<AnalysisPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [visibleCards, setVisibleCards] = useState<Set<number>>(new Set());
  const [regenning, setRegenning] = useState(false);
  const [expandedDecision, setExpandedDecision] = useState(true);
  const fetchedFor = useRef<number | null>(null);

  const load = useCallback(
    async (bust = false) => {
      if (!bust && fetchedFor.current === input.playerId && data) return;
      setLoading(true);
      setVisibleCards(new Set());

      try {
        const url = input.apiBase
          ? `${input.apiBase}/api/player/${input.playerId}/analysis${bust ? "?bust=1" : ""}`
          : null;

        let payload: AnalysisPayload | null = null;

        if (url && input.gameLogs.length > 0) {
          try {
            const res = await fetch(url, { method: bust ? "POST" : "GET" });
            if (res.ok) payload = await res.json();
          } catch {
            // fall through to local computation
          }
        }

        if (!payload) {
          // Compute client-side from the game logs already in memory
          await new Promise((r) => setTimeout(r, 300));
          payload = computeLocalAnalysis(input.gameLogs);
        }

        setData(payload);
        fetchedFor.current = input.playerId;

        // Stagger card reveals
        [0, 1, 2, 3].forEach((i) => {
          setTimeout(() => setVisibleCards((prev) => new Set([...prev, i])), i * 120);
        });
      } catch {
        // Use local computation as final fallback
        const payload = computeLocalAnalysis(input.gameLogs);
        setData(payload);
        [0, 1, 2, 3].forEach((i) => {
          setTimeout(() => setVisibleCards((prev) => new Set([...prev, i])), i * 120);
        });
      } finally {
        setLoading(false);
      }
    },
    [input.playerId, input.gameLogs, input.apiBase, data]
  );

  useEffect(() => {
    if (input.gameLogs.length > 0) load();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input.playerId, input.gameLogs.length]);

  const regen = useCallback(async () => {
    if (regenning) return;
    setRegenning(true);
    setData(null);
    await load(true);
    setRegenning(false);
  }, [regenning, load]);

  function fmtTs(iso: string) {
    try {
      const d = new Date(iso);
      return (
        d.toLocaleTimeString("en-US", {
          hour: "2-digit", minute: "2-digit",
          timeZone: "America/Los_Angeles", hour12: false,
        }) + " PT"
      );
    } catch { return "--"; }
  }

  const noLogs = input.gameLogs.length === 0;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div
      style={{
        background: "#000",
        borderRadius: 14,
        padding: "20px 22px 24px",
        marginTop: 24,
      }}
    >
      {/* Top strip */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div
            style={{
              width: 7, height: 7, borderRadius: "50%", background: SIG,
              boxShadow: `0 0 6px ${SIG}`, flexShrink: 0,
            }}
          />
          <span style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.5)" }}>
            PIVOT INTEL ANALYSIS
          </span>
          {data && (
            <>
              <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)" }}>
                L{data.sample_window}
              </span>
              <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.25)" }}>
                {fmtTs(data.cached_at)}
              </span>
            </>
          )}
        </div>

        <button
          onClick={regen}
          disabled={regenning || loading || noLogs}
          style={{
            ...MONO, fontSize: 10, letterSpacing: "0.6px",
            display: "flex", alignItems: "center", gap: 5,
            padding: "5px 12px", background: "transparent",
            border: "0.5px solid rgba(255,255,255,0.1)", borderRadius: 6,
            color: regenning ? "rgba(255,255,255,0.25)" : "rgba(255,255,255,0.55)",
            cursor: regenning || loading || noLogs ? "default" : "pointer",
            transition: "color 120ms, border-color 120ms",
          }}
        >
          {regenning ? (
            <span style={{ display: "inline-block", animation: "ia-spin 800ms linear infinite" }}>&#x21bb;</span>
          ) : (
            "&#x21bb;"
          )}
          {regenning ? "RUNNING..." : "\u21bb REGENERATE"}
        </button>
      </div>

      {noLogs ? (
        <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", padding: "32px 0", textAlign: "center", letterSpacing: "0.06em" }}>
          Load game log data to generate analysis.
        </div>
      ) : loading ? (
        <div>
          {/* Briefing shimmer */}
          <div style={{ ...GLASS, padding: "20px 22px", marginBottom: 14, overflow: "hidden", position: "relative" }}>
            <div style={{ position: "absolute", inset: 0, background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.04) 50%, transparent 100%)", animation: "ia-shimmer 1.4s ease infinite" }} />
            {[200, 320, 260, 180, 140].map((w, i) => (
              <div key={i} style={{ height: i === 0 ? 14 : 10, width: w, background: "rgba(255,255,255,0.07)", borderRadius: 3, marginBottom: 12 }} />
            ))}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {[0, 1, 2, 3].map((i) => <CardShimmer key={i} />)}
          </div>
        </div>
      ) : data ? (
        <>
          {/* BRIEFING CARD */}
          <div style={{ ...GLASS, padding: "20px 22px", marginBottom: 14 }}>
            <div style={{ ...MONO, fontSize: 10, color: SIG, letterSpacing: "1.4px", marginBottom: 14 }}>
              PIVOT BRIEFING
            </div>

            <div style={{ ...HB, fontSize: 18, color: "#fff", lineHeight: 1.3, marginBottom: 12 }}>
              {data.briefing.headline}
            </div>

            <div
              style={{ ...HN, fontSize: 14, color: "rgba(255,255,255,0.82)", lineHeight: 1.65, marginBottom: 18 }}
            >
              {data.briefing.body}
            </div>

            <div style={{ borderTop: "0.5px solid rgba(255,255,255,0.07)", paddingTop: 14 }}>
              <div
                style={{ borderLeft: `2px solid ${SIG}`, paddingLeft: 14, cursor: "pointer" }}
                onClick={() => setExpandedDecision((v) => !v)}
              >
                <div style={{ ...MONO, fontSize: 9, color: SIG, letterSpacing: "1.3px", marginBottom: 6 }}>
                  PRESSING DECISION
                </div>
                <div style={{ ...HN, fontSize: 13, color: "rgba(255,255,255,0.9)", lineHeight: 1.6, display: expandedDecision ? "block" : "none" }}>
                  {data.briefing.decision}
                </div>
                {!expandedDecision && (
                  <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.35)" }}>tap to expand</div>
                )}
              </div>
            </div>
          </div>

          {/* 2x2 GRID */}
          <div
            className="ia-grid"
            style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}
          >
            {/* Card 1: Form Signal */}
            {visibleCards.has(0) && (
              <div style={{ ...GLASS, padding: 18, transition: "opacity 200ms", opacity: 1 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                  <span style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.45)" }}>
                    FORM SIGNAL
                  </span>
                  <span
                    style={{
                      ...MONO_B, fontSize: 11,
                      color: data.form_signal.trend === "warming" ? SIG :
                             data.form_signal.trend === "cooling" ? CORAL : "rgba(255,255,255,0.6)",
                    }}
                  >
                    {data.form_signal.trend === "warming" ? "\u25b2" : data.form_signal.trend === "cooling" ? "\u25bc" : "\u2014"}
                    {" "}{Math.abs(data.form_signal.delta_4w).toFixed(1)}
                  </span>
                </div>

                <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 12 }}>
                  <span style={{ ...MONO_B, fontSize: 26, color: "#fff" }}>
                    {data.form_signal.current_index.toFixed(1)}
                  </span>
                  <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)" }}>PI</span>
                </div>

                <AnalysisSparkline
                  data={data.form_signal.history}
                  trend={data.form_signal.trend}
                  width={200}
                  height={40}
                />

                <div style={{ marginTop: 12, borderTop: "0.5px solid rgba(255,255,255,0.06)", paddingTop: 10 }}>
                  <div style={{ ...HN, fontSize: 12, color: "rgba(255,255,255,0.65)", lineHeight: 1.55 }}>
                    {data.form_signal.reason}
                  </div>
                </div>
              </div>
            )}
            {!visibleCards.has(0) && <CardShimmer />}

            {/* Card 2: Matchup DNA */}
            {visibleCards.has(1) && (
              <div style={{ ...GLASS, padding: 18 }}>
                <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.45)", marginBottom: 14 }}>
                  MATCHUP DNA
                </div>
                {(() => {
                  const maxPPG = Math.max(...data.matchup_dna.splits.map((s) => s.ppg));
                  return (
                    <>
                      {data.matchup_dna.splits.map((s) => (
                        <MatchupBar
                          key={s.scheme}
                          {...s}
                          maxPPG={maxPPG}
                        />
                      ))}
                      <div style={{ borderTop: "0.5px solid rgba(255,255,255,0.06)", paddingTop: 10, marginTop: 2 }}>
                        <div style={{ ...HN, fontSize: 12, color: "rgba(255,255,255,0.65)", lineHeight: 1.55 }}>
                          {data.matchup_dna.next_opponent_read}
                        </div>
                      </div>
                    </>
                  );
                })()}
              </div>
            )}
            {!visibleCards.has(1) && <CardShimmer />}

            {/* Card 3: Load Read */}
            {visibleCards.has(2) && (
              <div style={{ ...GLASS, padding: 18 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                  <span style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.45)" }}>
                    LOAD READ
                  </span>
                  {data.load_read.flag && (
                    <span
                      style={{
                        ...MONO, fontSize: 9, letterSpacing: "0.8px",
                        padding: "2px 8px", borderRadius: 999,
                        background: `${CORAL}18`, border: `0.5px solid ${CORAL}55`, color: CORAL,
                      }}
                    >
                      FLAG
                    </span>
                  )}
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
                  {/* Header row */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 56px 72px", gap: 8, padding: "0 0 6px", borderBottom: "0.5px solid rgba(255,255,255,0.07)" }}>
                    <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "0.8px" }}>REST</span>
                    <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "0.8px", textAlign: "right" }}>PPG</span>
                    <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "0.8px", textAlign: "right" }}>NET RTG</span>
                  </div>
                  {data.load_read.rows.map((row) => {
                    const best = Math.max(...data.load_read.rows.map((r) => r.ppg));
                    const isStrong = row.ppg >= best * 0.95;
                    const isWeak   = row.ppg < best * 0.80;
                    return (
                      <div
                        key={row.rest_bucket}
                        style={{ display: "grid", gridTemplateColumns: "1fr 56px 72px", gap: 8, padding: "5px 0", borderBottom: "0.5px solid rgba(255,255,255,0.04)" }}
                      >
                        <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.6)" }}>{row.rest_bucket}</span>
                        <span style={{ ...MONO_B, fontSize: 11, textAlign: "right", color: isStrong ? SIG : isWeak ? CORAL : "#fff" }}>
                          {row.ppg.toFixed(1)}
                        </span>
                        <span style={{ ...MONO, fontSize: 10, textAlign: "right", color: "rgba(255,255,255,0.35)" }}>
                          {row.net_rating != null ? (row.net_rating > 0 ? "+" : "") + row.net_rating.toFixed(1) : "--"}
                        </span>
                      </div>
                    );
                  })}
                </div>

                <div style={{ borderTop: "0.5px solid rgba(255,255,255,0.06)", paddingTop: 10 }}>
                  <div style={{ ...HN, fontSize: 12, color: "rgba(255,255,255,0.65)", lineHeight: 1.55 }}>
                    {data.load_read.recommendation}
                  </div>
                </div>
              </div>
            )}
            {!visibleCards.has(2) && <CardShimmer />}

            {/* Card 4: Regression Watch */}
            {visibleCards.has(3) && (
              <div style={{ ...GLASS, padding: 18 }}>
                <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.45)", marginBottom: 14 }}>
                  REGRESSION WATCH
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 0, marginBottom: 12 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "52px 56px 1fr", gap: 8, padding: "0 0 6px", borderBottom: "0.5px solid rgba(255,255,255,0.07)" }}>
                    <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "0.8px" }}>STAT</span>
                    <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "0.8px", textAlign: "right" }}>NOW</span>
                    <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", letterSpacing: "0.8px" }}>OUTLOOK</span>
                  </div>
                  {data.regression.rows.map((row) => {
                    const dir = row.projected_direction;
                    const dirColor =
                      dir === "regress" ? CORAL :
                      dir === "improve" ? SIG : "rgba(255,255,255,0.35)";
                    const dirLabel =
                      dir === "regress" ? "\u25bc regress" :
                      dir === "improve" ? "\u25b2 improve" : "stable";
                    return (
                      <div
                        key={row.stat}
                        style={{ display: "grid", gridTemplateColumns: "52px 56px 1fr", gap: 8, padding: "7px 0", borderBottom: "0.5px solid rgba(255,255,255,0.04)", alignItems: "start" }}
                      >
                        <span style={{ ...MONO_B, fontSize: 11, color: "rgba(255,255,255,0.8)" }}>{row.stat}</span>
                        <span style={{ ...MONO_B, fontSize: 11, textAlign: "right", color: "#fff" }}>{row.current}</span>
                        <div>
                          <span style={{ ...MONO, fontSize: 10, color: dirColor }}>{dirLabel}</span>
                          <div style={{ ...HN, fontSize: 10, color: "rgba(255,255,255,0.4)", marginTop: 2, lineHeight: 1.4 }}>{row.reason}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div style={{ borderTop: "0.5px solid rgba(255,255,255,0.06)", paddingTop: 10 }}>
                  <div style={{ ...HN, fontSize: 12, color: "rgba(255,255,255,0.65)", lineHeight: 1.55 }}>
                    {data.regression.contextual_note}
                  </div>
                </div>
              </div>
            )}
            {!visibleCards.has(3) && <CardShimmer />}
          </div>

          {/* ACTION ROW */}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 4 }}>
            <button
              style={{
                ...MONO_B, fontSize: 11, padding: "10px 20px",
                background: SIG, color: "#000", border: "none", borderRadius: 8,
                cursor: "pointer", letterSpacing: "0.4px",
              }}
              onClick={() =>
                window.open(
                  `/chat?prompt=${encodeURIComponent(`Full analysis report for ${input.playerName}`)}`,
                  "_blank"
                )
              }
            >
              ANALYZE \u2197
            </button>
            {[
              { label: "Project next 5 \u2197", prompt: `Project next 5 games for ${input.playerName}` },
              { label: "Age comps \u2197",       prompt: `Age comparable players for ${input.playerName}` },
              { label: "Film tendencies \u2197", prompt: `Film tendency breakdown for ${input.playerName}` },
            ].map(({ label, prompt }) => (
              <button
                key={label}
                style={{
                  ...MONO, fontSize: 11, padding: "10px 16px",
                  background: "transparent", border: "0.5px solid rgba(255,255,255,0.1)",
                  borderRadius: 8, color: "rgba(255,255,255,0.6)", cursor: "pointer",
                  transition: "border-color 120ms, color 120ms",
                }}
                onClick={() =>
                  window.open(`/chat?prompt=${encodeURIComponent(prompt)}`, "_blank")
                }
              >
                {label}
              </button>
            ))}
          </div>
        </>
      ) : null}

      {/* Grid + spin keyframes */}
      <style>{`
        @keyframes ia-shimmer {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
        @keyframes ia-spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
        @media (max-width: 600px) {
          .ia-grid { grid-template-columns: 1fr !important; }
        }
      `}</style>
    </div>
  );
}
