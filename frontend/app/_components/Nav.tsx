"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/games",    label: "Games"    },
  { href: "/intel",    label: "Intel"    },
  { href: "/war-room", label: "War Room" },
  { href: "/compare",  label: "Compare"  },
  { href: "/coach",    label: "Coach"    },
  { href: "/chat",     label: "Chat"     },
];

export default function Nav() {
  const pathname = usePathname();

  return (
    <header
      style={{
        background: "var(--carbon)",
        borderBottom: "1px solid var(--wire)",
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}
    >
      <div
        style={{
          maxWidth: 1200,
          margin: "0 auto",
          padding: "0 24px",
          height: 52,
          display: "flex",
          alignItems: "center",
          gap: 32,
        }}
      >
        {/* Wordmark */}
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontWeight: 700,
            fontSize: 15,
            letterSpacing: "0.06em",
            color: "var(--neon)",
          }}
        >
          PIVOT
        </span>

        {/* Tabs */}
        <nav style={{ display: "flex", gap: 4 }}>
          {TABS.map(({ href, label }) => {
            const active = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  height: 30,
                  padding: "0 12px",
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  color: active ? "var(--bone)" : "var(--ash)",
                  background: active ? "var(--panel)" : "transparent",
                  border: active ? "1px solid var(--wire)" : "1px solid transparent",
                  textDecoration: "none",
                  transition: "color 0.15s, background 0.15s",
                }}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
