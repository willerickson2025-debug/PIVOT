"use client";

import { Suspense, useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";

// ─── constants ────────────────────────────────────────────────────────────────
const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://pivot-app-production-1eb4.up.railway.app/api/v1";
const HEL = '"Helvetica Neue", Helvetica, Arial, sans-serif';

const TEAM_TINTS: Record<string, string> = {
  ATL: "#C8AEAE", BOS: "#AAB7A8", BKN: "#B4B4B4", CHA: "#A8B4B8",
  CHI: "#C0A8A8", CLE: "#B8A8A0", DAL: "#A8B8C0", DEN: "#A7B6B0",
  DET: "#C0B0A8", GSW: "#B4B4A0", HOU: "#C0A8A8", IND: "#B8B0A0",
  LAC: "#C0AEAE", LAL: "#C0B8A0", MEM: "#A8B0B8", MIA: "#B0B8B0",
  MIL: "#A8B4A0", MIN: "#A8B0C0", NOP: "#A8B4B0", NYK: "#C0AFA0",
  OKC: "#B8C0C0", ORL: "#A8B4C0", PHI: "#A8B0C0", PHX: "#C0B0A8",
  POR: "#C0A8A8", SAC: "#C0B0B8", SAS: "#B4B4B4", TOR: "#C0A8A8",
  UTA: "#A8B4C0", WAS: "#C0B0B0",
};

const SECTIONS = [
  { id: "overview" as const, label: "OVERVIEW" },
  { id: "box"      as const, label: "BOX SCORE" },
];

// ─── types ────────────────────────────────────────────────────────────────────
interface ApiTeam {
  id: number;
  name: string;
  abbreviation: string;
  city: string;
  conference: string;
  division: string;
}
interface ApiGame {
  id: number;
  date: string;
  status: string;
  home_team: ApiTeam;
  visitor_team: ApiTeam;
  home_team_score: number;
  visitor_team_score: number;
  postseason: boolean;
}
interface StatLine {
  player: string;
  pos: string;
  min: string;
  pts: number;
  reb: number;
  ast: number;
  stl: number;
  blk: number;
  fg: string;
  fg3: string;
  ft: string;
  fg_pct: number;
  to: number;
  pf: number;
}
interface BsTeam {
  id: number | null;
  name: string;
  abbreviation: string;
  score: number;
}
interface BoxscoreData {
  game_id: number;
  game_info: {
    status: string;
    period: number | null;
    time: string | null;
    home_team_score: number;
    away_team_score: number;
  };
  home_team: BsTeam;
  away_team: BsTeam;
  home_players: StatLine[];
  away_players: StatLine[];
}
type SectionId = "overview" | "box";

// ─── helpers ──────────────────────────────────────────────────────────────────
function parseStatus(status: string): {
  isLive: boolean;
  isFinal: boolean;
  label: string;
} {
  const s = (status ?? "").trim();
  const lower = s.toLowerCase();
  if (!s) return { isLive: false, isFinal: false, label: "UPCOMING" };
  if (lower === "final" || lower.startsWith("final/"))
    return {
      isLive: false,
      isFinal: true,
      label: lower.includes("ot") ? "F/OT" : "FINAL",
    };
  // ISO 8601 scheduled datetime (e.g. "2026-04-22T00:00:00Z")
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) {
    try {
      const d = new Date(s);
      const label = d.toLocaleTimeString("en-US", {
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
        timeZone: "America/New_York",
      }).replace(":00", "").replace(" AM", " AM ET").replace(" PM", " PM ET");
      return { isLive: false, isFinal: false, label };
    } catch {
      return { isLive: false, isFinal: false, label: "UPCOMING" };
    }
  }
  if (
    lower.includes(" pm") ||
    lower.includes(" am") ||
    (lower.includes(":") && (lower.includes("et") || lower.includes("pt")))
  )
    return { isLive: false, isFinal: false, label: s };
  if (lower.includes("half")) return { isLive: true, isFinal: false, label: "HALF" };
  // Anything else with content is treated as live (e.g. "1Q", "3Q 4:12", "2nd Qtr")
  return { isLive: true, isFinal: false, label: s.toUpperCase() };
}

function parseMinutes(min: string): number {
  return parseInt((min ?? "0").split(":")[0]) || 0;
}

function calcTS(pts: number, fg: string, ft: string): number {
  const fga = parseInt((fg ?? "0-0").split("-")[1] ?? "0") || 0;
  const fta = parseInt((ft ?? "0-0").split("-")[1] ?? "0") || 0;
  const denom = 2 * (fga + 0.44 * fta);
  return denom > 0 ? Math.round((pts / denom) * 1000) / 10 : 0;
}

// ─── sub-components ───────────────────────────────────────────────────────────
function Orbs() {
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        overflow: "hidden",
        pointerEvents: "none",
        zIndex: 0,
      }}
    >
      <div className="pv-orb pv-orb-a" />
      <div className="pv-orb pv-orb-b" />
      <div className="pv-orb pv-orb-c" />
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage:
            "radial-gradient(rgba(255,255,255,0.04) 1px, transparent 1px)",
          backgroundSize: "3px 3px",
          mixBlendMode: "overlay",
        }}
      />
    </div>
  );
}

function Glass({
  children,
  style,
  radius = 16,
  padding = 20,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
  radius?: number;
  padding?: number | string;
}) {
  return (
    <div
      style={{
        background: "rgba(10, 12, 10, 0.55)",
        border: "1px solid rgba(255, 255, 255, 0.06)",
        boxShadow:
          "inset 0 1px 0 rgba(255,255,255,0.04), 0 20px 60px rgba(0,0,0,0.4)",
        backdropFilter: "blur(24px) saturate(140%)",
        WebkitBackdropFilter: "blur(24px) saturate(140%)",
        borderRadius: radius,
        padding,
        position: "relative",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function SectionTitle({
  left,
  right,
}: {
  left: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "10px 18px",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      <span
        style={{
          fontFamily: HEL,
          fontWeight: 700,
          fontSize: 11,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          color: "rgba(255,255,255,0.65)",
        }}
      >
        {left}
      </span>
      {right && (
        <span
          style={{
            fontFamily: HEL,
            fontWeight: 700,
            fontSize: 10,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            color: "rgba(255,255,255,0.4)",
          }}
        >
          {right}
        </span>
      )}
    </div>
  );
}

function GameTab({
  game,
  active,
  onClick,
}: {
  game: ApiGame;
  active: boolean;
  onClick: () => void;
}) {
  const { isLive, label } = parseStatus(game.status);
  const homeLeads = game.home_team_score >= game.visitor_team_score;

  return (
    <button
      role="tab"
      aria-selected={active}
      onClick={onClick}
      style={{
        flex: "0 0 auto",
        border: "none",
        cursor: "pointer",
        textAlign: "left",
        fontFamily: HEL,
        fontWeight: 700,
        background: active
          ? "rgba(0, 255, 102, 0.10)"
          : "rgba(10, 12, 10, 0.45)",
        borderBottom: active ? "2px solid #00FF66" : "2px solid transparent",
        padding: "14px 20px",
        color: "#fff",
        backdropFilter: "blur(20px) saturate(140%)",
        WebkitBackdropFilter: "blur(20px) saturate(140%)",
        minWidth: 220,
        transition: "background 160ms",
        outline: "none",
      }}
    >
      {/* Status row */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}
      >
        {isLive ? (
          <>
            <span
              className="pv-pulse-dot"
              style={{ width: 6, height: 6 }}
            />
            <span
              style={{
                fontSize: 10,
                letterSpacing: "0.14em",
                color: "#00FF66",
              }}
            >
              LIVE · {label}
            </span>
          </>
        ) : (
          <span
            style={{
              fontSize: 10,
              letterSpacing: "0.14em",
              color: "rgba(255,255,255,0.5)",
            }}
          >
            {label}
          </span>
        )}
      </div>
      {/* Teams + scores */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span
            style={{
              fontSize: 15,
              letterSpacing: "0.02em",
              color: !homeLeads ? "#fff" : "rgba(255,255,255,0.55)",
            }}
          >
            {game.visitor_team.abbreviation}
          </span>
          <span
            style={{
              fontSize: 15,
              letterSpacing: "0.02em",
              color: homeLeads ? "#fff" : "rgba(255,255,255,0.55)",
            }}
          >
            {game.home_team.abbreviation}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            textAlign: "right",
            fontFeatureSettings: '"tnum"',
            fontVariantNumeric: "tabular-nums",
          }}
        >
          <span
            style={{
              fontSize: 22,
              letterSpacing: "-0.02em",
              color: !homeLeads ? "#fff" : "rgba(255,255,255,0.55)",
            }}
          >
            {game.visitor_team_score}
          </span>
          <span
            style={{
              fontSize: 22,
              letterSpacing: "-0.02em",
              color: homeLeads ? "#fff" : "rgba(255,255,255,0.55)",
            }}
          >
            {game.home_team_score}
          </span>
        </div>
      </div>
    </button>
  );
}

function BoxTable({
  teamAbbr,
  teamCity,
  players,
}: {
  teamAbbr: string;
  teamCity: string;
  players: StatLine[];
}) {
  const cols = ["MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "FG", "3P", "TS%"] as const;
  const gtc = `1.8fr ${cols.map(() => "1fr").join(" ")}`;

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 12,
          padding: "12px 18px",
          borderBottom: "1px solid rgba(255,255,255,0.08)",
        }}
      >
        <span
          style={{
            fontFamily: HEL,
            fontWeight: 700,
            fontSize: 16,
            letterSpacing: "0.02em",
            textTransform: "uppercase",
          }}
        >
          {teamCity}
        </span>
        <span
          style={{
            fontFamily: HEL,
            fontWeight: 700,
            fontSize: 11,
            letterSpacing: "0.1em",
            color: "rgba(255,255,255,0.4)",
            textTransform: "uppercase",
          }}
        >
          {teamAbbr}
        </span>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: gtc,
          padding: "8px 18px",
          borderBottom: "1px solid rgba(255,255,255,0.05)",
        }}
      >
        <div
          style={{
            fontFamily: HEL,
            fontSize: 10,
            letterSpacing: "0.12em",
            color: "rgba(255,255,255,0.35)",
            fontWeight: 700,
          }}
        >
          PLAYER
        </div>
        {cols.map((c) => (
          <div
            key={c}
            style={{
              fontFamily: HEL,
              fontSize: 10,
              letterSpacing: "0.12em",
              color: "rgba(255,255,255,0.35)",
              textAlign: "right",
              fontWeight: 700,
            }}
          >
            {c}
          </div>
        ))}
      </div>
      {players.map((p, i) => {
        const ts = calcTS(p.pts, p.fg, p.ft);
        const mins = parseMinutes(p.min);
        const cells = [
          mins,
          p.pts,
          p.reb,
          p.ast,
          p.stl,
          p.blk,
          p.to,
          p.fg.replace("-", "/"),
          p.fg3.replace("-", "/"),
          ts > 0 ? ts.toFixed(1) : "-",
        ];
        return (
          <div
            key={i}
            style={{
              display: "grid",
              gridTemplateColumns: gtc,
              padding: "11px 18px",
              borderBottom: "1px solid rgba(255,255,255,0.04)",
              alignItems: "baseline",
            }}
          >
            <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
              <span
                style={{
                  fontFamily: HEL,
                  fontWeight: 700,
                  fontSize: 10,
                  letterSpacing: "0.12em",
                  color: "rgba(255,255,255,0.4)",
                  width: 22,
                  flexShrink: 0,
                }}
              >
                {p.pos}
              </span>
              <span
                style={{
                  fontFamily: HEL,
                  fontWeight: 700,
                  fontSize: 13,
                  letterSpacing: "0.01em",
                }}
              >
                {p.player}
              </span>
            </div>
            {cells.map((v, j) => (
              <div
                key={j}
                style={{
                  fontFamily: HEL,
                  fontWeight: 700,
                  fontSize: 13,
                  textAlign: "right",
                  fontVariantNumeric: "tabular-nums",
                  color: j === 1 ? "#fff" : "rgba(255,255,255,0.75)",
                }}
              >
                {v}
              </div>
            ))}
          </div>
        );
      })}
      {players.length === 0 && (
        <div
          style={{
            padding: "20px 18px",
            fontFamily: HEL,
            fontSize: 12,
            color: "rgba(255,255,255,0.3)",
            textAlign: "center",
          }}
        >
          No stats available yet
        </div>
      )}
    </div>
  );
}

function StartersPanel({
  teamCode,
  players,
}: {
  teamCode: string;
  players: StatLine[];
}) {
  const top5 = [...players].sort((a, b) => b.pts - a.pts).slice(0, 5);
  return (
    <Glass padding={0}>
      <div
        style={{
          padding: "14px 18px",
          borderBottom: "1px solid rgba(255,255,255,0.06)",
        }}
      >
        <span
          style={{
            fontFamily: HEL,
            fontWeight: 700,
            fontSize: 11,
            letterSpacing: "0.14em",
            color: "rgba(255,255,255,0.5)",
            textTransform: "uppercase",
          }}
        >
          {teamCode} · TOP PERFORMERS
        </span>
      </div>
      {top5.map((p, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "11px 18px",
            borderBottom:
              i < top5.length - 1 ? "1px solid rgba(255,255,255,0.04)" : "none",
          }}
        >
          <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
            <span
              style={{
                fontFamily: HEL,
                fontWeight: 700,
                fontSize: 10,
                letterSpacing: "0.08em",
                color: "rgba(255,255,255,0.4)",
                width: 20,
                flexShrink: 0,
              }}
            >
              {p.pos}
            </span>
            <span
              style={{
                fontFamily: HEL,
                fontWeight: 700,
                fontSize: 13,
                letterSpacing: "0.01em",
              }}
            >
              {p.player}
            </span>
          </div>
          <span
            style={{
              fontFamily: HEL,
              fontWeight: 700,
              fontSize: 12,
              letterSpacing: "0.04em",
              fontVariantNumeric: "tabular-nums",
              color: "rgba(255,255,255,0.7)",
              flexShrink: 0,
            }}
          >
            {p.pts}P · {parseMinutes(p.min)}M
          </span>
        </div>
      ))}
      {top5.length === 0 && (
        <div
          style={{
            padding: "20px 18px",
            fontFamily: HEL,
            fontSize: 12,
            color: "rgba(255,255,255,0.3)",
            textAlign: "center",
          }}
        >
          No data yet
        </div>
      )}
    </Glass>
  );
}

function Ticker({ games }: { games: ApiGame[] }) {
  if (games.length === 0) return null;
  const items = games.map((g) => {
    const { isLive, label } = parseStatus(g.status);
    return {
      away: g.visitor_team.abbreviation,
      awayScore: g.visitor_team_score,
      home: g.home_team.abbreviation,
      homeScore: g.home_team_score,
      status: label,
      isLive,
    };
  });
  const anyLive = items.some((item) => item.isLive);
  // Duplicate for seamless loop
  const all = [...items, ...items];

  return (
    <div
      role="region"
      aria-label="Live scores ticker"
      style={{
        position: "fixed",
        bottom: 0,
        left: 0,
        right: 0,
        height: 40,
        zIndex: 100,
        background: "rgba(0,0,0,0.80)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderTop: "1px solid rgba(255,255,255,0.06)",
        display: "flex",
        alignItems: "center",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "0 16px",
          borderRight: "1px solid rgba(255,255,255,0.06)",
          height: "100%",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}
      >
        {anyLive && <span className="pv-pulse-dot" style={{ width: 6, height: 6 }} />}
        <span
          style={{
            fontFamily: HEL,
            fontWeight: 700,
            fontSize: 11,
            letterSpacing: "0.1em",
            color: anyLive ? "#00FF66" : "rgba(255,255,255,0.45)",
          }}
        >
          {anyLive ? "LIVE" : "SCORES"}
        </span>
      </div>
      <div
        style={{ overflow: "hidden", flex: 1, position: "relative" }}
      >
        <div
          style={{
            display: "flex",
            gap: 40,
            paddingLeft: 32,
            whiteSpace: "nowrap",
            animation: "pv-tick 80s linear infinite",
          }}
        >
          {all.map((item, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 10,
                alignItems: "center",
                fontFamily: HEL,
                fontWeight: 700,
                fontSize: 12,
              }}
            >
              <span style={{ color: "#fff" }}>{item.away}</span>
              <span
                style={{
                  color: "rgba(255,255,255,0.75)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {item.awayScore}
              </span>
              <span style={{ color: "rgba(255,255,255,0.3)" }}>vs</span>
              <span style={{ color: "#fff" }}>{item.home}</span>
              <span
                style={{
                  color: "rgba(255,255,255,0.75)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {item.homeScore}
              </span>
              <span
                style={{
                  color: item.isLive ? "#00FF66" : "rgba(255,255,255,0.35)",
                  letterSpacing: "0.06em",
                }}
              >
                {item.status}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─── main content (uses useSearchParams — inside Suspense boundary) ───────────
function GamesContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const today = new Date().toISOString().slice(0, 10);
  const dateParam = searchParams.get("date");
  const selectedDate = dateParam ?? today;

  const [games, setGames] = useState<ApiGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [boxscore, setBoxscore] = useState<BoxscoreData | null>(null);
  const [bsLoading, setBsLoading] = useState(false);
  const [section, setSection] = useState<SectionId>("overview");
  const prevGameId = useRef<number | null>(null);

  // Derived: selected game (URL param → first in list)
  const gameIdParam = searchParams.get("game");
  const selectedGame: ApiGame | null = gameIdParam
    ? (games.find((g) => g.id === Number(gameIdParam)) ?? games[0] ?? null)
    : (games[0] ?? null);

  // Fetch games when date changes
  useEffect(() => {
    setLoading(true);
    setGames([]);
    setBoxscore(null);
    prevGameId.current = null;
    fetch(`${BASE}/nba/games?date=${selectedDate}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => setGames(data.games ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [selectedDate]);

  // Fetch boxscore when selected game changes
  useEffect(() => {
    if (!selectedGame) return;
    if (selectedGame.id === prevGameId.current) return;
    prevGameId.current = selectedGame.id;
    setBsLoading(true);
    setBoxscore(null);
    fetch(`${BASE}/nba/games/${selectedGame.id}/boxscore`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data: BoxscoreData) => {
        setBoxscore(data);
      })
      .catch(() => {})
      .finally(() => setBsLoading(false));
  }, [selectedGame?.id]);

  function handleTabClick(id: number) {
    router.replace(`/games?date=${selectedDate}&game=${id}`, { scroll: false });
    setSection("overview");
  }

  function navDate(delta: number) {
    const d = new Date(selectedDate + "T12:00:00");
    d.setDate(d.getDate() + delta);
    const next = d.toISOString().slice(0, 10);
    router.replace(`/games?date=${next}`, { scroll: false });
  }

  // Counts
  const liveCount = games.filter((g) => parseStatus(g.status).isLive).length;
  const finalCount = games.filter((g) => parseStatus(g.status).isFinal).length;

  // Date label
  const dateLabel = new Date(selectedDate + "T12:00:00")
    .toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    })
    .toUpperCase();

  // Detail derived values
  const bs = boxscore;
  const gi = bs?.game_info;
  const { isLive, label: statusLabel } = selectedGame
    ? parseStatus(selectedGame.status)
    : { isLive: false, label: "" };
  const periodLabel = gi?.period
    ? gi.period <= 4
      ? `Q${gi.period}`
      : "OT"
    : statusLabel;
  const clockStr = gi?.time ?? "";

  const homeAbbr = selectedGame?.home_team.abbreviation ?? "";
  const awayAbbr = selectedGame?.visitor_team.abbreviation ?? "";
  const homeTint = TEAM_TINTS[homeAbbr] ?? "rgba(255,255,255,0.08)";
  const awayTint = TEAM_TINTS[awayAbbr] ?? "rgba(255,255,255,0.08)";

  return (
    <div
      style={{
        position: "relative",
        minHeight: "100vh",
        background: "#000",
        overflowX: "hidden",
        paddingBottom: 60,
        fontFamily: HEL,
      }}
    >
      <Orbs />
      <div style={{ position: "relative", zIndex: 1 }}>

        {/* ── Page header ──────────────────────────────────────── */}
        <div style={{ padding: "24px 32px 12px" }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "flex-end",
            }}
          >
            <div>
              <h1
                style={{
                  fontFamily: HEL,
                  fontWeight: 700,
                  fontSize: 36,
                  letterSpacing: "-0.01em",
                  textTransform: "uppercase",
                  color: "#fff",
                  lineHeight: 1.1,
                }}
              >
                GAMES
              </h1>
              {/* Date navigation */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                <button
                  onClick={() => navDate(-1)}
                  style={{ background: "rgba(255,255,255,0.07)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, color: "#fff", cursor: "pointer", fontFamily: HEL, fontWeight: 700, fontSize: 14, width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center", outline: "none" }}
                  aria-label="Previous day"
                >‹</button>
                <input
                  type="date"
                  value={selectedDate}
                  onChange={(e) => router.replace(`/games?date=${e.target.value}`, { scroll: false })}
                  style={{ background: "rgba(255,255,255,0.07)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, color: "#fff", fontFamily: HEL, fontWeight: 700, fontSize: 11, letterSpacing: "0.08em", padding: "4px 8px", outline: "none", colorScheme: "dark" }}
                />
                <button
                  onClick={() => navDate(1)}
                  style={{ background: "rgba(255,255,255,0.07)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 6, color: "#fff", cursor: "pointer", fontFamily: HEL, fontWeight: 700, fontSize: 14, width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center", outline: "none" }}
                  aria-label="Next day"
                >›</button>
                <span style={{ fontFamily: HEL, fontWeight: 700, fontSize: 11, letterSpacing: "0.12em", color: "rgba(255,255,255,0.45)" }}>
                  {dateLabel}{selectedGame?.postseason ? " · PLAYOFFS" : ""}
                </span>
              </div>
            </div>
            <div
              style={{
                fontFamily: HEL,
                fontWeight: 700,
                fontSize: 11,
                letterSpacing: "0.12em",
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              {liveCount > 0 && (
                <>
                  <span className="pv-pulse-dot" style={{ width: 6, height: 6 }} />
                  <span style={{ color: "#00FF66" }}>{liveCount} LIVE</span>
                  <span style={{ color: "rgba(255,255,255,0.3)" }}>·</span>
                </>
              )}
              <span style={{ color: "rgba(255,255,255,0.55)" }}>
                {finalCount} FINAL
              </span>
            </div>
          </div>
        </div>

        {/* ── Game tabs strip ───────────────────────────────────── */}
        {!loading && (
          <div
            role="tablist"
            aria-label="Games"
            className="pv-scroll-hide"
            style={{
              display: "flex",
              overflowX: "auto",
              padding: "8px 32px 24px",
              gap: 10,
            }}
          >
            {games.length === 0 ? (
              <div
                style={{
                  fontFamily: HEL,
                  fontWeight: 700,
                  fontSize: 13,
                  color: "rgba(255,255,255,0.35)",
                  padding: "20px 0",
                }}
              >
                No games scheduled today.
              </div>
            ) : (
              games.map((g) => (
                <GameTab
                  key={g.id}
                  game={g}
                  active={g.id === selectedGame?.id}
                  onClick={() => handleTabClick(g.id)}
                />
              ))
            )}
          </div>
        )}

        {loading && (
          <div
            style={{
              padding: "40px 32px",
              fontFamily: HEL,
              fontSize: 12,
              letterSpacing: "0.12em",
              color: "rgba(255,255,255,0.3)",
            }}
          >
            LOADING...
          </div>
        )}

        {/* ── Detail view ───────────────────────────────────────── */}
        {selectedGame && (
          <div style={{ padding: "0 32px 40px" }} role="tabpanel">
            {/* SR score sentence */}
            <span className="sr-only" aria-live="polite">
              {selectedGame.visitor_team.city}{" "}
              {selectedGame.visitor_team_score},{" "}
              {selectedGame.home_team.city} {selectedGame.home_team_score}
              {isLive
                ? `, ${periodLabel}${clockStr ? `, ${clockStr} remaining` : ""}`
                : `, ${statusLabel}`}
            </span>

            {/* Detail header */}
            <Glass style={{ marginBottom: 20 }} padding={28}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 20,
                  flexWrap: "wrap",
                }}
              >
                {/* Score cluster */}
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 28,
                    flex: 1,
                    minWidth: 0,
                    flexWrap: "wrap",
                  }}
                >
                  {/* Away */}
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <div
                      style={{
                        width: 68,
                        height: 68,
                        borderRadius: 12,
                        background: awayTint,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <span
                        style={{
                          fontFamily: HEL,
                          fontWeight: 700,
                          fontSize: 20,
                          letterSpacing: "-0.02em",
                          color: "#000",
                        }}
                      >
                        {awayAbbr}
                      </span>
                    </div>
                    <span
                      style={{
                        fontFamily: HEL,
                        fontWeight: 700,
                        fontSize: 10,
                        letterSpacing: "0.12em",
                        color: "rgba(255,255,255,0.45)",
                      }}
                    >
                      {selectedGame.visitor_team.city.toUpperCase()}
                    </span>
                  </div>

                  <div
                    style={{
                      fontFamily: HEL,
                      fontWeight: 700,
                      fontSize: "clamp(48px, 6vw, 84px)",
                      letterSpacing: "-0.04em",
                      fontVariantNumeric: "tabular-nums",
                      color:
                        selectedGame.visitor_team_score >
                        selectedGame.home_team_score
                          ? "#fff"
                          : "rgba(255,255,255,0.45)",
                      lineHeight: 1,
                    }}
                    aria-hidden="true"
                  >
                    {selectedGame.visitor_team_score}
                  </div>

                  {/* Center stack */}
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 6,
                      padding: "0 18px",
                      flexShrink: 0,
                    }}
                  >
                    {selectedGame.postseason && (
                      <span
                        style={{
                          fontFamily: HEL,
                          fontWeight: 700,
                          fontSize: 11,
                          letterSpacing: "0.16em",
                          color: "#00FF66",
                          textTransform: "uppercase",
                        }}
                      >
                        PLAYOFFS
                      </span>
                    )}
                    {isLive ? (
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <span
                          className="pv-pulse-dot"
                          style={{ width: 7, height: 7 }}
                        />
                        <span
                          style={{
                            fontFamily: HEL,
                            fontWeight: 700,
                            fontSize: 14,
                            letterSpacing: "0.04em",
                            color: "#00FF66",
                          }}
                        >
                          {periodLabel}
                        </span>
                        {clockStr && (
                          <span
                            style={{
                              fontFamily: HEL,
                              fontWeight: 700,
                              fontSize: 14,
                              color: "rgba(255,255,255,0.7)",
                            }}
                          >
                            · {clockStr}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span
                        style={{
                          fontFamily: HEL,
                          fontWeight: 700,
                          fontSize: 14,
                          letterSpacing: "0.08em",
                          color: "rgba(255,255,255,0.7)",
                        }}
                      >
                        {statusLabel}
                      </span>
                    )}
                    <span
                      style={{
                        fontFamily: HEL,
                        fontWeight: 700,
                        fontSize: 11,
                        letterSpacing: "0.1em",
                        color: "rgba(255,255,255,0.35)",
                      }}
                    >
                      {awayAbbr} @ {homeAbbr}
                    </span>
                  </div>

                  <div
                    style={{
                      fontFamily: HEL,
                      fontWeight: 700,
                      fontSize: "clamp(48px, 6vw, 84px)",
                      letterSpacing: "-0.04em",
                      fontVariantNumeric: "tabular-nums",
                      color:
                        selectedGame.home_team_score >=
                        selectedGame.visitor_team_score
                          ? "#fff"
                          : "rgba(255,255,255,0.45)",
                      lineHeight: 1,
                    }}
                    aria-hidden="true"
                  >
                    {selectedGame.home_team_score}
                  </div>

                  {/* Home */}
                  <div
                    style={{
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <div
                      style={{
                        width: 68,
                        height: 68,
                        borderRadius: 12,
                        background: homeTint,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      <span
                        style={{
                          fontFamily: HEL,
                          fontWeight: 700,
                          fontSize: 20,
                          letterSpacing: "-0.02em",
                          color: "#000",
                        }}
                      >
                        {homeAbbr}
                      </span>
                    </div>
                    <span
                      style={{
                        fontFamily: HEL,
                        fontWeight: 700,
                        fontSize: 10,
                        letterSpacing: "0.12em",
                        color: "rgba(255,255,255,0.45)",
                      }}
                    >
                      {selectedGame.home_team.city.toUpperCase()}
                    </span>
                  </div>
                </div>

                {/* Right: date */}
                <div
                  style={{
                    fontFamily: HEL,
                    fontWeight: 700,
                    fontSize: 10,
                    letterSpacing: "0.14em",
                    color: "rgba(255,255,255,0.45)",
                    textAlign: "right",
                    lineHeight: 1.8,
                    flexShrink: 0,
                  }}
                >
                  {new Date(selectedGame.date + "T12:00:00")
                    .toLocaleDateString("en-US", {
                      weekday: "short",
                      month: "short",
                      day: "numeric",
                    })
                    .toUpperCase()}
                </div>
              </div>
            </Glass>

            {/* Section tabs */}
            <div
              style={{
                display: "flex",
                gap: 8,
                marginBottom: 20,
                flexWrap: "wrap",
              }}
            >
              {SECTIONS.map((sec) => {
                const active = section === sec.id;
                return (
                  <button
                    key={sec.id}
                    onClick={() => setSection(sec.id)}
                    style={{
                      fontFamily: HEL,
                      fontWeight: 700,
                      fontSize: 11,
                      letterSpacing: "0.12em",
                      padding: "8px 16px",
                      borderRadius: 999,
                      border: active
                        ? "1px solid rgba(0,255,102,0.4)"
                        : "1px solid rgba(255,255,255,0.10)",
                      background: active
                        ? "rgba(0,255,102,0.14)"
                        : "rgba(255,255,255,0.04)",
                      color: active ? "#00FF66" : "rgba(255,255,255,0.65)",
                      cursor: "pointer",
                      transition: "all 120ms",
                      outline: "none",
                    }}
                  >
                    {sec.label}
                  </button>
                );
              })}
            </div>

            {/* ── OVERVIEW ── */}
            {section === "overview" && (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 400px), 1fr))",
                  gap: 20,
                }}
              >
                {/* Left column */}
                <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                  {bs ? (
                    <Glass padding={0}>
                      <SectionTitle left="BOX SCORE" />
                      <BoxTable
                        teamAbbr={bs.away_team.abbreviation}
                        teamCity={bs.away_team.name}
                        players={bs.away_players}
                      />
                      <BoxTable
                        teamAbbr={bs.home_team.abbreviation}
                        teamCity={bs.home_team.name}
                        players={bs.home_players}
                      />
                    </Glass>
                  ) : bsLoading ? (
                    <Glass>
                      <div
                        style={{
                          fontFamily: HEL,
                          fontSize: 12,
                          letterSpacing: "0.1em",
                          color: "rgba(255,255,255,0.3)",
                          textAlign: "center",
                          padding: 20,
                        }}
                      >
                        LOADING BOX SCORE...
                      </div>
                    </Glass>
                  ) : null}
                </div>

                {/* Right column */}
                <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                  {bs ? (
                    <>
                      <StartersPanel
                        teamCode={bs.away_team.abbreviation}
                        players={bs.away_players}
                      />
                      <StartersPanel
                        teamCode={bs.home_team.abbreviation}
                        players={bs.home_players}
                      />
                    </>
                  ) : (
                    <>
                      <Glass>
                        <div
                          style={{
                            fontFamily: HEL,
                            fontSize: 12,
                            color: "rgba(255,255,255,0.3)",
                            textAlign: "center",
                            padding: 20,
                          }}
                        >
                          No stats yet
                        </div>
                      </Glass>
                      <Glass>
                        <div
                          style={{
                            fontFamily: HEL,
                            fontSize: 12,
                            color: "rgba(255,255,255,0.3)",
                            textAlign: "center",
                            padding: 20,
                          }}
                        >
                          No stats yet
                        </div>
                      </Glass>
                    </>
                  )}
                </div>
              </div>
            )}

            {/* ── BOX SCORE ── */}
            {section === "box" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
                {bs ? (
                  <>
                    <Glass padding={0}>
                      <BoxTable
                        teamAbbr={bs.away_team.abbreviation}
                        teamCity={bs.away_team.name}
                        players={bs.away_players}
                      />
                    </Glass>
                    <Glass padding={0}>
                      <BoxTable
                        teamAbbr={bs.home_team.abbreviation}
                        teamCity={bs.home_team.name}
                        players={bs.home_players}
                      />
                    </Glass>
                  </>
                ) : (
                  <Glass>
                    <div
                      style={{
                        fontFamily: HEL,
                        fontSize: 12,
                        letterSpacing: "0.1em",
                        color: "rgba(255,255,255,0.3)",
                        textAlign: "center",
                        padding: 40,
                      }}
                    >
                      {bsLoading ? "LOADING..." : "BOX SCORE UNAVAILABLE"}
                    </div>
                  </Glass>
                )}
              </div>
            )}

          </div>
        )}
      </div>

      <Ticker games={games} />
    </div>
  );
}

// ─── page export (Suspense wrapper required for useSearchParams) ──────────────
export default function GamesPage() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            background: "#000",
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <span
            style={{
              fontFamily: HEL,
              fontWeight: 700,
              fontSize: 12,
              letterSpacing: "0.12em",
              color: "rgba(255,255,255,0.3)",
            }}
          >
            LOADING...
          </span>
        </div>
      }
    >
      <GamesContent />
    </Suspense>
  );
}
