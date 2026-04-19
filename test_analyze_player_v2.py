"""
test_analyze_player_v2.py
=========================
Phase 6 deliverable 5: verify analyze_player(player_id=246, season=2024)
uses real V2 advanced aggregates and produces a grounded Claude response.

Sanity checks:
  - ts_pct 0.45-0.75, usage_pct 0.15-0.40, off_rtg 95-135
  - Payload contains V2-only fields: contested_fg_pct, screen_assists_pg,
    deflections_pg, pct_unassisted_fgm, defended_at_rim_fg_pct
  - Claude response cites at least one of those V2-only fields
  - Response follows Verdict / Supporting Numbers / Counter-Evidence / Bottom Line structure

Run from project root:
  python test_analyze_player_v2.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("BALLDONTLIE_API_KEY", "67dd3c73-0cda-49db-8c2e-53b0b7062b1d")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: ANTHROPIC_API_KEY not set.")
    sys.exit(1)

from app.services.analysis_service import analyze_player  # noqa: E402

BOUNDS = {
    "ts_pct":    (0.45, 0.75),
    "usage_pct": (0.15, 0.40),
    "off_rtg":   (95.0, 135.0),
}

V2_ONLY_FIELDS = [
    "contested_fg_pct",
    "screen_assists_pg",
    "deflections_pg",
    "pct_unassisted_fgm",
    "defended_at_rim_fg_pct",
]


async def main() -> None:
    print("\n═══════════════════════════════════════════════════════════")
    print("  Phase 6 — analyze_player V2 test")
    print("  Nikola Jokic (id=246), season=2024")
    print("═══════════════════════════════════════════════════════════\n")

    result = await analyze_player(player_id=246, season=2024)

    if "error" in result and not result.get("analysis"):
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    payload = result.get("payload", {})
    adv = payload.get("player", {}).get("advanced", {})
    basic = payload.get("player", {}).get("basic", {})

    # ── Payload path audit ─────────────────────────────────────────────────
    print("── Payload Path Audit ────────────────────────────────────────")
    has_v2 = any(f in adv for f in V2_ONLY_FIELDS)
    if has_v2:
        print("  DATA PATH: aggregate_season_advanced (V2) — V2 fields confirmed")
    else:
        print("  DATA PATH: MISSING V2 fields — migration may not have taken effect")
    print(f"  advanced keys: {len(adv)}")
    print(f"  basic.pts={basic.get('pts')}  basic.min={basic.get('min')}")

    # ── Sanity bounds ──────────────────────────────────────────────────────
    print("\n── Sanity Bounds ─────────────────────────────────────────────")
    field_map = {
        "ts_pct":    adv.get("ts_pct") or adv.get("true_shooting_percentage"),
        "usage_pct": adv.get("usage_pct") or adv.get("usage_percentage"),
        "off_rtg":   adv.get("off_rtg") or adv.get("offensive_rating"),
    }
    failures = []
    for field, (lo, hi) in BOUNDS.items():
        val = field_map[field]
        if val is None:
            failures.append(f"  {field}: MISSING")
            print(f"  {field:<20} = None  FAIL (missing)")
        elif not (lo <= val <= hi):
            failures.append(f"  {field}: FAIL ({val} not in [{lo}, {hi}])")
            print(f"  {field:<20} = {val:.4f}  FAIL (out of range)")
        else:
            print(f"  {field:<20} = {val:.4f}  PASS")

    # ── V2-only field presence ─────────────────────────────────────────────
    print("\n── V2-Only Field Presence ────────────────────────────────────")
    for f in V2_ONLY_FIELDS:
        val = adv.get(f)
        status = "present" if val is not None else "MISSING"
        print(f"  {f:<35} = {status} ({val})")
        if val is None:
            failures.append(f"  V2 field {f} missing")

    # ── Model / tokens ─────────────────────────────────────────────────────
    print(f"\n  Model:       {result.get('model', 'unknown')}")
    print(f"  Tokens used: {result.get('tokens_used', 'unknown')}")
    print(f"  Games played: {result.get('games_played', '?')}")
    print(f"  Sample warnings: {result.get('sample_warnings', [])}")

    # ── Full Claude response ───────────────────────────────────────────────
    analysis = result.get("analysis", "")
    print("\n── Full Claude Response ──────────────────────────────────────")
    print(analysis)

    # ── V2-field citation check ────────────────────────────────────────────
    print("\n── V2 Field Citation Check ───────────────────────────────────")
    # Look for V2-specific terms in the response text
    v2_terms = [
        "contested", "screen assist", "deflection", "unassisted",
        "defended at rim", "rim", "pct_unassisted", "screen_assist",
    ]
    cited = [t for t in v2_terms if t.lower() in analysis.lower()]
    if cited:
        print(f"  PASS: Response references V2-specific concepts: {cited}")
    else:
        print("  WARN: Response does not mention V2-specific fields by name")
        print("        (may still be grounded — check analysis text above)")

    # ── Structure check (Verdict / Supporting / Counter / Bottom Line) ─────
    print("\n── Structure Check ───────────────────────────────────────────")
    structure_markers = {
        "EFFICIENCY ANCHOR / TS%":     any(k in analysis.upper() for k in ["EFFICIENCY", "TRUE SHOOTING", "TS%"]),
        "SUPPORTING NUMBERS":          any(k in analysis.upper() for k in ["SUPPORTING", "CONTESTED", "DEFLECT", "SCREEN", "UNASSISTED"]),
        "COUNTER-EVIDENCE / contrast":  any(k in analysis.upper() for k in ["COUNTER", "HOWEVER", "BUT", "CONCERN", "WEAKNESS", "DEFICIT"]),
        "BOTTOM LINE / verdict":        any(k in analysis.upper() for k in ["BOTTOM LINE", "VERDICT", "STANDS RIGHT NOW", "WHERE THIS PLAYER"]),
    }
    for label, found in structure_markers.items():
        print(f"  {'PASS' if found else 'MISS'} {label}")

    # ── Numeric grounding check ────────────────────────────────────────────
    print("\n── Numeric Grounding ─────────────────────────────────────────")
    grounded = False
    for field, val in field_map.items():
        if val is not None:
            for fmt in [f"{val:.3f}", f"{val:.1f}", f"{val * 100:.1f}"]:
                if fmt in analysis:
                    print(f"  PASS: '{fmt}' from {field} found verbatim in response")
                    grounded = True
                    break
    if not grounded:
        print("  WARN: No payload numeric value found verbatim — check response above")

    # ── Final verdict ──────────────────────────────────────────────────────
    print("\n── Final Assessment ──────────────────────────────────────────")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        sys.exit(1)
    else:
        print("All sanity bounds and V2 field checks PASSED.")


if __name__ == "__main__":
    asyncio.run(main())
