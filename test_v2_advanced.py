"""
test_v2_advanced.py
===================
Phase 2 deliverable #2: verify aggregate_season_advanced for Nikola Jokic (BDL ID 246, season 2024).

Sanity bounds:
  TS%     0.45 – 0.75
  usage   0.15 – 0.40
  off_rtg 95   – 135

Run from project root:
  python test_v2_advanced.py
"""

import asyncio
import json
import os
import sys

# Make sure the app package is importable
sys.path.insert(0, os.path.dirname(__file__))

# ── Minimal env so Settings() doesn't crash ──────────────────────────────────
os.environ.setdefault("BALLDONTLIE_API_KEY", "67dd3c73-0cda-49db-8c2e-53b0b7062b1d")
os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-not-needed-for-this-test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from app.services.nba_service import aggregate_season_advanced  # noqa: E402

JOKIC_BDL_ID = 246
SEASON = 2024

BOUNDS: dict[str, tuple[float, float]] = {
    "ts_pct":   (0.45, 0.75),
    "usage_pct": (0.15, 0.40),
    "off_rtg":  (95.0, 135.0),
}


async def main() -> None:
    print(f"\nFetching aggregate_season_advanced(player_id={JOKIC_BDL_ID}, season={SEASON}) ...\n")
    result = await aggregate_season_advanced(player_id=JOKIC_BDL_ID, season=SEASON)

    if not result:
        print("ERROR: aggregate_season_advanced returned empty dict.")
        sys.exit(1)

    # Pretty-print full dict
    print(json.dumps(result, indent=2, default=str))

    # Sanity checks
    print("\n── Sanity Bounds ─────────────────────────────────────────────────────────")
    all_pass = True
    for field, (lo, hi) in BOUNDS.items():
        val = result.get(field)
        if val is None:
            status = "MISSING"
            all_pass = False
        elif lo <= val <= hi:
            status = "PASS"
        else:
            status = f"FAIL  (expected {lo}–{hi}, got {val})"
            all_pass = False
        print(f"  {field:<20} {str(val):<12}  {status}")

    print()
    if all_pass:
        print("All sanity bounds PASSED.")
    else:
        print("One or more bounds FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
