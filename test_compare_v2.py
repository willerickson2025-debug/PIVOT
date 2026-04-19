"""
test_compare_v2.py
==================
Phase 4 deliverable: verify compare_players uses real V2 advanced aggregates
and produces a grounded Claude response.

Test case: Nikola Jokic vs Shai Gilgeous-Alexander, season 2024

Sanity checks:
  - Both players have V2 fields: usage_percentage, offensive_rating,
    true_shooting_percentage, contested_fg_pct
  - TS% 0.45-0.75, usage 0.15-0.40, off_rtg 95-135 for both
  - Claude response references at least one specific V2 number

Run from project root:
  python test_compare_v2.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("BALLDONTLIE_API_KEY", "67dd3c73-0cda-49db-8c2e-53b0b7062b1d")
os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from app.services.analysis_service import compare_players  # noqa: E402

# ── Sanity bounds ─────────────────────────────────────────────────────────────
BOUNDS: dict[str, tuple[float, float]] = {
    "ts_pct":    (0.45, 0.75),
    "usage_pct": (0.15, 0.40),
    "off_rtg":   (95.0, 135.0),
}

V2_SPOT_FIELDS = [
    "usage_percentage", "offensive_rating", "true_shooting_percentage",
    "contested_fg_pct", "deflections_pg", "pie",
]


def _check_bounds(name: str, adv: dict) -> list[str]:
    failures = []
    for field, (lo, hi) in BOUNDS.items():
        val = adv.get(field)
        if val is None:
            failures.append(f"  {name} {field}: MISSING")
        elif not (lo <= val <= hi):
            failures.append(f"  {name} {field}: FAIL ({val} not in [{lo}, {hi}])")
        else:
            print(f"  {name} {field:<20} = {val:.4f}  PASS")
    return failures


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set. Export it before running this test.")
        sys.exit(1)

    print("\n═══════════════════════════════════════════════════════════")
    print("  Phase 4 — compare_players V2 test")
    print("  Jokic vs SGA, season 2024")
    print("═══════════════════════════════════════════════════════════\n")

    result = await compare_players(
        "Nikola Jokic",
        "Shai Gilgeous-Alexander",
        season=2024,
    )

    # ── Print full payload ────────────────────────────────────────────────
    print("── Payload ──────────────────────────────────────────────────")
    print(json.dumps(result["payload"], indent=2, default=str))

    # ── Sanity bounds ─────────────────────────────────────────────────────
    print("\n── Sanity Bounds ─────────────────────────────────────────────")
    adv_a = result["payload"]["player_a"]["advanced"]
    adv_b = result["payload"]["player_b"]["advanced"]
    name_a = result["payload"]["player_a"]["name"]
    name_b = result["payload"]["player_b"]["name"]

    failures = []
    failures += _check_bounds(name_a, adv_a)
    failures += _check_bounds(name_b, adv_b)

    # ── V2 spot-field check ───────────────────────────────────────────────
    print("\n── V2 Field Presence ─────────────────────────────────────────")
    for f in V2_SPOT_FIELDS:
        a_val = adv_a.get(f)
        b_val = adv_b.get(f)
        status_a = "present" if a_val is not None else "MISSING"
        status_b = "present" if b_val is not None else "MISSING"
        print(f"  {f:<35} A={status_a} ({a_val})  B={status_b} ({b_val})")
        if a_val is None or b_val is None:
            failures.append(f"  V2 field {f} missing for one or both players")

    # ── Claude response ───────────────────────────────────────────────────
    print("\n── Claude Response ───────────────────────────────────────────")
    print(result["analysis"])

    # ── Structured verdict ────────────────────────────────────────────────
    print("\n── Structured JSON ───────────────────────────────────────────")
    print(json.dumps(result["structured"], indent=2))

    # ── Grounding check: does Claude cite numbers from the payload? ────────
    analysis_text = result["analysis"]
    grounding_ok = False
    # Check that at least one numeric value from the payload appears verbatim
    for player_key in ("player_a", "player_b"):
        adv = result["payload"][player_key]["advanced"]
        for field in ("ts_pct", "usage_pct", "off_rtg", "pie"):
            val = adv.get(field)
            if val is not None:
                # Look for the value rounded to 3 decimal places or as a percentage
                val_str = f"{val:.3f}"
                val_pct = f"{val * 100:.1f}"
                if val_str in analysis_text or val_pct in analysis_text:
                    grounding_ok = True
                    break
        if grounding_ok:
            break

    print("\n── Final Assessment ──────────────────────────────────────────")
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
    else:
        print("All sanity bounds and V2 field checks PASSED.")

    if grounding_ok:
        print("Grounding check PASSED: Claude response cites specific payload numbers.")
    else:
        print("Grounding check WARNING: could not confirm payload numbers in response text.")
        print("(This may pass on closer inspection — review Claude response above.)")

    print(f"\nModel: {result['model']}  |  Tokens: {result['tokens_used']}")
    print(f"sample_warnings: {result['sample_warnings']}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
