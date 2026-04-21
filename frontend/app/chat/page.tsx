"use client";

import { useState, useRef, useEffect } from "react";

// ── Brand tokens ──────────────────────────────────────────────────────────────

const SIG = "#39FF14";
const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://pivot-app-production-1eb4.up.railway.app/api/v1";

const HB: React.CSSProperties = {
  fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
  fontWeight: 700,
};
const HN: React.CSSProperties = {
  fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif',
  fontWeight: 400,
};
const MONO: React.CSSProperties = {
  fontFamily: '"JetBrains Mono", "Fira Code", ui-monospace, monospace',
  fontWeight: 400,
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface Message {
  role: "user" | "assistant";
  content: string;
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Scroll to bottom when messages update
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send() {
    const text = input.trim();
    if (!text || streaming) return;

    setInput("");
    setError(null);

    const userMsg: Message = { role: "user", content: text };
    const history = [...messages, userMsg];
    setMessages(history);

    // Placeholder for streaming assistant reply
    const placeholder: Message = { role: "assistant", content: "" };
    setMessages([...history, placeholder]);
    setStreaming(true);

    try {
      const res = await fetch(`${BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history.map((m) => ({ role: m.role, content: m.content })) }),
      });

      if (!res.ok || !res.body) {
        const t = await res.text();
        throw new Error(`${res.status}: ${t}`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        for (const line of chunk.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const evt = JSON.parse(raw) as { type: string; text?: string };
            if (evt.type === "chunk" && evt.text) {
              accumulated += evt.text;
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = { role: "assistant", content: accumulated };
                return updated;
              });
            } else if (evt.type === "done") {
              break;
            }
          } catch {
            // non-JSON line, skip
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      // Remove the empty placeholder
      setMessages((prev) => prev.slice(0, -1));
    } finally {
      setStreaming(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div style={{ background: "#000", minHeight: "100vh", color: "#fff", display: "flex", flexDirection: "column" }}>

      {/* Header */}
      <div
        style={{
          padding: "16px 24px",
          borderBottom: "0.5px solid rgba(255,255,255,0.07)",
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <div style={{ width: 7, height: 7, borderRadius: "50%", background: SIG, boxShadow: `0 0 6px ${SIG}` }} />
        <span style={{ ...HB, fontSize: 13, letterSpacing: "0.14em", color: "#fff" }}>PIVOT CHAT</span>
        <span style={{ ...MONO, fontSize: 11, color: "rgba(255,255,255,0.3)", marginLeft: 4 }}>NBA AI ASSISTANT</span>
      </div>

      {/* Message list */}
      <div style={{ flex: 1, overflowY: "auto", padding: "24px 24px 8px" }}>
        <div style={{ maxWidth: 720, margin: "0 auto", display: "flex", flexDirection: "column", gap: 20 }}>

          {messages.length === 0 && (
            <div style={{ textAlign: "center", paddingTop: 80 }}>
              <div style={{ ...HB, fontSize: "clamp(20px,3vw,32px)", color: "#fff", marginBottom: 8 }}>Ask anything.</div>
              <div style={{ ...HN, fontSize: 14, color: "rgba(255,255,255,0.35)", lineHeight: 1.6 }}>
                Player analysis, matchups, trade implications, draft intel — PIVOT knows the league.
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                flexDirection: m.role === "user" ? "row-reverse" : "row",
                gap: 12,
                alignItems: "flex-start",
              }}
            >
              {/* Avatar */}
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: "50%",
                  background: m.role === "user" ? "rgba(255,255,255,0.08)" : "rgba(57,255,20,0.12)",
                  border: m.role === "user" ? "1px solid rgba(255,255,255,0.1)" : `1px solid rgba(57,255,20,0.25)`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <span style={{ ...MONO, fontSize: 9, color: m.role === "user" ? "rgba(255,255,255,0.5)" : SIG, letterSpacing: "0.06em" }}>
                  {m.role === "user" ? "U" : "AI"}
                </span>
              </div>

              {/* Bubble */}
              <div
                style={{
                  maxWidth: "80%",
                  padding: "12px 16px",
                  borderRadius: m.role === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
                  background: m.role === "user" ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.03)",
                  border: m.role === "user" ? "1px solid rgba(255,255,255,0.1)" : "1px solid rgba(255,255,255,0.06)",
                }}
              >
                {m.content ? (
                  <p style={{ ...HN, fontSize: 14, color: "rgba(255,255,255,0.85)", lineHeight: 1.7, margin: 0, whiteSpace: "pre-wrap" }}>
                    {m.content}
                  </p>
                ) : (
                  <span style={{ ...MONO, fontSize: 14, color: SIG }}>▋</span>
                )}
              </div>
            </div>
          ))}

          {/* Error */}
          {error && (
            <div style={{ background: "rgba(255,107,74,0.08)", border: "1px solid rgba(255,107,74,0.2)", borderRadius: 10, padding: "12px 16px" }}>
              <span style={{ ...MONO, fontSize: 12, color: "#FF6B4A" }}>{error}</span>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* Input bar */}
      <div
        style={{
          padding: "16px 24px 24px",
          borderTop: "0.5px solid rgba(255,255,255,0.07)",
          flexShrink: 0,
        }}
      >
        <div style={{ maxWidth: 720, margin: "0 auto", display: "flex", gap: 10, alignItems: "flex-end" }}>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about the NBA…"
            rows={1}
            style={{
              ...HN,
              flex: 1,
              fontSize: 14,
              color: "#fff",
              background: "rgba(255,255,255,0.04)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 12,
              padding: "12px 16px",
              outline: "none",
              resize: "none",
              lineHeight: 1.6,
              caretColor: SIG,
              minHeight: 46,
              maxHeight: 160,
              overflowY: "auto",
            }}
          />
          <button
            onClick={send}
            disabled={streaming || !input.trim()}
            style={{
              ...HB,
              height: 46,
              padding: "0 20px",
              background: streaming || !input.trim() ? "rgba(57,255,20,0.15)" : SIG,
              color: streaming || !input.trim() ? "rgba(57,255,20,0.4)" : "#000",
              border: `1px solid ${streaming || !input.trim() ? "rgba(57,255,20,0.2)" : SIG}`,
              borderRadius: 12,
              fontSize: 12,
              letterSpacing: "0.1em",
              cursor: streaming || !input.trim() ? "not-allowed" : "pointer",
              transition: "all 120ms",
              flexShrink: 0,
            }}
          >
            {streaming ? "…" : "SEND"}
          </button>
        </div>
        <div style={{ ...MONO, fontSize: 10, color: "rgba(255,255,255,0.2)", textAlign: "center", marginTop: 8, letterSpacing: "0.04em" }}>
          ENTER to send · SHIFT+ENTER for newline
        </div>
      </div>
    </div>
  );
}
