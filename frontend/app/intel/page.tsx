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
  borderRadius: "16px",
};

const GLASS_PILL: React.CSSProperties = {
  background: "rgba(255, 255, 255, 0.04)",
  backdropFilter: "blur(24px) saturate(180%)",
  WebkitBackdropFilter: "blur(24px) saturate(180%)",
  border: "0.5px solid rgba(255, 255, 255, 0.08)",
  borderRadius: "999px",
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface PlayerResult {
  id: string;
  slug: string;
  full_name: string;
  team_abbr: string;
  position: string;
  jersey: string;
  pivot_index: number;
  pi_l10_change: number;
  pi_l10_history: number[];
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({ data, change }: { data: number[]; change: number }) {
  if (!data || data.length < 2) return <div style={{ width: 80, height: 24 }} />;
  const W = 80,
    H = 24;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * W;
      const y = H - 2 - ((v - min) / range) * (H - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const stroke =
    change > 0.1 ? SIG : change < -0.1 ? CORAL : "rgba(255,255,255,0.35)";
  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      style={{ display: "block", overflow: "visible" }}
    >
      <polyline
        points={pts}
        fill="none"
        stroke={stroke}
        strokeWidth="1.4"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

// ── Skeleton row ──────────────────────────────────────────────────────────────

function SkeletonRow({ idx }: { idx: number }) {
  return (
    <div
      className="pivot-top10-row"
      style={{
        ...GLASS,
        borderRadius: 0,
        border: "none",
        borderBottom: "0.5px solid rgba(255,255,255,0.05)",
        display: "grid",
        gridTemplateColumns: "32px 1fr 90px 80px 80px",
        alignItems: "center",
        padding: "14px 0",
        opacity: Math.max(0.15, 1 - idx * 0.085),
      }}
    >
      <div
        style={{
          width: 14,
          height: 10,
          background: "rgba(255,255,255,0.08)",
          borderRadius: 2,
          marginLeft: 8,
        }}
      />
      <div style={{ paddingRight: 16 }}>
        <div
          style={{
            width: "52%",
            height: 13,
            background: "rgba(255,255,255,0.09)",
            borderRadius: 2,
            marginBottom: 5,
          }}
        />
        <div
          style={{
            width: "36%",
            height: 10,
            background: "rgba(255,255,255,0.05)",
            borderRadius: 2,
          }}
        />
      </div>
      <div
        className="pivot-sparkline-cell"
        style={{
          width: 80,
          height: 22,
          background: "rgba(255,255,255,0.05)",
          borderRadius: 4,
          margin: "0 auto",
        }}
      />
      <div
        style={{
          width: 34,
          height: 13,
          background: "rgba(255,255,255,0.09)",
          borderRadius: 2,
          marginLeft: "auto",
          marginRight: 8,
        }}
      />
      <div
        style={{
          width: 38,
          height: 10,
          background: "rgba(255,255,255,0.05)",
          borderRadius: 2,
        }}
      />
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function IntelPage() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PlayerResult[]>([]);
  const [totalResults, setTotalResults] = useState(0);
  const [focused, setFocused] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(-1);
  const [top10, setTop10] = useState<PlayerResult[]>([]);
  const [top10Loading, setTop10Loading] = useState(true);
  const [hoveredRow, setHoveredRow] = useState<number | null>(null);
  const [updateLabel, setUpdateLabel] = useState("");

  // Set update timestamp on mount (avoids hydration mismatch)
  useEffect(() => {
    const now = new Date();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    const day = String(now.getDate()).padStart(2, "0");
    const time = now.toLocaleTimeString("en-US", {
      timeZone: "America/Los_Angeles",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    setUpdateLabel(`${month}.${day} ${time}`);
  }, []);

  // Fetch top 10 on mount
  useEffect(() => {
    fetch("/api/players/top?limit=10&sort=pivot_index")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) setTop10(Array.isArray(d) ? d : d.data || []);
        setTop10Loading(false);
      })
      .catch(() => setTop10Loading(false));
  }, []);

  // Autocomplete
  const doSearch = useCallback((q: string) => {
    if (!q.trim()) {
      setResults([]);
      setTotalResults(0);
      return;
    }
    fetch(`/api/players/search?q=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        const items: PlayerResult[] = Array.isArray(d) ? d : d.data || [];
        setResults(items.slice(0, 6));
        setTotalResults(d.total ?? items.length);
      })
      .catch(() => {
        setResults([]);
        setTotalResults(0);
      });
  }, []);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(query), 120);
    return () => clearTimeout(debounceRef.current);
  }, [query, doSearch]);

  // Reset keyboard selection when results change
  useEffect(() => {
    setSelectedIdx(-1);
  }, [results]);

  // ⌘K global shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Click outside to close dropdown
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setFocused(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIdx((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (selectedIdx >= 0 && results[selectedIdx]) {
        router.push(`/intel/player/${results[selectedIdx].slug}`);
      }
    } else if (e.key === "Escape") {
      setFocused(false);
      inputRef.current?.blur();
    }
  }

  const showDropdown = focused && query.trim().length > 0;

  return (
    <>
      {/* Responsive styles + global overrides for this page */}
      <style>{`
        .pivot-intel-root {
          background: #000;
          min-height: 100vh;
          color: #fff;
        }
        .pivot-intel-root input::placeholder {
          font-family: "JetBrains Mono", "Fira Code", ui-monospace, monospace;
          color: rgba(255, 255, 255, 0.3);
        }
        .pivot-top10-row {
          grid-template-columns: 32px 1fr 90px 80px 80px;
        }
        @media (max-width: 480px) {
          .pivot-top10-row {
            grid-template-columns: 32px 1fr 80px 80px !important;
          }
          .pivot-sparkline-cell {
            display: none !important;
          }
        }
      `}</style>

      <div className="pivot-intel-root" style={{ ...HB, paddingBottom: 64 }}>

        {/* ── 1. TOP BAR ──────────────────────────────────────────────────── */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 24px",
          }}
        >
          <span
            style={{
              ...MONO,
              fontSize: 12,
              color: "rgba(255,255,255,0.6)",
              letterSpacing: "1px",
            }}
          >
            PIVOT/TERM&nbsp;&nbsp;INTEL
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <div
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: SIG,
                boxShadow: `0 0 6px ${SIG}`,
              }}
            />
            <span
              style={{
                ...MONO,
                fontSize: 11,
                color: SIG,
                letterSpacing: "1.2px",
              }}
            >
              LIVE
            </span>
          </div>
        </div>

        {/* ── 2. HERO ─────────────────────────────────────────────────────── */}
        <div
          style={{
            paddingTop: 96,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            paddingLeft: 24,
            paddingRight: 24,
          }}
        >
          {/* Eyebrow */}
          <div
            style={{
              ...MONO,
              fontSize: 11,
              color: SIG,
              letterSpacing: "1.4px",
              marginBottom: 12,
            }}
          >
            PLAYER INTEL
          </div>

          {/* H1 */}
          <h1
            style={{
              ...HB,
              fontSize: "clamp(28px, 5vw, 36px)",
              color: "#ffffff",
              letterSpacing: "-0.4px",
              margin: "0 0 32px",
              textAlign: "center",
            }}
          >
            Search any player
          </h1>

          {/* Search wrapper */}
          <div
            ref={containerRef}
            style={{
              position: "relative",
              width: "100%",
              maxWidth: 540,
            }}
          >
            {/* Search bar */}
            <div
              style={{
                ...GLASS_PILL,
                position: "relative",
                boxShadow: focused
                  ? `0 0 0 1px ${SIG}, 0 0 32px rgba(57, 255, 20, 0.35)`
                  : "none",
                transition: "box-shadow 160ms ease",
              }}
            >
              {/* Search icon */}
              <svg
                width="18"
                height="18"
                viewBox="0 0 18 18"
                fill="none"
                style={{
                  position: "absolute",
                  left: 22,
                  top: "50%",
                  transform: "translateY(-50%)",
                  pointerEvents: "none",
                  flexShrink: 0,
                }}
              >
                <circle
                  cx="7.5"
                  cy="7.5"
                  r="5"
                  stroke="rgba(255,255,255,0.35)"
                  strokeWidth="1.4"
                />
                <path
                  d="M11.5 11.5L15.5 15.5"
                  stroke="rgba(255,255,255,0.35)"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
              </svg>

              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onFocus={() => setFocused(true)}
                onKeyDown={handleKeyDown}
                placeholder="LeBron, SGA, Wemby..."
                autoComplete="off"
                spellCheck={false}
                style={{
                  ...MONO,
                  display: "block",
                  width: "100%",
                  fontSize: 18,
                  color: "#ffffff",
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  padding: "22px 28px 22px 60px",
                  caretColor: SIG,
                }}
              />
            </div>

            {/* Autocomplete dropdown */}
            {showDropdown && (
              <div
                style={{
                  ...GLASS,
                  position: "absolute",
                  top: "calc(100% + 8px)",
                  left: 0,
                  right: 0,
                  zIndex: 100,
                  overflow: "hidden",
                }}
              >
                {results.length === 0 ? (
                  <div
                    style={{
                      ...MONO,
                      padding: "14px 20px",
                      fontSize: 12,
                      color: "rgba(255,255,255,0.4)",
                      letterSpacing: "0.04em",
                    }}
                  >
                    No results for &ldquo;{query}&rdquo;
                  </div>
                ) : (
                  <>
                    {results.map((r, i) => {
                      const active = i === selectedIdx;
                      return (
                        <div
                          key={r.id}
                          onMouseEnter={() => setSelectedIdx(i)}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            router.push(`/intel/player/${r.slug}`);
                          }}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            padding: "12px 20px",
                            cursor: "pointer",
                            background: active
                              ? "rgba(255,255,255,0.06)"
                              : "transparent",
                            borderLeft: active
                              ? `2px solid ${SIG}`
                              : "2px solid transparent",
                            transition:
                              "background 80ms ease, border-color 80ms ease",
                          }}
                        >
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div
                              style={{
                                ...HB,
                                fontSize: 14,
                                color: "#ffffff",
                                marginBottom: 2,
                              }}
                            >
                              {r.full_name}
                            </div>
                            <div
                              style={{
                                ...MONO,
                                fontSize: 11,
                                color: "rgba(255,255,255,0.6)",
                                letterSpacing: "0.04em",
                              }}
                            >
                              {r.team_abbr} · {r.position} · #{r.jersey}
                            </div>
                          </div>
                          <div
                            style={{
                              ...MONO_B,
                              fontSize: 13,
                              color:
                                r.pivot_index > 85
                                  ? SIG
                                  : "rgba(255,255,255,0.6)",
                              marginLeft: 16,
                              flexShrink: 0,
                            }}
                          >
                            {r.pivot_index != null
                              ? r.pivot_index.toFixed(1)
                              : "--"}
                          </div>
                        </div>
                      );
                    })}

                    {totalResults > 6 && (
                      <div
                        style={{
                          ...MONO,
                          padding: "10px 20px",
                          borderTop: "0.5px solid rgba(255,255,255,0.06)",
                          fontSize: 11,
                          color: "rgba(255,255,255,0.4)",
                          letterSpacing: "0.04em",
                        }}
                      >
                        view all {totalResults} matches
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>

        {/* ── 3. TOP 10 ───────────────────────────────────────────────────── */}
        <div
          style={{
            maxWidth: 860,
            margin: "0 auto",
            padding: "80px 24px 0",
          }}
        >
          {/* Section header */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 16,
            }}
          >
            <span
              style={{
                ...MONO,
                fontSize: 11,
                color: "rgba(255,255,255,0.6)",
                letterSpacing: "1.2px",
              }}
            >
              TOP 10 CURRENT
            </span>
            <span
              style={{
                ...MONO,
                fontSize: 11,
                color: "rgba(255,255,255,0.4)",
                letterSpacing: "0.04em",
              }}
            >
              by PIVOT INDEX, L10 form
            </span>
          </div>

          {/* Column headers */}
          <div
            className="pivot-top10-row"
            style={{
              display: "grid",
              padding: "0 0 8px",
              borderBottom: "0.5px solid rgba(255,255,255,0.1)",
              marginBottom: 0,
            }}
          >
            {[
              { label: "#", align: "left" as const },
              { label: "PLAYER", align: "left" as const },
              { label: "FORM", align: "center" as const, className: "pivot-sparkline-cell" },
              { label: "PI", align: "right" as const },
              { label: "TREND", align: "right" as const },
            ].map(({ label, align, className }) => (
              <div
                key={label}
                className={className}
                style={{
                  ...MONO,
                  fontSize: 10,
                  color: "rgba(255,255,255,0.4)",
                  letterSpacing: "1px",
                  textAlign: align,
                  paddingLeft: label === "#" ? 8 : 0,
                }}
              >
                {label}
              </div>
            ))}
          </div>

          {/* Data rows or skeletons */}
          {top10Loading
            ? Array.from({ length: 10 }, (_, i) => (
                <SkeletonRow key={i} idx={i} />
              ))
            : top10.map((p, i) => {
                const hovered = hoveredRow === i;
                const trendUp = p.pi_l10_change > 0.1;
                const trendDown = p.pi_l10_change < -0.1;
                return (
                  <div
                    key={p.id}
                    className="pivot-top10-row"
                    onClick={() => router.push(`/intel/player/${p.slug}`)}
                    onMouseEnter={() => setHoveredRow(i)}
                    onMouseLeave={() => setHoveredRow(null)}
                    style={{
                      display: "grid",
                      alignItems: "center",
                      padding: "14px 0",
                      borderBottom: "0.5px solid rgba(255,255,255,0.05)",
                      cursor: "pointer",
                      background: hovered
                        ? "rgba(255,255,255,0.04)"
                        : "transparent",
                      backdropFilter: hovered
                        ? "blur(24px) saturate(180%)"
                        : "none",
                      WebkitBackdropFilter: hovered
                        ? "blur(24px) saturate(180%)"
                        : "none",
                      borderLeft: hovered
                        ? `2px solid ${SIG}`
                        : "2px solid transparent",
                      transition:
                        "background 160ms ease, border-color 160ms ease, backdrop-filter 160ms ease",
                    }}
                  >
                    {/* Rank */}
                    <div
                      style={{
                        ...MONO,
                        fontSize: 12,
                        color: "rgba(255,255,255,0.4)",
                        paddingLeft: 8,
                      }}
                    >
                      {i + 1}
                    </div>

                    {/* Name + meta */}
                    <div style={{ paddingRight: 16, minWidth: 0 }}>
                      <div
                        style={{
                          ...HB,
                          fontSize: 14,
                          color: "#ffffff",
                          marginBottom: 2,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {p.full_name}
                      </div>
                      <div
                        style={{
                          ...MONO,
                          fontSize: 11,
                          color: "rgba(255,255,255,0.6)",
                          letterSpacing: "0.04em",
                        }}
                      >
                        {p.team_abbr} · {p.position}
                      </div>
                    </div>

                    {/* Sparkline */}
                    <div
                      className="pivot-sparkline-cell"
                      style={{ display: "flex", justifyContent: "center" }}
                    >
                      <Sparkline
                        data={p.pi_l10_history}
                        change={p.pi_l10_change}
                      />
                    </div>

                    {/* PI value */}
                    <div
                      style={{
                        ...MONO_B,
                        fontSize: 16,
                        color: "#ffffff",
                        textAlign: "right",
                        paddingRight: 8,
                      }}
                    >
                      {p.pivot_index != null
                        ? p.pivot_index.toFixed(1)
                        : "--"}
                    </div>

                    {/* Trend */}
                    <div
                      style={{
                        ...MONO,
                        fontSize: 11,
                        color: trendUp
                          ? SIG
                          : trendDown
                          ? CORAL
                          : "rgba(255,255,255,0.4)",
                        textAlign: "right",
                      }}
                    >
                      {trendUp
                        ? `\u25b2 ${Math.abs(p.pi_l10_change).toFixed(1)}`
                        : trendDown
                        ? `\u25bc ${Math.abs(p.pi_l10_change).toFixed(1)}`
                        : `\u2014 ${Math.abs(p.pi_l10_change).toFixed(1)}`}
                    </div>
                  </div>
                );
              })}
        </div>

        {/* ── 4. BOTTOM BAR ───────────────────────────────────────────────── */}
        <div
          style={{
            position: "fixed",
            bottom: 0,
            left: 0,
            right: 0,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "12px 24px",
            background: "rgba(255, 255, 255, 0.04)",
            backdropFilter: "blur(24px) saturate(180%)",
            WebkitBackdropFilter: "blur(24px) saturate(180%)",
            borderTop: "0.5px solid rgba(255, 255, 255, 0.08)",
          }}
        >
          <span
            style={{
              ...MONO,
              fontSize: 10,
              color: "rgba(255,255,255,0.4)",
              letterSpacing: "0.04em",
            }}
          >
            &#x2191;&#x2193; NAVIGATE&nbsp;&nbsp;&nbsp;&#x23CE; OPEN&nbsp;&nbsp;&nbsp;&#x2318;K SEARCH
          </span>
          <span
            style={{
              ...MONO,
              fontSize: 10,
              color: "rgba(255,255,255,0.4)",
              letterSpacing: "0.04em",
            }}
          >
            {updateLabel ? `UPD ${updateLabel} PT` : ""}
          </span>
        </div>
      </div>
    </>
  );
}
