"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";

// ── Brand tokens ──────────────────────────────────────────────────────────────

const SIG = "#39FF14";
const CORAL = "#FF6B4A";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
  fontWeight: 700,
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
  background: "rgba(255, 255, 255, 0.04)",
  backdropFilter: "blur(24px) saturate(180%)",
  WebkitBackdropFilter: "blur(24px) saturate(180%)",
  border: "0.5px solid rgba(255, 255, 255, 0.08)",
  borderRadius: 12,
};
const GLASS_SM: React.CSSProperties = {
  background: "rgba(255, 255, 255, 0.04)",
  backdropFilter: "blur(24px) saturate(180%)",
  WebkitBackdropFilter: "blur(24px) saturate(180%)",
  border: "0.5px solid rgba(255, 255, 255, 0.08)",
  borderRadius: 8,
};
const GLASS_PILL: React.CSSProperties = {
  background: "rgba(255, 255, 255, 0.04)",
  backdropFilter: "blur(24px) saturate(180%)",
  WebkitBackdropFilter: "blur(24px) saturate(180%)",
  border: "0.5px solid rgba(255, 255, 255, 0.08)",
  borderRadius: 999,
};

// ── Types ─────────────────────────────────────────────────────────────────────

type CapTier =
  | "APRON_2"
  | "APRON_1"
  | "OVER_TAX"
  | "OVER_CAP"
  | "UNDER_CAP";

interface RailTeam {
  abbr: string;
  name: string;
  record: string;
  payroll: number;
  tier: CapTier;
}

interface PayrollYear {
  season: string;
  total: number;
  tier_at_eoy: CapTier;
  top_contracts: { player: string; amount: number; type: string }[];
}

interface RosterPlayer {
  name: string;
  age: number;
  role_tier: "STAR" | "STARTER" | "ROTATION" | "BENCH";
  minute_share: number;
  aging_curve_status: string;
}

interface ExpiringPlayer {
  player: string;
  position: string;
  cap_hit: number;
  type: string;
  bird_rights: boolean;
  retain_recommendation: "KEEP" | "REPLACE" | "LET_WALK";
  reasoning: string;
}

interface TradeChip {
  player: string;
  position: string;
  age: number;
  cap_hit: number;
  years_remaining: number;
  movability_score: number;
  comparable_trades: { partner: string; players_received: string; year: number }[];
}

interface TradeMatch {
  partner_abbr: string;
  reason: string;
  concept: string;
}

interface FranchiseData {
  team: {
    abbr: string;
    name: string;
    record: string;
    conference_seed: string;
    head_coach: string;
    gm: string;
  };
  cap: {
    payroll: number;
    tier: CapTier;
    delta_to_tax: number;
    delta_to_apron_1: number;
    delta_to_apron_2: number;
    restrictions: string[];
    tools: string[];
  };
  payroll_by_year: PayrollYear[];
  roster_aging: {
    avg_age: number;
    median_age: number;
    championship_median_age: number;
    window_classification: "closing" | "open" | "opening" | "rebuild";
    window_reasoning: string;
    players: RosterPlayer[];
  };
  expiring: ExpiringPlayer[];
  trade_chips: TradeChip[];
  trade_matches: TradeMatch[];
  ai_briefing: {
    headline: string;
    body: string;
    pressing_decision: string;
    generated_at: string;
  };
}

// ── Stub data for 5 hardcoded teams (scaffold for real pipeline) ──────────────

const RAIL_DATA: RailTeam[] = [
  { abbr: "BOS", name: "Celtics",    record: "62-20", payroll: 197.4, tier: "APRON_2" },
  { abbr: "GSW", name: "Warriors",   record: "46-36", payroll: 191.8, tier: "APRON_2" },
  { abbr: "PHX", name: "Suns",       record: "49-33", payroll: 185.2, tier: "APRON_1" },
  { abbr: "MIL", name: "Bucks",      record: "48-34", payroll: 179.6, tier: "APRON_1" },
  { abbr: "NYK", name: "Knicks",     record: "50-32", payroll: 173.1, tier: "OVER_TAX" },
  { abbr: "LAL", name: "Lakers",     record: "47-35", payroll: 168.4, tier: "OVER_TAX" },
  { abbr: "DEN", name: "Nuggets",    record: "57-25", payroll: 161.2, tier: "OVER_CAP" },
  { abbr: "MIN", name: "Timberwolves",record:"49-33",  payroll: 158.7, tier: "OVER_CAP" },
  { abbr: "OKC", name: "Thunder",    record: "57-25", payroll: 134.1, tier: "UNDER_CAP" },
  { abbr: "IND", name: "Pacers",     record: "47-35", payroll: 128.9, tier: "UNDER_CAP" },
];

const FRANCHISE_STUBS: Record<string, FranchiseData> = {
  BOS: {
    team: { abbr: "BOS", name: "Boston Celtics", record: "62-20", conference_seed: "#1 East", head_coach: "Joe Mazzulla", gm: "Brad Stevens" },
    cap: {
      payroll: 197.4, tier: "APRON_2",
      delta_to_tax: -27.4, delta_to_apron_1: -21.2, delta_to_apron_2: -9.3,
      restrictions: ["NO AGGREGATION", "NO TPE", "NO SIGN-AND-TRADE", "FROZEN 2026 1ST"],
      tools: [],
    },
    payroll_by_year: [
      { season: "24-25", total: 197.4, tier_at_eoy: "APRON_2",  top_contracts: [{ player: "Tatum", amount: 54.1, type: "MAX" }, { player: "Brown", amount: 49.0, type: "MAX" }, { player: "Porzingis", amount: 30.7, type: "EXT" }] },
      { season: "25-26", total: 201.2, tier_at_eoy: "APRON_2",  top_contracts: [{ player: "Tatum", amount: 54.1, type: "MAX" }, { player: "Brown", amount: 49.0, type: "MAX" }, { player: "Porzingis", amount: 30.7, type: "EXT" }] },
      { season: "26-27", total: 183.1, tier_at_eoy: "APRON_1",  top_contracts: [{ player: "Tatum", amount: 57.3, type: "MAX" }, { player: "Brown", amount: 52.1, type: "MAX" }, { player: "White",   amount: 22.6, type: "EXT" }] },
      { season: "27-28", total: 169.8, tier_at_eoy: "OVER_TAX", top_contracts: [{ player: "Tatum", amount: 60.8, type: "MAX" }, { player: "Brown", amount: 55.4, type: "MAX" }, { player: "White",   amount: 22.6, type: "EXT" }] },
      { season: "28-29", total: 128.3, tier_at_eoy: "OVER_CAP", top_contracts: [{ player: "Tatum", amount: 64.5, type: "MAX" }, { player: "Brown", amount: 14.2, type: "PO" }] },
    ],
    roster_aging: {
      avg_age: 28.6, median_age: 28.1, championship_median_age: 28.3,
      window_classification: "closing",
      window_reasoning: "Window closing. Core trio averages 29.4, oldest among East contenders. 18-month urgency before payroll cliffs collide with age decline.",
      players: [
        { name: "Tatum",     age: 26, role_tier: "STAR",     minute_share: 0.34, aging_curve_status: "Peak" },
        { name: "Brown",     age: 27, role_tier: "STAR",     minute_share: 0.32, aging_curve_status: "Peak" },
        { name: "White",     age: 30, role_tier: "STARTER",  minute_share: 0.28, aging_curve_status: "Declining" },
        { name: "Holiday",   age: 34, role_tier: "STARTER",  minute_share: 0.26, aging_curve_status: "Late career" },
        { name: "Porzingis", age: 29, role_tier: "STARTER",  minute_share: 0.23, aging_curve_status: "Health risk" },
        { name: "Hauser",    age: 29, role_tier: "ROTATION", minute_share: 0.18, aging_curve_status: "Stable" },
        { name: "Kornet",    age: 28, role_tier: "ROTATION", minute_share: 0.14, aging_curve_status: "Stable" },
        { name: "Payton II", age: 31, role_tier: "ROTATION", minute_share: 0.12, aging_curve_status: "Declining" },
        { name: "Jaden Springer", age: 22, role_tier: "BENCH", minute_share: 0.10, aging_curve_status: "Developing" },
      ],
    },
    expiring: [
      { player: "Al Horford",   position: "C",  cap_hit: 9.5, type: "EXP", bird_rights: true,  retain_recommendation: "LET_WALK", reasoning: "Age 38 at expiry, declining production, $9.5M creates MLE flexibility. Full Bird rights but replacement cost is lower at C depth." },
      { player: "Jrue Holiday", position: "PG", cap_hit: 30.8, type: "PO", bird_rights: true,  retain_recommendation: "KEEP",     reasoning: "Elite defender, championship experience, Bird rights allow full re-sign. Irreplaceable glue at this stage of window." },
      { player: "Sam Hauser",   position: "SF", cap_hit: 12.3, type: "EXT",bird_rights: true,  retain_recommendation: "KEEP",     reasoning: "46% corner three, fits system, Bird rights available. Would cost $14-16M on open market given shooting scarcity." },
    ],
    trade_chips: [
      { player: "Kristaps Porzingis", position: "C",  age: 29, cap_hit: 30.7, years_remaining: 1, movability_score: 8, comparable_trades: [{ partner: "WAS", players_received: "Beal + picks", year: 2023 }, { partner: "MIN", players_received: "KAT", year: 2023 }] },
      { player: "Payton II",          position: "PG", age: 31, cap_hit: 8.1,  years_remaining: 1, movability_score: 5, comparable_trades: [{ partner: "GSW", players_received: "rotation piece", year: 2022 }] },
      { player: "Luke Kornet",        position: "C",  age: 28, cap_hit: 3.9,  years_remaining: 1, movability_score: 4, comparable_trades: [] },
    ],
    trade_matches: [
      { partner_abbr: "MIA", reason: "Need wing scoring depth, under cap, willing to absorb salary 1-for-1", concept: "BOS sends Porzingis for Butler + expiring, frees $30.7M in 25-26" },
      { partner_abbr: "DET", reason: "Rebuilding, want young assets, BOS needs cost-controlled rotation guard", concept: "BOS sends Payton II + future 2nd for Cade Cunningham salary match" },
      { partner_abbr: "SAC", reason: "Need proven wing defender, can absorb in cap space", concept: "BOS sends Hauser for draft capital and cap flexibility" },
    ],
    ai_briefing: {
      headline: "Championship window is now. The 28-29 cliff changes everything.",
      body: "Boston is the rare franchise simultaneously at peak performance and entering a payroll correction cycle. The 28-29 season brings $41M in relief when Holiday's PO and Porzingis expire, but the current Apron 2 status freezes first-round picks and blocks aggregation, leaving the front office with no runway for a midseason splash. The 18-month window before those restrictions bite harder is the single most important scheduling reality Stevens is managing. If Porzingis stays healthy, this roster needs no additions. If he misses significant time again, the team has no legal mechanism to add above replacement.",
      pressing_decision: "Decide by February trade deadline whether Porzingis health justifies keeping his $30.7M expiring as trade leverage vs. using it now in a 1-for-1 to upgrade wing depth before Apron 2 freezes the 2026 first.",
      generated_at: "2025-04-19T14:22:00Z",
    },
  },
  OKC: {
    team: { abbr: "OKC", name: "Oklahoma City Thunder", record: "57-25", conference_seed: "#1 West", head_coach: "Mark Daigneault", gm: "Sam Presti" },
    cap: {
      payroll: 134.1, tier: "UNDER_CAP",
      delta_to_tax: 35.9, delta_to_apron_1: 53.9, delta_to_apron_2: 65.9,
      restrictions: [],
      tools: ["FULL MLE", "$14M ROOM", "BIRD RIGHTS READY"],
    },
    payroll_by_year: [
      { season: "24-25", total: 134.1, tier_at_eoy: "UNDER_CAP",  top_contracts: [{ player: "SGA",   amount: 33.4, type: "MAX" }, { player: "Holmgren", amount: 11.8, type: "RKS" }, { player: "Wallace", amount: 4.2, type: "RKS" }] },
      { season: "25-26", total: 158.3, tier_at_eoy: "OVER_CAP",   top_contracts: [{ player: "SGA",   amount: 36.0, type: "MAX" }, { player: "Holmgren", amount: 35.8, type: "EXT" }, { player: "Dort",    amount: 16.7, type: "EXT" }] },
      { season: "26-27", total: 171.4, tier_at_eoy: "OVER_TAX",   top_contracts: [{ player: "SGA",   amount: 38.2, type: "MAX" }, { player: "Holmgren", amount: 37.9, type: "EXT" }, { player: "Wallace", amount: 29.1, type: "EXT" }] },
      { season: "27-28", total: 179.8, tier_at_eoy: "APRON_1",    top_contracts: [{ player: "SGA",   amount: 40.5, type: "MAX" }, { player: "Holmgren", amount: 40.2, type: "EXT" }, { player: "Wallace", amount: 30.8, type: "EXT" }] },
      { season: "28-29", total: 192.1, tier_at_eoy: "APRON_2",    top_contracts: [{ player: "SGA",   amount: 43.0, type: "MAX" }, { player: "Holmgren", amount: 42.7, type: "EXT" }, { player: "Wallace", amount: 32.6, type: "EXT" }] },
    ],
    roster_aging: {
      avg_age: 22.8, median_age: 22.4, championship_median_age: 28.3,
      window_classification: "opening",
      window_reasoning: "Window opening. Youngest playoff contender in decades, peak years for SGA and Holmgren land precisely at projected Apron 2 threshold in 28-29. Presti has 4 years to build before the constraints arrive.",
      players: [
        { name: "SGA",      age: 26, role_tier: "STAR",     minute_share: 0.36, aging_curve_status: "Prime entry" },
        { name: "Holmgren", age: 22, role_tier: "STAR",     minute_share: 0.32, aging_curve_status: "Developing" },
        { name: "Wallace",  age: 20, role_tier: "STARTER",  minute_share: 0.29, aging_curve_status: "Developing" },
        { name: "Dort",     age: 25, role_tier: "STARTER",  minute_share: 0.26, aging_curve_status: "Peak entry" },
        { name: "Williams", age: 22, role_tier: "ROTATION", minute_share: 0.18, aging_curve_status: "Developing" },
        { name: "Caruso",   age: 30, role_tier: "ROTATION", minute_share: 0.16, aging_curve_status: "Veteran" },
        { name: "Giddey",   age: 22, role_tier: "ROTATION", minute_share: 0.14, aging_curve_status: "Developing" },
      ],
    },
    expiring: [
      { player: "Alex Caruso",  position: "PG", cap_hit: 13.2, type: "EXP", bird_rights: false, retain_recommendation: "KEEP",     reasoning: "Elite perimeter defender, veteran leadership at zero cost on young roster. Bird rights not available but team can use Full MLE to re-sign at market." },
      { player: "Isaiah Joe",   position: "SG", cap_hit: 3.1,  type: "EXP", bird_rights: true,  retain_recommendation: "REPLACE",  reasoning: "Shooting specialist with 10 cap units available. Open market will demand $8-10M, better value through draft or FA at that range." },
    ],
    trade_chips: [
      { player: "Josh Giddey",   position: "PG", age: 22, cap_hit: 6.3,  years_remaining: 1, movability_score: 7, comparable_trades: [{ partner: "CHI", players_received: "Coby White swap", year: 2024 }] },
      { player: "Luguentz Dort", position: "SF", age: 25, cap_hit: 16.7, years_remaining: 2, movability_score: 6, comparable_trades: [{ partner: "DEN", players_received: "wing + pick", year: 2023 }] },
    ],
    trade_matches: [
      { partner_abbr: "BOS", reason: "BOS needs rotation and is asset-constrained under Apron 2", concept: "OKC sends Giddey for Kornet + future protected second" },
      { partner_abbr: "LAL", reason: "LAL needs youth injection, OKC has trade chips but no cap constraints", concept: "OKC sends Dort for rotation piece and 2026 first" },
      { partner_abbr: "SAC", reason: "SAC in rebuild mode, OKC can acquire young talent without giving picks", concept: "OKC sends Giddey + Joe for DeAaron Fox if SAC pivots to full rebuild" },
    ],
    ai_briefing: {
      headline: "Presti is 4 years away from peak. The question is whether to accelerate.",
      body: "Oklahoma City is building toward the 2027-29 window when SGA, Holmgren, and Wallace simultaneously enter their primes under contracts that have not yet breached Apron 1. The current UNDER_CAP status is not a limitation, it is leverage. OKC can absorb salary, aggregate picks, and use the Full MLE to add a veteran without touching future flexibility. The singular risk is the 28-29 Apron 2 ceiling that arrives precisely when this core peaks, meaning Presti has roughly one more major addition available before the CBA locks the franchise into the same box it watched Boston and Golden State build themselves into.",
      pressing_decision: "Decide now whether Giddey is a long-term fit or a trade asset. His $6.3M expiring is the cleanest chip on the roster and the best vehicle for acquiring a veteran third star before the cap situation tightens.",
      generated_at: "2025-04-19T14:22:00Z",
    },
  },
};

// Fill remaining teams with placeholder stubs
["GSW", "PHX", "MIL", "NYK", "LAL", "DEN", "MIN", "IND"].forEach((abbr) => {
  const rail = RAIL_DATA.find((t) => t.abbr === abbr)!;
  FRANCHISE_STUBS[abbr] = {
    team: { abbr, name: rail.name, record: rail.record, conference_seed: "--", head_coach: "--", gm: "--" },
    cap: { payroll: rail.payroll, tier: rail.tier, delta_to_tax: 0, delta_to_apron_1: 0, delta_to_apron_2: 0, restrictions: rail.tier === "APRON_2" ? ["NO AGGREGATION", "NO TPE"] : [], tools: rail.tier === "UNDER_CAP" ? ["FULL MLE", "ROOM"] : [] },
    payroll_by_year: [],
    roster_aging: { avg_age: 27.0, median_age: 27.0, championship_median_age: 28.3, window_classification: "open", window_reasoning: "Data loading...", players: [] },
    expiring: [],
    trade_chips: [],
    trade_matches: [],
    ai_briefing: { headline: "Full briefing available for BOS and OKC in this prototype.", body: "Connect the /api/franchise/:abbr endpoint to populate full intelligence for all 30 teams. The scaffold is wired, the prompts are defined, data pipeline is the remaining work.", pressing_decision: "Wire Spotrac contract scraper and CBA rules table to unlock all teams.", generated_at: new Date().toISOString() },
  };
});

// ── Helpers ───────────────────────────────────────────────────────────────────

const TIER_GROUPS: { tier: CapTier; label: string }[] = [
  { tier: "APRON_2",   label: "APRON 2" },
  { tier: "APRON_1",   label: "APRON 1" },
  { tier: "OVER_TAX",  label: "OVER TAX" },
  { tier: "OVER_CAP",  label: "OVER CAP" },
  { tier: "UNDER_CAP", label: "UNDER CAP" },
];

const TIER_COLOR: Record<CapTier, string> = {
  APRON_2:   CORAL,
  APRON_1:   "#F5A623",
  OVER_TAX:  "#E0C84A",
  OVER_CAP:  "rgba(255,255,255,0.5)",
  UNDER_CAP: SIG,
};

function fmtM(n: number) { return `$${n.toFixed(1)}M`; }
function fmtDelta(n: number) {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}M`;
}
function fmtTs(iso: string) {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/Los_Angeles", hour12: false }) + " PT";
  } catch { return "--"; }
}
function now_ts() {
  return new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZone: "America/Los_Angeles", hour12: false }) + " PT";
}

// ── Payroll bar chart ─────────────────────────────────────────────────────────

function PayrollChart({ years, onHover }: { years: PayrollYear[]; onHover: (y: PayrollYear | null) => void }) {
  const max = Math.max(...years.map((y) => y.total), 220);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 8, height: 80 }}>
      {years.map((y) => {
        const h = Math.round((y.total / max) * 76);
        const col = TIER_COLOR[y.tier_at_eoy];
        return (
          <div
            key={y.season}
            style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, cursor: "default" }}
            onMouseEnter={() => onHover(y)}
            onMouseLeave={() => onHover(null)}
          >
            <div style={{ ...MONO_B, fontSize: 9, color: "rgba(255,255,255,0.5)" }}>{fmtM(y.total)}</div>
            <div style={{ width: "100%", height: h, background: col, opacity: 0.75, borderRadius: "3px 3px 0 0", transition: "opacity 120ms ease" }} />
            <div style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.4)" }}>{y.season}</div>
          </div>
        );
      })}
    </div>
  );
}

// ── Window scatter ────────────────────────────────────────────────────────────

function WindowScatter({ players }: { players: RosterPlayer[] }) {
  const W = 280, H = 100;
  const minAge = 20, maxAge = 38;
  const xOf = (age: number) => ((age - minAge) / (maxAge - minAge)) * W;
  const yOf = (ms: number) => H - 8 - ms * (H - 16);
  const ROLE_SIZE: Record<string, number> = { STAR: 7, STARTER: 5, ROTATION: 4, BENCH: 3 };
  const champLow = xOf(27), champHigh = xOf(30);
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block", overflow: "visible" }}>
      {/* Championship age band */}
      <rect x={champLow} y={0} width={champHigh - champLow} height={H} fill={`${SIG}14`} />
      {/* Age axis ticks */}
      {[22, 25, 28, 31, 34, 37].map((age) => (
        <g key={age}>
          <line x1={xOf(age)} y1={H - 2} x2={xOf(age)} y2={H} stroke="rgba(255,255,255,0.15)" strokeWidth="1" />
          <text x={xOf(age)} y={H + 9} textAnchor="middle" fill="rgba(255,255,255,0.3)" fontSize="7" fontFamily="JetBrains Mono, monospace">{age}</text>
        </g>
      ))}
      {/* Players */}
      {players.map((p) => (
        <g key={p.name}>
          <circle cx={xOf(p.age)} cy={yOf(p.minute_share)} r={ROLE_SIZE[p.role_tier] ?? 3} fill={p.role_tier === "STAR" ? SIG : "rgba(255,255,255,0.55)"} opacity={0.85} />
          {p.role_tier === "STAR" && (
            <text x={xOf(p.age)} y={yOf(p.minute_share) - 9} textAnchor="middle" fill="rgba(255,255,255,0.7)" fontSize="7" fontFamily="Helvetica Neue, sans-serif" fontWeight="700">{p.name.split(" ").pop()}</text>
          )}
        </g>
      ))}
    </svg>
  );
}

// ── Shimmer animation ─────────────────────────────────────────────────────────

function Shimmer({ children, active }: { children: React.ReactNode; active: boolean }) {
  return (
    <div style={{ position: "relative", overflow: "hidden" }}>
      {active && (
        <div
          style={{
            position: "absolute", inset: 0, zIndex: 10,
            background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.06) 50%, transparent 100%)",
            backgroundSize: "200% 100%",
            animation: "pivot-shimmer 1.2s linear infinite",
          }}
        />
      )}
      <div style={{ opacity: active ? 0.35 : 1, transition: "opacity 200ms" }}>{children}</div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function WarRoom() {
  const router = useRouter();
  const searchRef = useRef<HTMLInputElement>(null);

  const [activeTab, setActiveTab] = useState<"TEAMS" | "FRONT OFFICE" | "CAP TABLE">("TEAMS");
  const [activeTeam, setActiveTeam] = useState("BOS");
  const [franchise, setFranchise] = useState<FranchiseData | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailVisible, setDetailVisible] = useState(true);
  const [railSearch, setRailSearch] = useState("");
  const [railSort, setRailSort] = useState<"tier" | "payroll" | "record" | "az">("tier");
  const [hoveredPayrollYear, setHoveredPayrollYear] = useState<PayrollYear | null>(null);
  const [expandedExpiring, setExpandedExpiring] = useState<string | null>(null);
  const [hoveredChip, setHoveredChip] = useState<string | null>(null);
  const [hoveredMatch, setHoveredMatch] = useState<string | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [pinnedTeams, setPinnedTeams] = useState<string[]>([]);
  const [compareMode, setCompareMode] = useState(false);
  const [compareTeam, setCompareTeam] = useState<string | null>(null);
  const [railKeyIdx, setRailKeyIdx] = useState(0);
  const [timestamp, setTimestamp] = useState("");

  useEffect(() => { setTimestamp(now_ts()); }, []);

  // Load franchise data (real: fetch /api/franchise/:abbr)
  const loadFranchise = useCallback((abbr: string) => {
    setDetailVisible(false);
    setLoading(true);
    setTimeout(() => {
      setFranchise(FRANCHISE_STUBS[abbr] || null);
      setLoading(false);
      setTimeout(() => setDetailVisible(true), 60);
    }, 120);
  }, []);

  useEffect(() => { loadFranchise(activeTeam); }, [activeTeam, loadFranchise]);

  // ⌘K
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") { e.preventDefault(); searchRef.current?.focus(); }
      if (e.key === "p" || e.key === "P") {
        setPinnedTeams((prev) => prev.includes(activeTeam) ? prev.filter((t) => t !== activeTeam) : [...prev, activeTeam]);
      }
      if (e.key === "c" || e.key === "C") { setCompareMode((v) => !v); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [activeTeam]);

  // Rail list
  const filteredRail = RAIL_DATA
    .filter((t) => !railSearch || t.abbr.toLowerCase().includes(railSearch.toLowerCase()) || t.name.toLowerCase().includes(railSearch.toLowerCase()))
    .sort((a, b) => {
      if (railSort === "payroll") return b.payroll - a.payroll;
      if (railSort === "record") return parseInt(b.record) - parseInt(a.record);
      if (railSort === "az") return a.name.localeCompare(b.name);
      const tOrder = ["APRON_2", "APRON_1", "OVER_TAX", "OVER_CAP", "UNDER_CAP"];
      return tOrder.indexOf(a.tier) - tOrder.indexOf(b.tier);
    });

  const regenBriefing = useCallback(async () => {
    if (!franchise || briefingLoading) return;
    setBriefingLoading(true);
    // Real: POST /api/franchise/:abbr/brief
    await new Promise((r) => setTimeout(r, 1800));
    setFranchise((prev) => prev ? ({
      ...prev,
      ai_briefing: { ...prev.ai_briefing, generated_at: new Date().toISOString() },
    }) : prev);
    setBriefingLoading(false);
  }, [franchise, briefingLoading]);

  const f = franchise;

  const TIER_ORDER = ["APRON_2", "APRON_1", "OVER_TAX", "OVER_CAP", "UNDER_CAP"];
  const groupedRail = railSort === "tier"
    ? TIER_GROUPS.map((g) => ({ ...g, teams: filteredRail.filter((t) => t.tier === g.tier) })).filter((g) => g.teams.length > 0)
    : [{ tier: "ALL" as CapTier, label: "ALL TEAMS", teams: filteredRail }];

  return (
    <>
      <style>{`
        @keyframes pivot-shimmer {
          0%   { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
        .wr-root { background: #000; min-height: 100vh; color: #fff; display: flex; flex-direction: column; }
        .wr-root * { box-sizing: border-box; }
        .wr-rail-row:hover { background: rgba(255,255,255,0.06) !important; }
        .wr-chip-row:hover { background: rgba(255,255,255,0.04); }
        .wr-action-btn:hover { background: rgba(255,255,255,0.08) !important; border-color: rgba(255,255,255,0.18) !important; }
        .wr-tab:hover { color: rgba(255,255,255,0.75) !important; }
        .wr-top10-row:hover { background: rgba(255,255,255,0.04); border-left: 2px solid ${SIG} !important; }
        @media (max-width: 768px) {
          .wr-main-layout { flex-direction: column !important; }
          .wr-rail { width: 100% !important; max-height: 220px; flex-shrink: 0; }
          .wr-detail { width: 100% !important; }
        }
      `}</style>

      <div className="wr-root" style={{ ...HB }}>

        {/* TOP BAR */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 24px", flexShrink: 0 }}>
          <span style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.6)", letterSpacing: "1px" }}>
            PIVOT/TERM&nbsp;&nbsp;WAR ROOM &middot; FRANCHISE
          </span>
          <span style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.6)" }}>
            {new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }).toUpperCase()}
          </span>
        </div>

        {/* TAB ROW */}
        <div style={{ display: "flex", gap: 0, padding: "0 24px", borderBottom: "0.5px solid rgba(255,255,255,0.08)", flexShrink: 0 }}>
          {(["TEAMS", "FRONT OFFICE", "CAP TABLE"] as const).map((tab) => {
            const active = activeTab === tab;
            return (
              <button
                key={tab}
                className="wr-tab"
                onClick={() => setActiveTab(tab)}
                style={{
                  ...MONO, fontSize: 11, letterSpacing: "1px", padding: "10px 16px", background: "transparent", border: "none",
                  borderBottom: active ? `2px solid ${SIG}` : "2px solid transparent",
                  color: active ? "#fff" : "rgba(255,255,255,0.5)", cursor: "pointer", transition: "color 120ms",
                  marginBottom: -1,
                }}
              >
                {tab}
              </button>
            );
          })}
        </div>

        {/* MAIN LAYOUT */}
        <div className="wr-main-layout" style={{ display: "flex", flex: 1, overflow: "hidden", minHeight: 0 }}>

          {/* RAIL */}
          <div className="wr-rail" style={{ width: 210, flexShrink: 0, borderRight: "0.5px solid rgba(255,255,255,0.07)", display: "flex", flexDirection: "column", overflow: "hidden" }}>

            {/* Rail search */}
            <div style={{ padding: "12px 12px 8px" }}>
              <div style={{ ...GLASS_PILL, display: "flex", alignItems: "center", height: 34, padding: "0 14px", gap: 8 }}>
                <svg width="13" height="13" viewBox="0 0 13 13" fill="none" style={{ flexShrink: 0 }}>
                  <circle cx="5.5" cy="5.5" r="4" stroke="rgba(255,255,255,0.35)" strokeWidth="1.2" />
                  <path d="M9 9L12 12" stroke="rgba(255,255,255,0.35)" strokeWidth="1.2" strokeLinecap="round" />
                </svg>
                <input
                  ref={searchRef}
                  value={railSearch}
                  onChange={(e) => setRailSearch(e.target.value)}
                  placeholder="Search team or GM"
                  style={{ ...MONO, fontSize: 11, background: "transparent", border: "none", outline: "none", color: "#fff", width: "100%" }}
                />
              </div>
            </div>

            {/* Sort */}
            <div style={{ padding: "0 12px 8px" }}>
              <select
                value={railSort}
                onChange={(e) => setRailSort(e.target.value as typeof railSort)}
                style={{ ...MONO, fontSize: 10, background: "rgba(255,255,255,0.04)", border: "0.5px solid rgba(255,255,255,0.08)", borderRadius: 6, color: "rgba(255,255,255,0.6)", padding: "5px 8px", width: "100%", outline: "none" }}
              >
                <option value="tier">By tier</option>
                <option value="payroll">By payroll</option>
                <option value="record">By record</option>
                <option value="az">A to Z</option>
              </select>
            </div>

            {/* Pinned strip */}
            {pinnedTeams.length > 0 && (
              <div style={{ padding: "0 12px 8px", display: "flex", gap: 4, flexWrap: "wrap" }}>
                {pinnedTeams.map((t) => (
                  <span
                    key={t}
                    onClick={() => setActiveTeam(t)}
                    style={{ ...MONO_B, fontSize: 9, padding: "2px 7px", background: `${SIG}18`, border: `0.5px solid ${SIG}44`, borderRadius: 999, color: SIG, cursor: "pointer", letterSpacing: "0.5px" }}
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}

            {/* Team list */}
            <div style={{ flex: 1, overflowY: "auto" }}>
              {groupedRail.map((group) => (
                <div key={group.tier}>
                  {railSort === "tier" && (
                    <div style={{ ...MONO, fontSize: 9, letterSpacing: "1.2px", color: "rgba(255,255,255,0.3)", padding: "10px 14px 4px" }}>{group.label}</div>
                  )}
                  {group.teams.map((team) => {
                    const active = team.abbr === activeTeam;
                    return (
                      <div
                        key={team.abbr}
                        className="wr-rail-row"
                        onClick={() => setActiveTeam(team.abbr)}
                        style={{
                          display: "flex", alignItems: "center", padding: "8px 14px", cursor: "pointer", gap: 8,
                          background: active ? "rgba(255,255,255,0.05)" : "transparent",
                          borderLeft: active ? `2px solid ${SIG}` : "2px solid transparent",
                          transition: "background 120ms, border-color 120ms",
                        }}
                      >
                        <div style={{ width: 6, height: 6, borderRadius: "50%", background: TIER_COLOR[team.tier], flexShrink: 0 }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <span style={{ ...HB, fontSize: 12, color: active ? "#fff" : "rgba(255,255,255,0.85)" }}>{team.abbr}</span>
                            <span style={{ ...MONO_B, fontSize: 11, color: active ? "#fff" : "rgba(255,255,255,0.7)" }}>{fmtM(team.payroll)}</span>
                          </div>
                          <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.6)", marginTop: 1 }}>{team.record}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          {/* DETAIL PANE */}
          <div
            className="wr-detail"
            style={{
              flex: 1, overflowY: "auto", padding: "20px 28px 80px",
              opacity: detailVisible ? 1 : 0, transition: "opacity 120ms ease",
            }}
          >
            {loading || !f ? (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 200 }}>
                <span style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", letterSpacing: "1px" }}>LOADING...</span>
              </div>
            ) : (
              <div style={{ maxWidth: compareMode ? "100%" : 900 }}>

                {/* FRANCHISE HEADER */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 28 }}>
                  <div>
                    <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)", letterSpacing: "1.4px", marginBottom: 6 }}>FRANCHISE</div>
                    <div style={{ ...HB, fontSize: 26, color: "#fff", letterSpacing: "-0.3px", lineHeight: 1 }}>{f.team.name}</div>
                    <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.6)", marginTop: 6, display: "flex", gap: 12 }}>
                      <span>{f.team.record} &middot; {f.team.conference_seed}</span>
                      <span>HC {f.team.head_coach}</span>
                      <span>GM {f.team.gm}</span>
                    </div>
                  </div>

                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <div style={{ ...MONO_B, fontSize: 26, color: "#fff" }}>{fmtM(f.cap.payroll)}</div>
                      <span style={{ ...MONO, fontSize: 10, letterSpacing: "1px", padding: "3px 10px", borderRadius: 999, background: f.cap.tier === "APRON_2" ? `${CORAL}1e` : f.cap.tier === "UNDER_CAP" ? `${SIG}1e` : "rgba(255,255,255,0.06)", border: `0.5px solid ${f.cap.tier === "APRON_2" ? CORAL : f.cap.tier === "UNDER_CAP" ? SIG : "rgba(255,255,255,0.15)"}`, color: f.cap.tier === "APRON_2" ? CORAL : f.cap.tier === "UNDER_CAP" ? SIG : "rgba(255,255,255,0.8)" }}>
                        {f.cap.tier.replace("_", " ")}
                      </span>
                    </div>

                    {/* Restriction / tool pills */}
                    <div style={{ display: "flex", gap: 5, flexWrap: "wrap", justifyContent: "flex-end", maxWidth: 340 }}>
                      {f.cap.restrictions.map((r) => (
                        <span key={r} style={{ ...MONO, fontSize: 9, letterSpacing: "0.8px", padding: "2px 8px", borderRadius: 999, background: `${CORAL}18`, border: `0.5px solid ${CORAL}55`, color: CORAL }}>{r}</span>
                      ))}
                      {f.cap.tools.map((t) => (
                        <span key={t} style={{ ...MONO, fontSize: 9, letterSpacing: "0.8px", padding: "2px 8px", borderRadius: 999, background: `${SIG}18`, border: `0.5px solid ${SIG}55`, color: SIG }}>{t}</span>
                      ))}
                    </div>

                    {/* Pin / Compare */}
                    <div style={{ display: "flex", gap: 6 }}>
                      {[
                        { label: pinnedTeams.includes(f.team.abbr) ? "UNPIN" : "PIN (P)", action: () => setPinnedTeams((p) => p.includes(f.team.abbr) ? p.filter((x) => x !== f.team.abbr) : [...p, f.team.abbr]) },
                        { label: compareMode ? "EXIT COMPARE" : "COMPARE (C)", action: () => setCompareMode((v) => !v) },
                      ].map(({ label, action }) => (
                        <button key={label} onClick={action} style={{ ...MONO, fontSize: 9, letterSpacing: "0.8px", padding: "4px 10px", background: "transparent", border: "0.5px solid rgba(255,255,255,0.12)", borderRadius: 6, color: "rgba(255,255,255,0.55)", cursor: "pointer" }}>{label}</button>
                      ))}
                    </div>
                  </div>
                </div>

                {/* 4-PANEL GRID */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>

                  {/* Panel 1: Payroll trajectory */}
                  <div style={{ ...GLASS, padding: 20 }}>
                    <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.4)", marginBottom: 14 }}>PAYROLL TRAJECTORY</div>
                    {f.payroll_by_year.length > 0 ? (
                      <>
                        <PayrollChart years={f.payroll_by_year} onHover={setHoveredPayrollYear} />
                        {hoveredPayrollYear ? (
                          <div style={{ ...GLASS_SM, marginTop: 10, padding: "8px 12px" }}>
                            <div style={{ ...MONO_B, fontSize: 10, color: "rgba(255,255,255,0.6)", marginBottom: 4 }}>{hoveredPayrollYear.season}</div>
                            {hoveredPayrollYear.top_contracts.slice(0, 3).map((c) => (
                              <div key={c.player} style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                                <span style={{ ...HB, fontSize: 11 }}>{c.player}</span>
                                <span style={{ ...MONO_B, fontSize: 11, color: "rgba(255,255,255,0.7)" }}>{fmtM(c.amount)} <span style={{ color: "rgba(255,255,255,0.4)", fontWeight: 400 }}>{c.type}</span></span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div style={{ marginTop: 12, ...MONO, fontSize: 10, color: "rgba(255,255,255,0.5)", lineHeight: 1.5 }}>
                            {f.payroll_by_year.length >= 5 && (() => {
                              const last = f.payroll_by_year[f.payroll_by_year.length - 1];
                              const prev = f.payroll_by_year[f.payroll_by_year.length - 2];
                              if (last.total < prev.total - 20) {
                                return `Cliff in ${last.season} when contracts expire, ${fmtDelta(last.total - prev.total)} relief.`;
                              }
                              return `Payroll peaks at ${fmtM(Math.max(...f.payroll_by_year.map(y => y.total)))} in ${f.payroll_by_year.reduce((a, b) => a.total > b.total ? a : b).season}. Hover bars for contract detail.`;
                            })()}
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", padding: "20px 0" }}>Contract data pipeline not connected for this team.</div>
                    )}
                  </div>

                  {/* Panel 2: Window analyzer */}
                  <div style={{ ...GLASS, padding: 20 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                      <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.4)" }}>WINDOW ANALYZER</div>
                      <span style={{ ...MONO, fontSize: 9, letterSpacing: "0.8px", padding: "2px 10px", borderRadius: 999, background: f.roster_aging.window_classification === "open" ? `${SIG}18` : f.roster_aging.window_classification === "closing" ? `${CORAL}18` : "rgba(255,255,255,0.06)", border: `0.5px solid ${f.roster_aging.window_classification === "open" ? SIG : f.roster_aging.window_classification === "closing" ? CORAL : "rgba(255,255,255,0.2)"}`, color: f.roster_aging.window_classification === "open" ? SIG : f.roster_aging.window_classification === "closing" ? CORAL : "rgba(255,255,255,0.7)" }}>
                        {f.roster_aging.window_classification.toUpperCase()}
                      </span>
                    </div>
                    {f.roster_aging.players.length > 0 ? (
                      <>
                        <WindowScatter players={f.roster_aging.players} />
                        <div style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.3)", marginTop: 6 }}>Age &rarr;&nbsp; Signal band = champ range (27-30)</div>
                        <div style={{ marginTop: 10, ...MONO, fontSize: 10, color: "rgba(255,255,255,0.6)", lineHeight: 1.5 }}>{f.roster_aging.window_reasoning}</div>
                      </>
                    ) : (
                      <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", padding: "20px 0" }}>Roster aging data not connected for this team.</div>
                    )}
                  </div>

                  {/* Panel 3: Expiring decisions */}
                  <div style={{ ...GLASS, padding: 20 }}>
                    <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.4)", marginBottom: 14 }}>EXPIRING DECISIONS</div>
                    {f.expiring.length > 0 ? (
                      <>
                        {f.expiring.map((ex) => {
                          const expanded = expandedExpiring === ex.player;
                          const REC_STYLE: Record<string, React.CSSProperties> = {
                            KEEP:      { background: `${SIG}18`,      border: `0.5px solid ${SIG}55`,         color: SIG },
                            REPLACE:   { background: "rgba(255,255,255,0.06)", border: "0.5px solid rgba(255,255,255,0.15)", color: "rgba(255,255,255,0.7)" },
                            LET_WALK:  { background: `${CORAL}18`,    border: `0.5px solid ${CORAL}55`,       color: CORAL },
                          };
                          return (
                            <div key={ex.player}>
                              <div
                                onClick={() => setExpandedExpiring(expanded ? null : ex.player)}
                                style={{ display: "flex", alignItems: "center", padding: "9px 0", borderBottom: "0.5px solid rgba(255,255,255,0.05)", cursor: "pointer", gap: 10 }}
                              >
                                <div style={{ flex: 1 }}>
                                  <span style={{ ...HB, fontSize: 13, color: "#fff" }}>{ex.player}</span>
                                  <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.5)", marginLeft: 6 }}>{ex.position}</span>
                                </div>
                                <div style={{ ...MONO_B, fontSize: 11, color: "rgba(255,255,255,0.7)" }}>{fmtM(ex.cap_hit)}</div>
                                <span style={{ ...MONO, fontSize: 9, letterSpacing: "0.6px", padding: "2px 8px", borderRadius: 999, ...REC_STYLE[ex.retain_recommendation] }}>{ex.retain_recommendation.replace("_", " ")}</span>
                              </div>
                              {expanded && (
                                <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.55)", padding: "8px 0 10px", lineHeight: 1.55 }}>{ex.reasoning}</div>
                              )}
                            </div>
                          );
                        })}
                        <div style={{ marginTop: 10, display: "flex", justifyContent: "space-between" }}>
                          <span style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.35)" }}>
                            {f.expiring.length} expiring &middot; {fmtM(f.expiring.reduce((s, e) => s + e.cap_hit, 0))} total
                          </span>
                          <span style={{ ...MONO, fontSize: 9, color: SIG }}>
                            {fmtM(f.expiring.filter((e) => e.retain_recommendation !== "KEEP").reduce((s, e) => s + e.cap_hit, 0))} potential relief
                          </span>
                        </div>
                      </>
                    ) : (
                      <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", padding: "20px 0" }}>Expiring contract data not connected for this team.</div>
                    )}
                  </div>

                  {/* Panel 4: Trade chips + matches */}
                  <div style={{ ...GLASS, padding: 20 }}>
                    <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.4)", marginBottom: 14 }}>TRADE CHIPS</div>

                    {f.trade_chips.length > 0 ? (
                      <>
                        {f.trade_chips.map((chip) => (
                          <div
                            key={chip.player}
                            className="wr-chip-row"
                            onMouseEnter={() => setHoveredChip(chip.player)}
                            onMouseLeave={() => setHoveredChip(null)}
                            style={{ display: "flex", alignItems: "center", padding: "8px 6px", borderBottom: "0.5px solid rgba(255,255,255,0.05)", gap: 8, borderRadius: 4, transition: "background 80ms" }}
                          >
                            <div style={{ flex: 1 }}>
                              <div style={{ ...HB, fontSize: 13 }}>{chip.player}</div>
                              <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.5)", marginTop: 1 }}>{chip.position} &middot; age {chip.age} &middot; {chip.years_remaining}yr &middot; {fmtM(chip.cap_hit)}</div>
                            </div>
                            <div style={{ ...MONO_B, fontSize: 13, color: chip.movability_score >= 7 ? SIG : chip.movability_score >= 5 ? "rgba(255,255,255,0.8)" : CORAL }}>{chip.movability_score}/10</div>
                          </div>
                        ))}
                        {hoveredChip && (() => {
                          const chip = f.trade_chips.find((c) => c.player === hoveredChip);
                          if (!chip || !chip.comparable_trades.length) return null;
                          return (
                            <div style={{ ...MONO, fontSize: 9, color: "rgba(255,255,255,0.4)", padding: "6px 0 0", lineHeight: 1.6 }}>
                              {chip.comparable_trades[0].year} comp: {chip.comparable_trades[0].partner} received {chip.comparable_trades[0].players_received}
                            </div>
                          );
                        })()}

                        <div style={{ marginTop: 14, paddingTop: 12, borderTop: "0.5px solid rgba(255,255,255,0.07)" }}>
                          <div style={{ ...MONO, fontSize: 10, letterSpacing: "1.2px", color: "rgba(255,255,255,0.4)", marginBottom: 10 }}>MATCHES</div>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                            {f.trade_matches.map((m) => (
                              <div key={m.partner_abbr} style={{ position: "relative" }}>
                                <div
                                  onMouseEnter={() => setHoveredMatch(m.partner_abbr)}
                                  onMouseLeave={() => setHoveredMatch(null)}
                                  style={{ ...MONO_B, fontSize: 11, padding: "5px 12px", borderRadius: 999, background: "rgba(255,255,255,0.06)", border: "0.5px solid rgba(255,255,255,0.12)", color: "#fff", cursor: "default", letterSpacing: "0.5px" }}
                                >
                                  {m.partner_abbr}
                                </div>
                                {hoveredMatch === m.partner_abbr && (
                                  <div style={{ position: "absolute", bottom: "calc(100% + 6px)", left: 0, zIndex: 20, ...GLASS_SM, padding: "8px 12px", minWidth: 220, maxWidth: 280 }}>
                                    <div style={{ ...HB, fontSize: 11, marginBottom: 4 }}>{m.reason}</div>
                                    <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.6)", lineHeight: 1.5 }}>{m.concept}</div>
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      </>
                    ) : (
                      <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", padding: "20px 0" }}>Trade chip valuator not connected for this team.</div>
                    )}
                  </div>
                </div>

                {/* AI BRIEFING */}
                <div style={{ ...GLASS, padding: "22px 24px", marginBottom: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                    <div style={{ ...MONO, fontSize: 11, color: SIG, letterSpacing: "1.4px" }}>PIVOT BRIEFING</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)" }}>{fmtTs(f.ai_briefing.generated_at)}</span>
                      <button
                        onClick={regenBriefing}
                        disabled={briefingLoading}
                        style={{ ...MONO, fontSize: 10, letterSpacing: "0.6px", display: "flex", alignItems: "center", gap: 5, padding: "4px 10px", background: "transparent", border: "0.5px solid rgba(255,255,255,0.12)", borderRadius: 6, color: briefingLoading ? "rgba(255,255,255,0.3)" : "rgba(255,255,255,0.6)", cursor: briefingLoading ? "default" : "pointer" }}
                      >
                        &#x21bb; REGENERATE
                      </button>
                    </div>
                  </div>

                  <Shimmer active={briefingLoading}>
                    <div style={{ ...HB, fontSize: 18, color: "#fff", marginBottom: 12, lineHeight: 1.3 }}>{f.ai_briefing.headline}</div>
                    <div style={{ ...HB, fontSize: 14, color: "rgba(255,255,255,0.82)", lineHeight: 1.65, marginBottom: 16 }}>{f.ai_briefing.body}</div>
                    <div style={{ borderLeft: `2px solid ${SIG}`, paddingLeft: 14 }}>
                      <div style={{ ...MONO, fontSize: 9, color: SIG, letterSpacing: "1.2px", marginBottom: 5 }}>DECISION</div>
                      <div style={{ ...HB, fontSize: 13, color: "#fff", lineHeight: 1.55 }}>{f.ai_briefing.pressing_decision}</div>
                    </div>
                  </Shimmer>
                </div>

                {/* ACTION ROW */}
                <div style={{ display: "flex", gap: 8 }}>
                  {[
                    { label: "Trade scenario \u2197", prompt: `Build a trade for ${f.team.abbr} under ${f.cap.restrictions.join(", ") || "no apron restrictions"}` },
                    { label: "FA targets \u2197",     prompt: `Find 5 FA targets for ${f.team.abbr} within ${f.cap.tools.join(", ") || "over-cap constraints"}` },
                    { label: "Full report \u2197",    prompt: `Generate full PIVOT report on ${f.team.name}` },
                  ].map(({ label, prompt }) => (
                    <button
                      key={label}
                      className="wr-action-btn"
                      onClick={() => router.push(`/chat?prompt=${encodeURIComponent(prompt)}`)}
                      style={{ ...MONO, fontSize: 11, letterSpacing: "0.6px", padding: "10px 18px", background: "rgba(255,255,255,0.03)", border: "0.5px solid rgba(255,255,255,0.1)", borderRadius: 8, color: "rgba(255,255,255,0.7)", cursor: "pointer", transition: "background 120ms, border-color 120ms" }}
                    >
                      {label}
                    </button>
                  ))}
                </div>

              </div>
            )}
          </div>
        </div>

        {/* BOTTOM BAR */}
        <div
          style={{
            position: "fixed", bottom: 0, left: 0, right: 0,
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "10px 24px",
            background: "rgba(255,255,255,0.04)",
            backdropFilter: "blur(24px) saturate(180%)",
            WebkitBackdropFilter: "blur(24px) saturate(180%)",
            borderTop: "0.5px solid rgba(255,255,255,0.07)",
            zIndex: 50,
          }}
        >
          <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)", letterSpacing: "0.04em" }}>
            &#x2191;&#x2193; NAVIGATE TEAMS&nbsp;&nbsp;&middot;&nbsp;&nbsp;&#x23CE; SELECT&nbsp;&nbsp;&middot;&nbsp;&nbsp;P PIN&nbsp;&nbsp;&middot;&nbsp;&nbsp;C COMPARE&nbsp;&nbsp;&middot;&nbsp;&nbsp;&#x2318;K SEARCH
          </span>
          <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)" }}>
            {activeTeam} &middot; UPD {timestamp}
          </span>
        </div>
      </div>
    </>
  );
}
