"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RAIL_DATA, CBA, SIG, TIER_COLOR, TIER_LABEL, fmtM, getFranchise } from "../_lib/data";
import type { RosterPlayer, FranchiseData } from "../_lib/data";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
  fontWeight: 700,
};
const MONO: React.CSSProperties = {
  fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace',
  fontWeight: 700,
};

export default function CapTablePage() {
  const router = useRouter();
  const [selectedAbbr, setSelectedAbbr] = useState<string>("OKC");

  const franchise = getFranchise(selectedAbbr);
  const maxSalary = franchise.roster.length > 0
    ? Math.max(...franchise.roster.map((p) => p.salary))
    : 1;
  const tierColor = TIER_COLOR[franchise.tier];

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
      <div style={{ padding: "6px 24px", marginBottom: 0, textAlign: "center", borderBottom: "0.5px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.015)" }}>
        <span style={{ fontFamily: '"JetBrains Mono","Fira Code",monospace', fontSize: 9, color: "rgba(255,255,255,0.25)", letterSpacing: "0.12em" }}>DATA AS OF 2025-26 REGULAR SEASON · UPDATED MANUALLY</span>
      </div>
      <div style={{ maxWidth: 1100, margin: "0 auto" }}>

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
            CAP TABLE
          </p>
          <h1 style={{ ...HB, fontSize: "clamp(24px, 3vw, 40px)", margin: "0 0 4px", textTransform: "uppercase" }}>
            ROSTER SALARIES
          </h1>
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
            Select a team to view contract structure and payroll breakdown.
          </p>
        </div>

        {/* Team selector rail */}
        <div
          style={{
            display: "flex",
            gap: 6,
            flexWrap: "wrap",
            marginBottom: 32,
            paddingBottom: 20,
            borderBottom: "1px solid rgba(255,255,255,0.06)",
          }}
        >
          {RAIL_DATA.map((team) => (
            <button
              key={team.abbr}
              onClick={() => setSelectedAbbr(team.abbr)}
              style={{
                ...HB,
                fontSize: 11,
                letterSpacing: "0.08em",
                background: selectedAbbr === team.abbr ? SIG : "rgba(255,255,255,0.04)",
                border: `1px solid ${selectedAbbr === team.abbr ? SIG : "rgba(255,255,255,0.08)"}`,
                color: selectedAbbr === team.abbr ? "#000" : "rgba(255,255,255,0.5)",
                borderRadius: 6,
                padding: "5px 10px",
                cursor: "pointer",
                transition: "all 100ms",
              }}
            >
              {team.abbr}
            </button>
          ))}
        </div>

        {/* Franchise header */}
        <div
          style={{
            background: "rgba(255,255,255,0.02)",
            border: `1px solid ${tierColor}22`,
            borderRadius: 12,
            padding: "20px 24px",
            marginBottom: 24,
            display: "flex",
            gap: 24,
            flexWrap: "wrap",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <p style={{ ...HB, fontSize: "clamp(18px, 2.5vw, 28px)", color: "#fff", margin: "0 0 4px", textTransform: "uppercase" }}>
              {franchise.city} {franchise.name}
            </p>
            <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
              {franchise.gm} | {franchise.coach} | {franchise.conf}
            </p>
          </div>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <StatBlob label="TOTAL PAYROLL" value={fmtM(franchise.payroll)} color={tierColor} />
            <StatBlob label="CAP" value={fmtM(CBA.CAP)} color="rgba(255,255,255,0.3)" />
            <StatBlob label="STATUS" value={TIER_LABEL[franchise.tier]} color={tierColor} />
            <StatBlob label="RECORD" value={`${franchise.wins}-${franchise.losses}`} color="rgba(255,255,255,0.5)" />
          </div>
        </div>

        {/* Cap summary */}
        {franchise.capsummary !== "Full franchise data coming soon." && (
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.45)", margin: "0 0 28px", lineHeight: 1.6 }}>
            {franchise.capsummary}
          </p>
        )}

        {/* Roster / contract table */}
        {franchise.roster.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {/* Column headers */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "2fr 50px 120px 80px 100px",
                gap: 8,
                padding: "6px 16px",
              }}
            >
              {(["PLAYER", "POS", "SALARY", "YRS", ""] as const).map((col) => (
                <p
                  key={col}
                  style={{
                    ...HB,
                    fontSize: 9,
                    letterSpacing: "0.14em",
                    color: "rgba(255,255,255,0.25)",
                    margin: 0,
                  }}
                >
                  {col}
                </p>
              ))}
            </div>

            {franchise.roster
              .slice()
              .sort((a, b) => b.salary - a.salary)
              .map((player) => (
                <PlayerRow key={player.name} player={player} maxSalary={maxSalary} />
              ))}

            {/* Payroll total */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "2fr 50px 120px 80px 100px",
                gap: 8,
                padding: "12px 16px",
                borderTop: "1px solid rgba(255,255,255,0.08)",
                marginTop: 4,
              }}
            >
              <p style={{ ...HB, fontSize: 12, color: "rgba(255,255,255,0.4)", margin: 0 }}>
                TOTAL
              </p>
              <p style={{ margin: 0 }} />
              <p style={{ fontFamily: '"JetBrains Mono",monospace', fontWeight: 700, fontSize: 14, color: tierColor, margin: 0 }}>
                {fmtM(franchise.roster.reduce((s, p) => s + p.salary, 0))}
              </p>
              <p style={{ margin: 0 }} />
              <p style={{ margin: 0 }} />
            </div>
          </div>
        ) : (
          <div
            style={{
              background: "rgba(255,255,255,0.02)",
              border: "1px solid rgba(255,255,255,0.06)",
              borderRadius: 10,
              padding: 40,
              textAlign: "center",
            }}
          >
            <p style={{ ...HB, fontSize: 13, color: "rgba(255,255,255,0.25)", margin: 0 }}>
              Full roster data not yet available for this franchise.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function StatBlob({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <p style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", margin: "0 0 3px" }}>
        {label}
      </p>
      <p style={{ fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace', fontWeight: 700, fontSize: 16, color, margin: 0 }}>
        {value}
      </p>
    </div>
  );
}

function PlayerRow({ player, maxSalary }: { player: RosterPlayer; maxSalary: number }) {
  const pct = (player.salary / maxSalary) * 100;

  const flags = [
    player.team_option && "TEAM OPT",
    player.player_option && "PLAYER OPT",
    player.extension && "EXT",
    player.two_way && "TWO-WAY",
  ].filter(Boolean) as string[];

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "2fr 50px 120px 80px 100px",
        gap: 8,
        padding: "10px 16px",
        background: "rgba(255,255,255,0.02)",
        borderRadius: 8,
        border: "1px solid rgba(255,255,255,0.04)",
        alignItems: "center",
      }}
    >
      {/* Name */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 400, fontSize: 14, color: "#fff" }}>
          {player.name}
        </span>
        {flags.map((f) => (
          <span
            key={f}
            style={{
              fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
              fontWeight: 700,
              fontSize: 8,
              letterSpacing: "0.1em",
              background: "rgba(57,255,20,0.1)",
              border: "1px solid rgba(57,255,20,0.2)",
              color: SIG,
              borderRadius: 3,
              padding: "2px 5px",
            }}
          >
            {f}
          </span>
        ))}
      </div>

      {/* Pos */}
      <span style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 700, fontSize: 11, color: "rgba(255,255,255,0.35)", letterSpacing: "0.06em" }}>
        {player.pos}
      </span>

      {/* Salary bar + value */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={{ fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace', fontWeight: 700, fontSize: 13, color: "#fff" }}>
          {fmtM(player.salary)}
        </span>
        <div style={{ height: 3, background: "rgba(255,255,255,0.06)", borderRadius: 2 }}>
          <div style={{ height: "100%", width: `${pct}%`, background: SIG, borderRadius: 2, opacity: 0.6 }} />
        </div>
      </div>

      {/* Years */}
      <span style={{ fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace', fontWeight: 400, fontSize: 12, color: "rgba(255,255,255,0.4)" }}>
        {player.years}yr
      </span>

      {/* Notes */}
      <span style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 400, fontSize: 11, color: "rgba(255,255,255,0.25)" }}>
        {player.notes ?? ""}
      </span>
    </div>
  );
}
