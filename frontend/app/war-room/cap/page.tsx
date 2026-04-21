"use client";

import { useRouter } from "next/navigation";
import { CBA, SIG, CORAL } from "../_lib/data";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
  fontWeight: 700,
};
const MONO: React.CSSProperties = {
  fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace',
  fontWeight: 700,
};

interface CBALine {
  label: string;
  value: string;
  note: string;
  highlight?: boolean;
  coral?: boolean;
}

const THRESHOLDS: CBALine[] = [
  {
    label: "SALARY CAP",
    value: `$${CBA.CAP}M`,
    note: "Teams over the cap cannot use cap space to sign players. Most signings via exceptions.",
    highlight: true,
  },
  {
    label: "LUXURY TAX LINE",
    value: `$${CBA.LUXURY_TAX}M`,
    note: "Teams above this line pay dollar-for-dollar tax on each dollar over. Tax proceeds distributed to non-tax teams.",
  },
  {
    label: "FIRST APRON",
    value: `$${CBA.APRON_1}M`,
    note: "Lose ability to aggregate salaries in trades and use the Tax MLE over $5.1M. Cannot sign a buyout player from a tax team.",
  },
  {
    label: "SECOND APRON",
    value: `$${CBA.APRON_2}M`,
    note: "Most restrictive tier. Cannot trade first-round picks 7+ years out, take back more salary than sent out, or exceed the line for any reason.",
    coral: true,
  },
];

const EXCEPTIONS: CBALine[] = [
  {
    label: "NON-TAX MLE",
    value: `$${CBA.NON_TAX_MLE}M`,
    note: "Mid-Level Exception for teams below the luxury tax line. Up to 4-year contract.",
    highlight: true,
  },
  {
    label: "TAX MLE",
    value: `$${CBA.TAX_MLE}M`,
    note: "Mid-Level Exception for luxury-tax teams (below Apron 1). Up to 3 years.",
  },
  {
    label: "BI-ANNUAL EXCEPTION",
    value: `$${CBA.BAE}M`,
    note: "Available to non-tax teams that did not use the Non-Tax MLE in the prior year. 2-year max.",
  },
  {
    label: "MINIMUM SALARY",
    value: `~$${CBA.MINIMUM_SALARY}M`,
    note: "Rookie minimum for first-year players. Scales with years of service (up to ~$3.3M for 10-year vets).",
  },
];

const KEY_RULES: { title: string; body: string }[] = [
  {
    title: "BIRD RIGHTS",
    body: "A team acquires Larry Bird Rights after a player completes 3 seasons without clearing waivers or being a free agent. Allows re-signing above the cap up to the player's maximum salary.",
  },
  {
    title: "EARLY BIRD RIGHTS",
    body: "Acquired after 2 seasons. Allows re-signing to a 2-year deal at 175% of prior salary or 104.5% of the average salary, whichever is greater.",
  },
  {
    title: "SIGN-AND-TRADE",
    body: "A team can trade a player's Bird Rights to another team if they agree to a new contract. The acquiring team must be over the cap. Second-apron teams cannot receive players via sign-and-trade.",
  },
  {
    title: "ROOKIE SCALE",
    body: "First-round picks sign 4-year rookie-scale contracts. The team holds options on years 3 and 4. After year 4, the player is a restricted free agent.",
  },
  {
    title: "DESIGNATED VETERAN EXTENSION",
    body: "Teams can offer 5-year max extensions to their own players at 35% of the cap if they meet performance criteria (All-NBA, All-Star, DPOY, MVP).",
  },
  {
    title: "TRADE AGGREGATION",
    body: "Teams can combine multiple players' salaries on the same side of a trade. First-apron and second-apron teams face restrictions on aggregation.",
  },
];

export default function CapPage() {
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
      <div style={{ padding: "6px 24px", marginBottom: 0, textAlign: "center", borderBottom: "0.5px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.015)" }}>
        <span style={{ fontFamily: '"JetBrains Mono","Fira Code",monospace', fontSize: 9, color: "rgba(255,255,255,0.25)", letterSpacing: "0.12em" }}>DATA AS OF 2025-26 REGULAR SEASON · UPDATED MANUALLY</span>
      </div>
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
            CAP REFERENCE
          </p>
          <h1 style={{ ...HB, fontSize: "clamp(24px, 3vw, 40px)", margin: "0 0 4px", textTransform: "uppercase" }}>
            CBA 2025-26
          </h1>
          <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.4)", margin: 0 }}>
            Key thresholds, salary exceptions, and transaction rules.
          </p>
        </div>

        {/* Thresholds */}
        <Section label="THRESHOLDS">
          {THRESHOLDS.map((line) => (
            <CBARow key={line.label} line={line} />
          ))}
        </Section>

        {/* Exceptions */}
        <Section label="SALARY EXCEPTIONS">
          {EXCEPTIONS.map((line) => (
            <CBARow key={line.label} line={line} />
          ))}
        </Section>

        {/* Key Rules */}
        <div style={{ marginTop: 40 }}>
          <p style={{ ...HB, fontSize: 10, letterSpacing: "0.18em", color: "rgba(255,255,255,0.3)", margin: "0 0 16px" }}>
            KEY RULES
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {KEY_RULES.map((rule) => (
              <div
                key={rule.title}
                style={{
                  background: "rgba(255,255,255,0.02)",
                  border: "1px solid rgba(255,255,255,0.06)",
                  borderRadius: 8,
                  padding: "16px 20px",
                }}
              >
                <p style={{ ...HB, fontSize: 11, letterSpacing: "0.12em", color: SIG, margin: "0 0 6px" }}>
                  {rule.title}
                </p>
                <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.55)", margin: 0, lineHeight: 1.6 }}>
                  {rule.body}
                </p>
              </div>
            ))}
          </div>
        </div>

        {/* Footer note */}
        <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 11, color: "rgba(255,255,255,0.2)", marginTop: 40, lineHeight: 1.6 }}>
          Dollar figures are 2025-26 season estimates. CBA values are indexed annually to basketball-related income (BRI). Consult official CBA documentation for complete rule text.
        </p>
      </div>
    </div>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <p style={{ ...HB, fontSize: 10, letterSpacing: "0.18em", color: "rgba(255,255,255,0.3)", margin: "0 0 12px" }}>
        {label}
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {children}
      </div>
    </div>
  );
}

function CBARow({ line }: { line: CBALine }) {
  const color = line.coral ? CORAL : line.highlight ? SIG : "rgba(255,255,255,0.6)";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 16,
        background: "rgba(255,255,255,0.02)",
        border: `1px solid ${line.highlight || line.coral ? `${color}22` : "rgba(255,255,255,0.06)"}`,
        borderRadius: 8,
        padding: "14px 20px",
      }}
    >
      <div style={{ minWidth: 130 }}>
        <p style={{ fontFamily: '"Helvetica Neue","Helvetica",sans-serif', fontWeight: 700, fontSize: 9, letterSpacing: "0.12em", color: "rgba(255,255,255,0.3)", margin: "0 0 4px" }}>
          {line.label}
        </p>
        <p style={{ fontFamily: '"JetBrains Mono","Fira Mono","Courier New",monospace', fontWeight: 700, fontSize: 18, color, margin: 0 }}>
          {line.value}
        </p>
      </div>
      <p style={{ fontFamily: '"Helvetica Neue",sans-serif', fontWeight: 400, fontSize: 13, color: "rgba(255,255,255,0.45)", margin: 0, lineHeight: 1.6, paddingTop: 2 }}>
        {line.note}
      </p>
    </div>
  );
}
