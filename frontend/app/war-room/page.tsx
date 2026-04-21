"use client";

import { useRouter } from "next/navigation";
import { CBA, DEADLINES, RAIL_DATA, SIG, CORAL } from "./_lib/data";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
  fontWeight: 700,
};

interface TileConfig {
  code: string;
  title: string;
  subtitle: string;
  stat: string;
  statLabel: string;
  href: string;
  comingSoon?: boolean;
}

export default function WarRoomHub() {
  const router = useRouter();

  const overTax = RAIL_DATA.filter((t) => t.payroll >= CBA.LUXURY_TAX).length;
  const draftDeadline = DEADLINES.find((d) => d.label === "NBA DRAFT");
  const draftDays = draftDeadline ? draftDeadline.daysFromNow : 68;

  const TILES: TileConfig[] = [
    {
      code: "01",
      title: "LEAGUE VIEW",
      subtitle: "30-team payroll and cap standings",
      stat: `${overTax}`,
      statLabel: "TEAMS OVER TAX",
      href: "/war-room/league",
    },
    {
      code: "02",
      title: "CAP REFERENCE",
      subtitle: "CBA thresholds, exceptions, and rules",
      stat: `$${CBA.CAP}M`,
      statLabel: "2025-26 SALARY CAP",
      href: "/war-room/cap",
    },
    {
      code: "03",
      title: "DEADLINES",
      subtitle: "Draft, free agency, and transaction calendar",
      stat: `${draftDays}`,
      statLabel: "DAYS TO DRAFT",
      href: "/war-room/deadlines",
    },
    {
      code: "04",
      title: "FRONT OFFICE",
      subtitle: "GM, coach, and ownership by franchise",
      stat: "30",
      statLabel: "FRONT OFFICES",
      href: "/war-room/front-office",
    },
    {
      code: "05",
      title: "CAP TABLE",
      subtitle: "Roster salaries and contract structure",
      stat: `$${CBA.CAP}M`,
      statLabel: "SALARY CAP",
      href: "/war-room/cap-table",
    },
    {
      code: "06",
      title: "TRADE BUILDER",
      subtitle: "Salary-matching and asset analysis",
      stat: "SOON",
      statLabel: "IN DEVELOPMENT",
      href: "/war-room/trade",
      comingSoon: true,
    },
  ];

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
      {/* Header */}
      <div style={{ maxWidth: 1200, margin: "0 auto 40px" }}>
        <p
          style={{
            ...HB,
            fontSize: 11,
            letterSpacing: "0.2em",
            color: SIG,
            margin: "0 0 8px",
            textTransform: "uppercase",
          }}
        >
          PIVOT
        </p>
        <h1
          style={{
            ...HB,
            fontSize: "clamp(28px, 4vw, 48px)",
            margin: 0,
            letterSpacing: "-0.01em",
            textTransform: "uppercase",
          }}
        >
          WAR ROOM
        </h1>
        <p
          style={{
            fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
            fontWeight: 400,
            fontSize: 14,
            color: "rgba(255,255,255,0.45)",
            margin: "8px 0 0",
          }}
        >
          Front office intelligence. Cap management. Transaction calendar.
        </p>
      </div>

      {/* Tile Grid */}
      <div
        style={{
          maxWidth: 1200,
          margin: "0 auto",
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 16,
        }}
        className="war-room-grid"
      >
        {TILES.map((tile) => (
          <WarRoomTile
            key={tile.code}
            tile={tile}
            onNavigate={() => router.push(tile.href)}
          />
        ))}
      </div>

      <style>{`
        @media (max-width: 900px) {
          .war-room-grid {
            grid-template-columns: repeat(2, 1fr) !important;
          }
        }
        @media (max-width: 580px) {
          .war-room-grid {
            grid-template-columns: 1fr !important;
          }
        }
        .war-tile-hover:hover {
          transform: translateY(-2px);
          border-color: rgba(57,255,20,0.5) !important;
          box-shadow: 0 8px 32px rgba(57,255,20,0.08);
        }
        .war-tile-hover:active {
          transform: translateY(0);
        }
      `}</style>
    </div>
  );
}

function WarRoomTile({
  tile,
  onNavigate,
}: {
  tile: TileConfig;
  onNavigate: () => void;
}) {
  const HBLocal: React.CSSProperties = {
    fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
    fontWeight: 700,
  };

  return (
    <button
      onClick={tile.comingSoon ? undefined : onNavigate}
      disabled={tile.comingSoon}
      className={tile.comingSoon ? undefined : "war-tile-hover"}
      style={{
        background: "rgba(255,255,255,0.03)",
        backdropFilter: "blur(24px) saturate(180%)",
        WebkitBackdropFilter: "blur(24px) saturate(180%)",
        border: `1px solid rgba(57,255,20,${tile.comingSoon ? 0.08 : 0.2})`,
        borderRadius: 12,
        cursor: tile.comingSoon ? "default" : "pointer",
        opacity: tile.comingSoon ? 0.45 : 1,
        padding: "28px 24px",
        minHeight: 200,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        textAlign: "left",
        transition: "transform 120ms, border-color 120ms, box-shadow 120ms",
        outline: "none",
        width: "100%",
        boxSizing: "border-box",
        position: "relative",
      }}
    >
      {/* Coming Soon Badge */}
      {tile.comingSoon && (
        <span
          style={{
            ...HBLocal,
            position: "absolute",
            top: 14,
            right: 14,
            fontSize: 9,
            letterSpacing: "0.15em",
            background: "rgba(255,107,74,0.15)",
            border: "1px solid rgba(255,107,74,0.35)",
            color: CORAL,
            borderRadius: 4,
            padding: "3px 7px",
          }}
        >
          COMING SOON
        </span>
      )}

      {/* Code + Title */}
      <div>
        <p
          style={{
            ...HBLocal,
            fontSize: 10,
            letterSpacing: "0.18em",
            color: "rgba(255,255,255,0.3)",
            margin: "0 0 10px",
          }}
        >
          {tile.code}
        </p>
        <h2
          style={{
            ...HBLocal,
            fontSize: "clamp(16px, 2vw, 22px)",
            margin: "0 0 8px",
            letterSpacing: "0.02em",
            textTransform: "uppercase",
            color: "#fff",
          }}
        >
          {tile.title}
        </h2>
        <p
          style={{
            fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
            fontWeight: 400,
            fontSize: 13,
            color: "rgba(255,255,255,0.45)",
            margin: 0,
            lineHeight: 1.4,
          }}
        >
          {tile.subtitle}
        </p>
      </div>

      {/* Stat + Arrow Row */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginTop: 24,
        }}
      >
        <div>
          <p
            style={{
              ...HBLocal,
              fontSize: "clamp(22px, 2.5vw, 32px)",
              margin: 0,
              color: tile.comingSoon ? "rgba(255,255,255,0.3)" : SIG,
              letterSpacing: "-0.02em",
            }}
          >
            {tile.stat}
          </p>
          <p
            style={{
              ...HBLocal,
              fontSize: 9,
              letterSpacing: "0.14em",
              color: "rgba(255,255,255,0.3)",
              margin: "4px 0 0",
            }}
          >
            {tile.statLabel}
          </p>
        </div>
        {!tile.comingSoon && (
          <span
            style={{
              fontFamily: '"Helvetica Neue","Helvetica",sans-serif',
              fontWeight: 400,
              fontSize: 20,
              color: "rgba(57,255,20,0.5)",
              lineHeight: 1,
            }}
          >
            {"\u2192"}
          </span>
        )}
      </div>
    </button>
  );
}
