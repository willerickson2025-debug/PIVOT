"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RAIL_DATA, SIG } from "../_lib/data";

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

export default function FrontOfficePage() {
  const router = useRouter();
  const [conf, setConf] = useState<"All" | "East" | "West">("All");
  const [div, setDiv] = useState<typeof DIVISIONS[number]>("All");
  const [search, setSearch] = useState("");

  const filtered = RAIL_DATA.filter((t) => {
    if (conf !== "All" && t.conf !== conf) return false;
    if (div !== "All" && t.div !== div) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      return (
        t.name.toLowerCase().includes(q) ||
        t.city.toLowerCase().includes(q) ||
        t.abbr.toLowerCase().includes(q) ||
        (t.gm ?? "").toLowerCase().includes(q) ||
        (t.coach ?? "").toLowerCase().includes(q)
      );
    }
    return true;
  });

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
            FRONT OFFICE
          </p>
          <h1 style={{ ...HB, fontSize: "clamp(24px, 3vw, 40px)", margin: "0 0 4px", textTransform: "uppercase" }}>
            ALL 30 FRANCHISES
          </h1>
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
            General managers, head coaches, and division groupings.
          </p>
        </div>

        {/* Search + Filters */}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 24, alignItems: "center" }}>
          <input
            type="text"
            placeholder="Search team, city, GM, coach..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{
              fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
              fontWeight: 400,
              fontSize: 13,
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              color: "#fff",
              padding: "8px 14px",
              outline: "none",
              width: 260,
            }}
          />
          <div style={{ display: "flex", gap: 6 }}>
            {CONFERENCES.map((c) => (
              <FilterPill key={c} label={c} active={conf === c} onClick={() => { setConf(c as typeof conf); setDiv("All"); }} />
            ))}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {DIVISIONS.filter((d) => d === "All" || (conf === "All" || (
              conf === "East" ? ["Atlantic", "Central", "Southeast"].includes(d) :
              ["Northwest", "Pacific", "Southwest"].includes(d)
            ))).map((d) => (
              <FilterPill key={d} label={d.toUpperCase()} active={div === d} onClick={() => setDiv(d)} />
            ))}
          </div>
        </div>

        {/* Count */}
        <p style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: "rgba(255,255,255,0.25)", margin: "0 0 14px" }}>
          {filtered.length} TEAMS
        </p>

        {/* Grid */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            gap: 12,
          }}
        >
          {filtered.map((team) => (
            <div
              key={team.abbr}
              style={{
                background: "rgba(255,255,255,0.02)",
                border: "1px solid rgba(255,255,255,0.06)",
                borderRadius: 10,
                padding: "18px 20px",
              }}
            >
              {/* Team name + record */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
                <div>
                  <p style={{ ...HB, fontSize: 14, color: "#fff", margin: "0 0 2px", textTransform: "uppercase" }}>
                    {team.city}
                  </p>
                  <p style={{ ...HB, fontSize: 11, color: SIG, margin: 0, letterSpacing: "0.08em" }}>
                    {team.name.toUpperCase()}
                  </p>
                </div>
                <div style={{ textAlign: "right" }}>
                  <p style={{ fontFamily: '"JetBrains Mono",monospace', fontWeight: 700, fontSize: 14, color: "rgba(255,255,255,0.6)", margin: "0 0 2px" }}>
                    {team.wins ?? 0}-{team.losses ?? 0}
                  </p>
                  <p style={{ ...HB, fontSize: 9, letterSpacing: "0.1em", color: "rgba(255,255,255,0.25)", margin: 0 }}>
                    {team.conf.toUpperCase()}
                  </p>
                </div>
              </div>

              {/* Divider */}
              <div style={{ height: 1, background: "rgba(255,255,255,0.05)", marginBottom: 14 }} />

              {/* Personnel */}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <PersonnelRow role="GENERAL MANAGER" name={team.gm ?? "TBD"} />
                <PersonnelRow role="HEAD COACH" name={team.coach ?? "TBD"} />
              </div>

              {/* Division badge */}
              <p style={{ ...HB, fontSize: 9, letterSpacing: "0.12em", color: "rgba(255,255,255,0.2)", margin: "14px 0 0" }}>
                {team.div.toUpperCase()}
              </p>
            </div>
          ))}
        </div>

        {filtered.length === 0 && (
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 14, color: "rgba(255,255,255,0.3)", textAlign: "center", marginTop: 60 }}>
            No teams match your filters.
          </p>
        )}
      </div>
    </div>
  );
}

function PersonnelRow({ role, name }: { role: string; name: string }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
      <p style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: "0.12em", color: "rgba(255,255,255,0.3)", margin: 0 }}>
        {role}
      </p>
      <p style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.7)", margin: 0 }}>
        {name}
      </p>
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
