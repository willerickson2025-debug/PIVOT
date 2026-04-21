"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  RAIL_DATA,
  CBA,
  SIG,
  TIER_LABEL,
  TIER_COLOR,
  fmtM,
} from "../_lib/data";
import type { RailTeam, CapTier } from "../_lib/data";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
  fontWeight: 700,
};

const CONFERENCES = ["All", "East", "West"] as const;
const DIVISIONS = [
  "All",
  "Atlantic",
  "Central",
  "Southeast",
  "Northwest",
  "Pacific",
  "Southwest",
] as const;

const TIERS: { value: CapTier | "all"; label: string }[] = [
  { value: "all", label: "ALL TIERS" },
  { value: "over2", label: "APRON 2+" },
  { value: "apron2", label: "APRON 1" },
  { value: "apron1", label: "LUX TAX" },
  { value: "tax", label: "OVER CAP" },
  { value: "under", label: "CAP SPACE" },
];

export default function LeaguePage() {
  const router = useRouter();
  const [conf, setConf] = useState<"All" | "East" | "West">("All");
  const [div, setDiv] = useState<typeof DIVISIONS[number]>("All");
  const [tier, setTier] = useState<CapTier | "all">("all");
  const [sort, setSort] = useState<"payroll" | "wins" | "name">("payroll");
  const [dir, setDir] = useState<"asc" | "desc">("desc");

  const visibleDivs = DIVISIONS.filter((d) =>
    d === "All" ||
    conf === "All" ||
    (conf === "East"
      ? ["Atlantic", "Central", "Southeast"].includes(d)
      : ["Northwest", "Pacific", "Southwest"].includes(d))
  );

  const filtered = RAIL_DATA.filter((t) => {
    if (conf !== "All" && t.conf !== conf) return false;
    if (div !== "All" && t.div !== div) return false;
    if (tier !== "all" && t.tier !== tier) return false;
    return true;
  }).sort((a, b) => {
    let av = 0;
    let bv = 0;
    if (sort === "payroll") { av = a.payroll; bv = b.payroll; }
    else if (sort === "wins") { av = a.wins ?? 0; bv = b.wins ?? 0; }
    else { return dir === "asc" ? a.name.localeCompare(b.name) : b.name.localeCompare(a.name); }
    return dir === "desc" ? bv - av : av - bv;
  });

  function toggleSort(col: "payroll" | "wins" | "name") {
    if (sort === col) {
      setDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSort(col);
      setDir("desc");
    }
  }

  const capLine = CBA.CAP;
  const maxPayroll = Math.max(...RAIL_DATA.map((t) => t.payroll));

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        color: "#fff",
        padding: "32px 24px 64px",
        boxSizing: "border-box",
      }}
    >
      <div style={{ maxWidth: 1200, margin: "0 auto" }}>

        {/* Back */}
        <button
          onClick={() => router.push("/war-room")}
          style={{
            ...HB,
            background: "none",
            border: "none",
            color: "rgba(255,255,255,0.4)",
            cursor: "pointer",
            fontSize: 12,
            letterSpacing: "0.12em",
            padding: 0,
            marginBottom: 28,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {"\u2190"} WAR ROOM
        </button>

        {/* Header */}
        <div style={{ marginBottom: 32 }}>
          <p style={{ ...HB, fontSize: 11, letterSpacing: "0.2em", color: SIG, margin: "0 0 8px" }}>
            LEAGUE VIEW
          </p>
          <h1 style={{ ...HB, fontSize: "clamp(24px, 3vw, 40px)", margin: "0 0 4px", textTransform: "uppercase" }}>
            PAYROLL STANDINGS
          </h1>
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
            {filtered.length} of 30 teams shown
          </p>
        </div>

        {/* Filters */}
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 24 }}>
          {/* Row 1: Conference + Tier */}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {CONFERENCES.map((c) => (
              <FilterPill key={c} label={c} active={conf === c} onClick={() => { setConf(c as typeof conf); setDiv("All"); }} />
            ))}
            <div style={{ width: 1, background: "rgba(255,255,255,0.1)", margin: "0 4px" }} />
            {TIERS.map((t) => (
              <FilterPill key={t.value} label={t.label} active={tier === t.value} onClick={() => setTier(t.value)} />
            ))}
          </div>
          {/* Row 2: Division */}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {visibleDivs.map((d) => (
              <FilterPill key={d} label={d === "All" ? "ALL DIVS" : d.toUpperCase()} active={div === d} onClick={() => setDiv(d)} />
            ))}
          </div>
        </div>

        {/* Sort row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "40px 2fr 1fr 1fr 1fr 80px",
            gap: 8,
            padding: "8px 16px",
            marginBottom: 6,
          }}
        >
          {(["#", "TEAM", "PAYROLL", "W-L", "TIER", "BAR"] as const).map((col, i) => {
            const sortKey = col === "PAYROLL" ? "payroll" : col === "W-L" ? "wins" : col === "TEAM" ? "name" : null;
            return (
              <button
                key={col}
                onClick={sortKey ? () => toggleSort(sortKey as typeof sort) : undefined}
                style={{
                  ...HB,
                  background: "none",
                  border: "none",
                  color: sortKey
                    ? (sort === sortKey ? SIG : "rgba(255,255,255,0.3)")
                    : "rgba(255,255,255,0.2)",
                  cursor: sortKey ? "pointer" : "default",
                  fontSize: 9,
                  letterSpacing: "0.14em",
                  padding: 0,
                  textAlign: i === 0 ? "center" : "left",
                }}
              >
                {col}{sort === sortKey ? (dir === "desc" ? " \u2193" : " \u2191") : ""}
              </button>
            );
          })}
        </div>

        {/* Rows */}
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {filtered.map((team, idx) => (
            <TeamRow key={team.abbr} team={team} rank={idx + 1} maxPayroll={maxPayroll} capLine={capLine} />
          ))}
        </div>

        {/* Legend */}
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 32, paddingTop: 24, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
          {([
            ["over2", "APRON 2+", `$${CBA.APRON_2}M+`],
            ["apron2", "APRON 1", `$${CBA.APRON_1}M`],
            ["apron1", "LUX TAX", `$${CBA.LUXURY_TAX}M`],
            ["tax", "OVER CAP", `$${CBA.CAP}M`],
            ["under", "UNDER", `<$${CBA.CAP}M`],
          ] as [CapTier, string, string][]).map(([t, label, threshold]) => (
            <div key={t} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: TIER_COLOR[t] }} />
              <span style={{ ...HB, fontSize: 9, letterSpacing: "0.12em", color: "rgba(255,255,255,0.4)" }}>
                {label}
              </span>
              <span style={{ fontFamily: '"JetBrains Mono",monospace', fontSize: 9, color: "rgba(255,255,255,0.2)" }}>
                {threshold}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TeamRow({
  team,
  rank,
  maxPayroll,
  capLine,
}: {
  team: RailTeam;
  rank: number;
  maxPayroll: number;
  capLine: number;
}) {
  const HBLocal: React.CSSProperties = {
    fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
    fontWeight: 700,
  };
  const barPct = (team.payroll / maxPayroll) * 100;
  const capPct = (capLine / maxPayroll) * 100;
  const color = TIER_COLOR[team.tier];

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "40px 2fr 1fr 1fr 1fr 80px",
        gap: 8,
        padding: "12px 16px",
        background: "rgba(255,255,255,0.02)",
        borderRadius: 8,
        border: "1px solid rgba(255,255,255,0.04)",
        alignItems: "center",
      }}
    >
      <span style={{ ...HBLocal, fontSize: 11, color: "rgba(255,255,255,0.25)", textAlign: "center" }}>
        {rank}
      </span>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ ...HBLocal, fontSize: 14, color: "#fff" }}>
          {team.city} {team.name}
        </span>
        <span style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 10, color: "rgba(255,255,255,0.3)", letterSpacing: "0.08em" }}>
          {team.conf.toUpperCase()} | {team.div.toUpperCase()}
        </span>
      </div>
      <span style={{ fontFamily: '"JetBrains Mono",monospace', fontWeight: 700, fontSize: 13, color }}>
        {fmtM(team.payroll)}
      </span>
      <span style={{ fontFamily: '"JetBrains Mono",monospace', fontWeight: 400, fontSize: 12, color: "rgba(255,255,255,0.5)" }}>
        {team.wins ?? 0}-{team.losses ?? 0}
      </span>
      <span
        style={{
          ...HBLocal,
          fontSize: 9,
          letterSpacing: "0.1em",
          color,
          background: `${color}18`,
          border: `1px solid ${color}30`,
          borderRadius: 4,
          padding: "3px 7px",
          display: "inline-block",
        }}
      >
        {TIER_LABEL[team.tier]}
      </span>
      {/* Bar */}
      <div style={{ position: "relative", height: 6, background: "rgba(255,255,255,0.06)", borderRadius: 3, overflow: "hidden" }}>
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            height: "100%",
            width: `${barPct}%`,
            background: color,
            borderRadius: 3,
            opacity: 0.7,
          }}
        />
        {/* Cap line marker */}
        <div
          style={{
            position: "absolute",
            left: `${capPct}%`,
            top: 0,
            width: 1,
            height: "100%",
            background: "rgba(255,255,255,0.4)",
          }}
        />
      </div>
    </div>
  );
}

function FilterPill({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
        fontWeight: 700,
        fontSize: 10,
        letterSpacing: "0.12em",
        background: active ? SIG : "rgba(255,255,255,0.04)",
        border: `1px solid ${active ? SIG : "rgba(255,255,255,0.1)"}`,
        color: active ? "#000" : "rgba(255,255,255,0.5)",
        borderRadius: 6,
        padding: "5px 12px",
        cursor: "pointer",
        transition: "all 120ms",
      }}
    >
      {label}
    </button>
  );
}
