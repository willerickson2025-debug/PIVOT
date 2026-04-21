"use client";

import { useRouter } from "next/navigation";
import { SIG, CORAL } from "../_lib/data";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
  fontWeight: 700,
};

const PLANNED_FEATURES = [
  {
    label: "SALARY MATCHING",
    desc: "Build multi-team trades with automatic salary-match validation against the 125% + $100K rule.",
  },
  {
    label: "ASSET TRACKER",
    desc: "Attach picks and cash to trade packages. Visualize pick swaps and protection layers.",
  },
  {
    label: "APRON CHECK",
    desc: "Real-time check against first and second apron restrictions. Flags illegal trades instantly.",
  },
  {
    label: "CAP IMPACT",
    desc: "See the post-trade payroll, tax bill, and available exceptions for each team in the deal.",
  },
  {
    label: "HISTORICAL COMPS",
    desc: "Surface structurally similar trades from the last 10 years for context and precedent.",
  },
];

export default function TradePage() {
  const router = useRouter();

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
      <div style={{ maxWidth: 800, margin: "0 auto" }}>

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
        <div style={{ marginBottom: 48 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
            <p style={{ ...HB, fontSize: 11, letterSpacing: "0.2em", color: SIG, margin: 0 }}>
              TRADE BUILDER
            </p>
            <span
              style={{
                ...HB,
                fontSize: 9,
                letterSpacing: "0.15em",
                background: "rgba(255,107,74,0.15)",
                border: "1px solid rgba(255,107,74,0.35)",
                color: CORAL,
                borderRadius: 4,
                padding: "3px 8px",
              }}
            >
              COMING SOON
            </span>
          </div>
          <h1
            style={{
              ...HB,
              fontSize: "clamp(28px, 4vw, 52px)",
              margin: "0 0 16px",
              textTransform: "uppercase",
              letterSpacing: "-0.01em",
            }}
          >
            SALARY-MATCH TRADE BUILDER
          </h1>
          <p
            style={{
              fontFamily: '"Helvetica Neue",sans-serif',
              fontWeight: 400,
              fontSize: 15,
              color: "rgba(255,255,255,0.45)",
              margin: 0,
              lineHeight: 1.6,
              maxWidth: 560,
            }}
          >
            Build and validate NBA trades in real time. CBA compliance checking, apron restrictions, and cap impact analysis across all three teams.
          </p>
        </div>

        {/* Planned features */}
        <div style={{ marginBottom: 48 }}>
          <p style={{ ...HB, fontSize: 10, letterSpacing: "0.18em", color: "rgba(255,255,255,0.25)", margin: "0 0 16px" }}>
            PLANNED FEATURES
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {PLANNED_FEATURES.map((f) => (
              <div
                key={f.label}
                style={{
                  display: "flex",
                  gap: 20,
                  background: "rgba(255,255,255,0.02)",
                  border: "1px solid rgba(255,255,255,0.06)",
                  borderRadius: 8,
                  padding: "14px 18px",
                  alignItems: "flex-start",
                }}
              >
                <div
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    background: "rgba(57,255,20,0.4)",
                    flexShrink: 0,
                    marginTop: 6,
                  }}
                />
                <div>
                  <p style={{ ...HB, fontSize: 11, letterSpacing: "0.1em", color: SIG, margin: "0 0 4px" }}>
                    {f.label}
                  </p>
                  <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.45)", margin: 0, lineHeight: 1.5 }}>
                    {f.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Teaser mockup */}
        <div
          style={{
            background: "rgba(255,255,255,0.02)",
            border: "1px solid rgba(57,255,20,0.1)",
            borderRadius: 12,
            padding: "32px 28px",
            position: "relative",
            overflow: "hidden",
          }}
        >
          {/* Blur overlay */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              backdropFilter: "blur(6px)",
              WebkitBackdropFilter: "blur(6px)",
              background: "rgba(10,10,10,0.6)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 2,
              borderRadius: 12,
            }}
          >
            <div style={{ textAlign: "center" }}>
              <p style={{ ...HB, fontSize: 11, letterSpacing: "0.2em", color: CORAL, margin: "0 0 8px" }}>
                COMING SOON
              </p>
              <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
                Trade builder is under active development.
              </p>
            </div>
          </div>

          {/* Fake UI behind blur */}
          <div style={{ opacity: 0.3 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr auto 1fr", gap: 16, alignItems: "center" }}>
              <div>
                <p style={{ ...HB, fontSize: 9, letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", margin: "0 0 8px" }}>TEAM A SENDS</p>
                <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: 8, padding: "12px 14px", marginBottom: 8 }}>
                  <p style={{ ...HB, fontSize: 13, color: "#fff", margin: 0 }}>Player Name</p>
                  <p style={{ fontFamily: '"JetBrains Mono",monospace', fontSize: 11, color: SIG, margin: "4px 0 0" }}>$26.5M</p>
                </div>
              </div>
              <p style={{ ...HB, fontSize: 20, color: "rgba(255,255,255,0.15)", margin: 0, textAlign: "center" }}>
                {"\u21c4"}
              </p>
              <div>
                <p style={{ ...HB, fontSize: 9, letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", margin: "0 0 8px" }}>TEAM B SENDS</p>
                <div style={{ background: "rgba(255,255,255,0.04)", borderRadius: 8, padding: "12px 14px", marginBottom: 8 }}>
                  <p style={{ ...HB, fontSize: 13, color: "#fff", margin: 0 }}>Player Name</p>
                  <p style={{ fontFamily: '"JetBrains Mono",monospace', fontSize: 11, color: SIG, margin: "4px 0 0" }}>$24.8M</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
