"use client";

import { useRouter } from "next/navigation";
import { DEADLINES, SIG, CORAL } from "../_lib/data";
import type { Deadline } from "../_lib/data";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
  fontWeight: 700,
};

// Additional context rows that explain what each deadline means
const DEADLINE_CONTEXT: Record<string, string> = {
  "NBA DRAFT": "Teams submit picks and make selections. Two rounds: 30 picks each. Undrafted players can sign with any team after.",
  "FREE AGENCY": "Unrestricted and restricted free agency opens. Moratorium period begins July 1; contracts become official July 7.",
  "QUALIFYING OFFERS": "Teams must submit qualifying offers to restricted free agents by this date to retain matching rights.",
  "SALARY CAP SET": "Official cap and tax figures set by the league. Teams receive final cap sheets to begin planning.",
  "TRAINING CAMP": "Rosters must be at training camp maximum (20 players). Preseason games follow within 2 weeks.",
  "TRADE DEADLINE": "No trades may be executed after 3:00 PM ET on this date until the offseason moratorium lifts.",
};

function urgencyColor(days: number): string {
  if (days <= 14) return CORAL;
  if (days <= 60) return "#F9CA24";
  if (days <= 120) return SIG;
  return "rgba(255,255,255,0.45)";
}

export default function DeadlinesPage() {
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
      <div style={{ maxWidth: 900, margin: "0 auto" }}>

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
        <div style={{ marginBottom: 40 }}>
          <p style={{ ...HB, fontSize: 11, letterSpacing: "0.2em", color: SIG, margin: "0 0 8px" }}>
            DEADLINES
          </p>
          <h1 style={{ ...HB, fontSize: "clamp(24px, 3vw, 40px)", margin: "0 0 4px", textTransform: "uppercase" }}>
            TRANSACTION CALENDAR
          </h1>
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
            Key front-office dates from today, April 19, 2026.
          </p>
        </div>

        {/* Next deadline highlight */}
        {DEADLINES.length > 0 && (
          <NextDeadlineCard deadline={DEADLINES[0]} />
        )}

        {/* Remaining deadlines */}
        <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 10 }}>
          {DEADLINES.slice(1).map((dl) => (
            <DeadlineRow key={dl.label} deadline={dl} />
          ))}
        </div>

        {/* Footer */}
        <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 11, color: "rgba(255,255,255,0.2)", marginTop: 40, lineHeight: 1.6 }}>
          All dates are estimates based on historical NBA scheduling. Official dates are subject to announcement by the league office.
        </p>
      </div>
    </div>
  );
}

function NextDeadlineCard({ deadline }: { deadline: Deadline }) {
  const color = urgencyColor(deadline.daysFromNow);
  const context = DEADLINE_CONTEXT[deadline.label] ?? "";

  return (
    <div
      style={{
        background: "rgba(255,255,255,0.03)",
        backdropFilter: "blur(24px) saturate(180%)",
        WebkitBackdropFilter: "blur(24px) saturate(180%)",
        border: `1px solid ${color}30`,
        borderRadius: 12,
        padding: "28px 28px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `radial-gradient(ellipse at top left, ${color}08, transparent 60%)`,
          pointerEvents: "none",
        }}
      />
      <p style={{ ...HB, fontSize: 9, letterSpacing: "0.2em", color: "rgba(255,255,255,0.3)", margin: "0 0 16px" }}>
        NEXT DEADLINE
      </p>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 16 }}>
        <div>
          <h2 style={{ ...HB, fontSize: "clamp(20px, 3vw, 32px)", margin: "0 0 4px", textTransform: "uppercase", color: "#fff" }}>
            {deadline.label}
          </h2>
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.45)", margin: "0 0 12px" }}>
            {deadline.sublabel}
          </p>
          {context && (
            <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0, maxWidth: 520, lineHeight: 1.6 }}>
              {context}
            </p>
          )}
        </div>
        <div style={{ textAlign: "right" }}>
          <p style={{ fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace', fontWeight: 700, fontSize: "clamp(36px, 5vw, 64px)", color, margin: 0, lineHeight: 1 }}>
            {deadline.daysFromNow}
          </p>
          <p style={{ ...HB, fontSize: 10, letterSpacing: "0.14em", color: "rgba(255,255,255,0.3)", margin: "6px 0 0" }}>
            DAYS
          </p>
        </div>
      </div>
    </div>
  );
}

function DeadlineRow({ deadline }: { deadline: Deadline }) {
  const color = urgencyColor(deadline.daysFromNow);
  const context = DEADLINE_CONTEXT[deadline.label] ?? "";

  return (
    <div
      style={{
        background: "rgba(255,255,255,0.02)",
        border: "1px solid rgba(255,255,255,0.06)",
        borderRadius: 8,
        padding: "16px 20px",
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 20,
        flexWrap: "wrap",
      }}
    >
      <div style={{ flex: 1, minWidth: 220 }}>
        <p style={{ ...HB, fontSize: 12, letterSpacing: "0.08em", color: "#fff", margin: "0 0 3px", textTransform: "uppercase" }}>
          {deadline.label}
        </p>
        <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 11, color: "rgba(255,255,255,0.35)", margin: "0 0 8px" }}>
          {deadline.sublabel}
        </p>
        {context && (
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 12, color: "rgba(255,255,255,0.35)", margin: 0, lineHeight: 1.5 }}>
            {context}
          </p>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <p style={{ fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace', fontWeight: 700, fontSize: 22, color, margin: 0 }}>
          {deadline.daysFromNow}
        </p>
        <p style={{ ...HB, fontSize: 9, letterSpacing: "0.14em", color: "rgba(255,255,255,0.25)", margin: 0 }}>
          DAYS
        </p>
      </div>
    </div>
  );
}
