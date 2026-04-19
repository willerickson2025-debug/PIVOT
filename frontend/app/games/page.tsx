import { api, Game } from "../_lib/api";

function score(g: Game) {
  if (g.status === "Final" || g.home_team_score > 0 || g.visitor_team_score > 0) {
    return `${g.visitor_team_score} – ${g.home_team_score}`;
  }
  return null;
}

function statusLabel(g: Game) {
  const s = g.status ?? "";
  if (s === "Final") return { text: "FINAL", color: "var(--smoke)" };
  if (/^\d+Q/.test(s) || s.toLowerCase().includes("half")) {
    return { text: s.toUpperCase(), color: "var(--live)" };
  }
  return { text: s.toUpperCase(), color: "var(--smoke)" };
}

function GameCard({ g }: { g: Game }) {
  const sc = score(g);
  const { text: statusText, color: statusColor } = statusLabel(g);

  return (
    <div
      className="glass"
      style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}
    >
      {/* Teams row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {/* Visitor */}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              className="label"
              style={{ width: 36, textAlign: "right", color: "var(--smoke)" }}
            >
              {g.visitor_team.abbreviation}
            </span>
            <span style={{ color: "var(--ash)", fontSize: 13 }}>{g.visitor_team.city}</span>
          </div>
          {/* Home */}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              className="label"
              style={{ width: 36, textAlign: "right", color: "var(--bone)" }}
            >
              {g.home_team.abbreviation}
            </span>
            <span style={{ color: "var(--bone)", fontSize: 13 }}>{g.home_team.city}</span>
          </div>
        </div>

        {/* Score / Status */}
        <div style={{ textAlign: "right" }}>
          {sc ? (
            <div
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 22,
                fontWeight: 600,
                color: "var(--bone)",
                letterSpacing: "-0.02em",
              }}
            >
              {sc}
            </div>
          ) : null}
          <div
            className="label"
            style={{ color: statusColor, marginTop: sc ? 4 : 0 }}
          >
            {statusText}
          </div>
        </div>
      </div>

      {/* Postseason badge */}
      {g.postseason && (
        <div
          className="label"
          style={{
            color: "var(--neon)",
            borderTop: "1px solid var(--wire)",
            paddingTop: 8,
            marginTop: 2,
          }}
        >
          Playoffs
        </div>
      )}
    </div>
  );
}

export default async function GamesPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;

  let games: Game[] = [];
  let error: string | null = null;

  try {
    const result = await api.games.list(date);
    games = result.games;
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load games";
  }

  const today = new Date().toISOString().slice(0, 10);
  const displayDate = date ?? today;

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "32px 24px" }}>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          marginBottom: 24,
        }}
      >
        <h1
          style={{ fontSize: 20, fontWeight: 600, color: "var(--bone)" }}
        >
          Games
        </h1>
        <span className="label">{displayDate}</span>
      </div>

      {error ? (
        <div
          className="glass"
          style={{ padding: 24, color: "var(--danger)", textAlign: "center" }}
        >
          {error}
        </div>
      ) : games.length === 0 ? (
        <div
          className="glass"
          style={{ padding: 24, color: "var(--smoke)", textAlign: "center" }}
        >
          No games scheduled for {displayDate}.
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: 12,
          }}
        >
          {games.map((g) => (
            <GameCard key={g.id} g={g} />
          ))}
        </div>
      )}
    </div>
  );
}
