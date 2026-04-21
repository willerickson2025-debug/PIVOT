"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useApi } from "../_lib/api";

// ── Brand tokens ──────────────────────────────────────────────────────────────

const SIG = "#39FF14";
const CORAL = "#FF6B4A";
const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://pivot-app-production-1eb4.up.railway.app/api/v1";

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

// ── Constants ─────────────────────────────────────────────────────────────────


// ── Types ─────────────────────────────────────────────────────────────────────

interface IntelPlayer {
  name: string;
  slug: string;
  team: string;
  position: string;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  pie: number | null;
}

interface SearchResult {
  id: number;
  first_name: string;
  last_name: string;
  position: string;
  team?: { abbreviation?: string };
}

function nameToSlug(name: string) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
}

// ── Skeleton row ──────────────────────────────────────────────────────────────

function SkeletonRow({ idx }: { idx: number }) {
  return (
    <div
      className="pivot-top10-row"
      style={{
        display: "grid",
        borderBottom: "0.5px solid rgba(255,255,255,0.05)",
        padding: "14px 0",
        opacity: Math.max(0.15, 1 - idx * 0.085),
        alignItems: "center",
      }}
    >
      <div style={{ width: 14, height: 10, background: "rgba(255,255,255,0.08)", borderRadius: 2, marginLeft: 8 }} />
      <div style={{ paddingRight: 16 }}>
        <div style={{ width: "52%", height: 13, background: "rgba(255,255,255,0.09)", borderRadius: 2, marginBottom: 5 }} />
        <div style={{ width: "36%", height: 10, background: "rgba(255,255,255,0.05)", borderRadius: 2 }} />
      </div>
      <div style={{ width: 28, height: 13, background: "rgba(255,255,255,0.09)", borderRadius: 2, marginLeft: "auto", marginRight: 8 }} />
      <div style={{ width: 28, height: 13, background: "rgba(255,255,255,0.09)", borderRadius: 2, marginLeft: "auto", marginRight: 8 }} />
      <div style={{ width: 28, height: 13, background: "rgba(255,255,255,0.09)", borderRadius: 2, marginLeft: "auto", marginRight: 8 }} />
      <div style={{ width: 34, height: 13, background: "rgba(255,255,255,0.07)", borderRadius: 2, marginLeft: "auto", marginRight: 8 }} />
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
  const [results, setResults] = useState<SearchResult[]>([]);
  const [focused, setFocused] = useState(false);
  const [selectedIdx, setSelectedIdx] = useState(-1);
  const [hoveredRow, setHoveredRow] = useState<number | null>(null);
  const [updateLabel, setUpdateLabel] = useState("");

  const { data: leaderboardData, isLoading: top10Loading } = useApi<{
    players: Array<{ name: string; team: string; position: string; pts: number | null; reb: number | null; ast: number | null; pie: number | null }>;
  }>("/intel/leaderboard?limit=10&sort=pie");

  const top10: IntelPlayer[] = (leaderboardData?.players ?? []).map((p) => ({
    name: p.name,
    slug: nameToSlug(p.name),
    team: p.team,
    position: p.position,
    pts: p.pts,
    reb: p.reb,
    ast: p.ast,
    pie: p.pie,
  }));

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

  // Search: real backend player search
  const doSearch = useCallback((q: string) => {
    if (!q.trim()) {
      setResults([]);
      return;
    }
    fetch(`${BASE}/nba/players?name=${encodeURIComponent(q)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        // API returns { players: [...], count: n }
        const items: SearchResult[] = d.players ?? d.data ?? (Array.isArray(d) ? d : []);
        setResults(items.slice(0, 6));
      })
      .catch(() => setResults([]));
  }, []);

  useEffect(() => {
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => doSearch(query), 200);
    return () => clearTimeout(debounceRef.current);
  }, [query, doSearch]);

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
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
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
        const r = results[selectedIdx];
        router.push(`/intel/player/${nameToSlug(`${r.first_name} ${r.last_name}`)}`);
      }
    } else if (e.key === "Escape") {
      setFocused(false);
      inputRef.current?.blur();
    }
  }

  const showDropdown = focused && query.trim().length > 0;

  return (
    <>
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
          grid-template-columns: 32px 1fr 52px 52px 52px 72px;
        }
        @media (max-width: 480px) {
          .pivot-top10-row {
            grid-template-columns: 32px 1fr 52px 52px !important;
          }
          .pivot-hide-sm { display: none !important; }
        }
      `}</style>

      <div className="pivot-intel-root" style={{ ...HB, paddingBottom: 64 }}>

        {/* ── 1. TOP BAR ──────────────────────────────────────────────────── */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 24px" }}>
          <span style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.6)", letterSpacing: "1px" }}>
            PIVOT/TERM&nbsp;&nbsp;INTEL
          </span>
          <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: SIG, boxShadow: `0 0 6px ${SIG}` }} />
            <span style={{ ...MONO, fontSize: 11, color: SIG, letterSpacing: "1.2px" }}>LIVE</span>
          </div>
        </div>

        {/* ── 2. HERO ─────────────────────────────────────────────────────── */}
        <div style={{ paddingTop: 128, display: "flex", flexDirection: "column", alignItems: "center", paddingLeft: 24, paddingRight: 24 }}>
          <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.4)", letterSpacing: "1.4px", marginBottom: 20 }}>
            PLAYER INTEL
          </div>

          <div ref={containerRef} style={{ position: "relative", width: "100%", maxWidth: 680 }}>
            {/* Search bar */}
            <div
              style={{
                ...GLASS_PILL,
                position: "relative",
                boxShadow: focused ? `0 0 0 1px ${SIG}, 0 0 48px rgba(57, 255, 20, 0.3)` : "none",
                transition: "box-shadow 160ms ease",
              }}
            >
              <svg width="22" height="22" viewBox="0 0 22 22" fill="none"
                style={{ position: "absolute", left: 28, top: "50%", transform: "translateY(-50%)", pointerEvents: "none" }}>
                <circle cx="9.5" cy="9.5" r="6.5" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
                <path d="M14.5 14.5L19.5 19.5" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onFocus={() => setFocused(true)}
                onKeyDown={handleKeyDown}
                placeholder="Search any player..."
                autoComplete="off"
                spellCheck={false}
                style={{
                  ...MONO,
                  display: "block",
                  width: "100%",
                  fontSize: 22,
                  color: "#ffffff",
                  background: "transparent",
                  border: "none",
                  outline: "none",
                  padding: "28px 36px 28px 72px",
                  caretColor: SIG,
                }}
              />
            </div>

            {/* Autocomplete dropdown */}
            {showDropdown && (
              <div style={{ ...GLASS, position: "absolute", top: "calc(100% + 8px)", left: 0, right: 0, zIndex: 100, overflow: "hidden" }}>
                {results.length === 0 ? (
                  <div style={{ ...MONO, padding: "14px 20px", fontSize: 12, color: "rgba(255,255,255,0.4)", letterSpacing: "0.04em" }}>
                    No results for &ldquo;{query}&rdquo;
                  </div>
                ) : (
                  results.map((r, i) => {
                    const active = i === selectedIdx;
                    const fullName = `${r.first_name} ${r.last_name}`;
                    const slug = nameToSlug(fullName);
                    return (
                      <div
                        key={r.id}
                        onMouseEnter={() => setSelectedIdx(i)}
                        onMouseDown={(e) => { e.preventDefault(); router.push(`/intel/player/${slug}`); }}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          padding: "12px 20px",
                          cursor: "pointer",
                          background: active ? "rgba(255,255,255,0.06)" : "transparent",
                          borderLeft: active ? `2px solid ${SIG}` : "2px solid transparent",
                          transition: "background 80ms ease, border-color 80ms ease",
                        }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ ...HB, fontSize: 14, color: "#ffffff", marginBottom: 2 }}>{fullName}</div>
                          <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.6)", letterSpacing: "0.04em" }}>
                            {r.team?.abbreviation ?? "—"} · {r.position || "—"}
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>
        </div>

        {/* ── 3. TOP 10 ───────────────────────────────────────────────────── */}
        <div style={{ maxWidth: 860, margin: "0 auto", padding: "80px 24px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <span style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.6)", letterSpacing: "1.2px" }}>
              TOP 10 CURRENT
            </span>
            <span style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.4)", letterSpacing: "0.04em" }}>
              sorted by PIE
            </span>
          </div>

          {/* Column headers */}
          <div className="pivot-top10-row" style={{ display: "grid", padding: "0 0 8px", borderBottom: "0.5px solid rgba(255,255,255,0.1)" }}>
            {[
              { label: "#", sm: false },
              { label: "PLAYER", sm: false },
              { label: "PTS", sm: false },
              { label: "REB", sm: false },
              { label: "AST", sm: true },
              { label: "PIE", sm: true },
            ].map(({ label, sm }) => (
              <div
                key={label}
                className={sm ? "pivot-hide-sm" : undefined}
                style={{
                  ...MONO,
                  fontSize: 10,
                  color: "rgba(255,255,255,0.4)",
                  letterSpacing: "1px",
                  textAlign: label === "#" || label === "PLAYER" ? "left" : "right",
                  paddingLeft: label === "#" ? 8 : 0,
                  paddingRight: label !== "#" && label !== "PLAYER" ? 8 : 0,
                }}
              >
                {label}
              </div>
            ))}
          </div>

          {/* Data rows or skeletons */}
          {top10Loading
            ? Array.from({ length: 10 }, (_, i) => <SkeletonRow key={i} idx={i} />)
            : top10.map((p, i) => {
                const hovered = hoveredRow === i;
                return (
                  <div
                    key={p.slug}
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
                      background: hovered ? "rgba(255,255,255,0.04)" : "transparent",
                      borderLeft: hovered ? `2px solid ${SIG}` : "2px solid transparent",
                      transition: "background 160ms ease, border-color 160ms ease",
                    }}
                  >
                    {/* Rank */}
                    <div style={{ ...MONO, fontSize: 12, color: "rgba(255,255,255,0.4)", paddingLeft: 8 }}>{i + 1}</div>

                    {/* Name + meta */}
                    <div style={{ paddingRight: 16, minWidth: 0 }}>
                      <div style={{ ...HB, fontSize: 14, color: "#ffffff", marginBottom: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {p.name}
                      </div>
                      <div style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.6)", letterSpacing: "0.04em" }}>
                        {p.team} · {p.position}
                      </div>
                    </div>

                    {/* PTS */}
                    <div style={{ ...MONO_B, fontSize: 14, color: "#fff", textAlign: "right", paddingRight: 8 }}>
                      {p.pts != null ? p.pts.toFixed(1) : "—"}
                    </div>

                    {/* REB */}
                    <div style={{ ...MONO_B, fontSize: 14, color: "#fff", textAlign: "right", paddingRight: 8 }}>
                      {p.reb != null ? p.reb.toFixed(1) : "—"}
                    </div>

                    {/* AST */}
                    <div className="pivot-hide-sm" style={{ ...MONO_B, fontSize: 14, color: "#fff", textAlign: "right", paddingRight: 8 }}>
                      {p.ast != null ? p.ast.toFixed(1) : "—"}
                    </div>

                    {/* PIE */}
                    <div className="pivot-hide-sm" style={{ ...MONO_B, fontSize: 14, color: p.pie != null && p.pie > 0.1 ? SIG : "#fff", textAlign: "right", paddingRight: 8 }}>
                      {p.pie != null ? (p.pie * 100).toFixed(1) + "%" : "—"}
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
          <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)", letterSpacing: "0.04em" }}>
            &#x2191;&#x2193; NAVIGATE&nbsp;&nbsp;&nbsp;&#x23CE; OPEN&nbsp;&nbsp;&nbsp;&#x2318;K SEARCH
          </span>
          <span style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.4)", letterSpacing: "0.04em" }}>
            {updateLabel ? `UPD ${updateLabel} PT` : ""}
          </span>
        </div>
      </div>
    </>
  );
}
