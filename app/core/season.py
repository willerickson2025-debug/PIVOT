"""
season.py
=========
Active NBA season resolution for BallDontLie API calls.

BallDontLie identifies seasons by their *start* year: the 2025-26 season is
season 2025, the 2024-25 season is season 2024, and so on.

Season calendar mapping:
  October – December  →  new season just tipped off   →  return current year
  January – June      →  second half / playoffs        →  return current year - 1
  July – September    →  off-season                    →  return current year - 1
                                                           (most recently completed season)
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def get_current_season() -> int:
    """Return the BDL season integer for the currently active (or most recent) NBA season.

    Examples (all relative to ET wall-clock time):
      November 2025  →  2025   (2025-26 season just started)
      April    2026  →  2025   (2025-26 playoffs)
      August   2026  →  2025   (2025-26 off-season; last completed season)
      November 2026  →  2026   (2026-27 season just started)
    """
    now = datetime.datetime.now(_ET)
    # October (10), November (11), December (12) — a new season has just begun.
    if now.month >= 10:
        return now.year
    # January–September — we are either in the second half of the season that
    # started last October, or in the off-season between seasons.  Either way
    # the correct season identifier is the previous calendar year.
    return now.year - 1
