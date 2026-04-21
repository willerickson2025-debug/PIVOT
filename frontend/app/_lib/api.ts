/**
 * PIVOT API client — typed wrappers around the Railway FastAPI backend.
 * All calls go through Next.js rewrites (/api/* → Railway /api/v1/*).
 */

const BASE = process.env.NEXT_PUBLIC_API_URL || "https://pivot-app-production-1eb4.up.railway.app/api/v1";

async function get<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(path, "http://localhost");
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(`${BASE}${url.pathname}${url.search}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Team {
  id: number;
  name: string;
  abbreviation: string;
  city: string;
  conference: string;
  division: string;
}

export interface Player {
  id: number;
  first_name: string;
  last_name: string;
  position: string | null;
  team: Team | null;
  nba_id: number | null;
}

export interface Game {
  id: number;
  date: string;
  status: string;
  home_team: Team;
  visitor_team: Team;
  home_team_score: number;
  visitor_team_score: number;
  postseason: boolean;
}

export interface AdvancedStats {
  ts_pct: number | null;
  efg_pct: number | null;
  usage_pct: number | null;
  off_rtg: number | null;
  def_rtg: number | null;
  net_rtg: number | null;
  ast_pct: number | null;
  ast_to_tov: number | null;
  reb_pct: number | null;
  pie: number | null;
  contested_fg_pct: number | null;
  screen_assists_pg: number | null;
  deflections_pg: number | null;
  pct_unassisted_fgm: number | null;
  defended_at_rim_fg_pct: number | null;
  [key: string]: number | null | undefined;
}

export interface PlayerPayload {
  id: number;
  name: string;
  team: string;
  position: string | null;
  is_active: boolean;
  basic: {
    pts: number | null;
    reb: number | null;
    ast: number | null;
    stl: number | null;
    blk: number | null;
    min: number | null;
    fg_pct: number | null;
    fg3_pct: number | null;
    ft_pct: number | null;
  };
  advanced: AdvancedStats;
}

export interface AnalyzePlayerResponse {
  payload: {
    season: number;
    season_label: string;
    player: PlayerPayload;
    games_played: number;
    sample_warnings: string[];
  };
  analysis: string;
  model: string;
  tokens_used: number;
}

export interface ComparePlayersResponse {
  player_a: Record<string, unknown>;
  player_b: Record<string, unknown>;
  analysis: string;
  structured: {
    key_differences: string[];
    better_for_context: string;
    reasoning: string;
  };
  model: string;
  tokens_used: number;
  season: number;
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

export const api = {
  health: () => get<{ status: string; environment: string; version: string }>("/health"),

  games: {
    list: (date?: string) =>
      get<{ games: Game[]; count: number }>("/nba/games", date ? { date } : undefined),
  },

  players: {
    search: (name: string) =>
      get<{ players: Player[]; count: number }>("/nba/players", { name }),
  },

  analysis: {
    player: (playerId: number, season = 2025) =>
      get<AnalyzePlayerResponse>("/analysis/player", { player_id: playerId, season }),

    playerByName: (playerName: string, season = 2025) =>
      get<AnalyzePlayerResponse>("/analysis/player", { player_name: playerName, season }),

    compare: (playerA: string, playerB: string, season = 2025) =>
      get<ComparePlayersResponse>("/analysis/compare", {
        player_a: playerA,
        player_b: playerB,
        season,
      }),
  },
};
