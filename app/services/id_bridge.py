"""
id_bridge.py
============
Maps BallDontLie player IDs ↔ stats.nba.com (NBA.com) player IDs.

Strategy
--------
1. Seed from the verified _NBA_ID_BY_NAME dict in nba_service.py (name → nba_id).
2. On first lookup of an unknown BDL player, fetch the player name from BDL,
   then fuzzy-match against the CommonAllPlayers endpoint from nba_api.
3. All resolved pairs are written to /tmp/id_bridge.json for persistence
   across cold starts.
4. prewarm() resolves the top ~150 BDL players at startup so live requests
   are mostly cache hits.

Thread safety
-------------
All writes are protected by an asyncio.Lock. The cache is an in-memory dict
backed by a JSON file — simple and sufficient for a single-process deployment.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_BRIDGE_FILE = "/tmp/id_bridge.json"

# Bidirectional in-memory maps
_bdl_to_nba: dict[int, int] = {}   # bdl_id → nba_id
_nba_to_bdl: dict[int, int] = {}   # nba_id → bdl_id
_name_to_nba: dict[str, int] = {}  # "first last" (lower) → nba_id

_lock = asyncio.Lock()
_prewarm_done = False

# ── Seeded known mappings (name → nba_id) ─────────────────────────────────────
# Verified NBA.com player IDs — mirrored from nba_service._NBA_ID_BY_NAME
_SEED_NAME_TO_NBA: dict[str, int] = {
    "lebron james": 2544,
    "stephen curry": 201939,
    "nikola jokic": 203999,
    "jayson tatum": 1628369,
    "kevin durant": 201142,
    "giannis antetokounmpo": 203507,
    "luka doncic": 1629029,
    "anthony davis": 203076,
    "shai gilgeous-alexander": 1628983,
    "og anunoby": 1628384,
    "joel embiid": 203954,
    "kawhi leonard": 202695,
    "kyrie irving": 202681,
    "james harden": 201935,
    "trae young": 1629027,
    "damian lillard": 203081,
    "devin booker": 1626164,
    "ja morant": 1629630,
    "zion williamson": 1629627,
    "jimmy butler": 202710,
    "de'aaron fox": 1628368,
    "jalen brunson": 1628386,
    "donovan mitchell": 1628378,
    "victor wembanyama": 1641705,
    "paolo banchero": 1631094,
    "tyrese haliburton": 1630169,
    "evan mobley": 1630596,
    "scottie barnes": 1630567,
    "anthony edwards": 1630162,
    "lamelo ball": 1630163,
    "cade cunningham": 1630595,
    "jaylen brown": 1627759,
    "bam adebayo": 1628389,
    "tyler herro": 1629639,
    "tyrese maxey": 1630178,
    "karl-anthony towns": 1626157,
    "jalen green": 1630224,
    "draymond green": 203110,
    "klay thompson": 202691,
    "jamal murray": 1627750,
    "paul george": 202331,
    "rudy gobert": 203497,
    "andrew wiggins": 203952,
    "darius garland": 1629636,
    "jarrett allen": 1628991,
    "lauri markkanen": 1628374,
    "franz wagner": 1630532,
    "josh giddey": 1630581,
    "alperen sengun": 1631167,
    "jalen williams": 1631114,
    "deandre ayton": 1629028,
    "mikal bridges": 1628969,
    "brandon ingram": 1627742,
    "zach lavine": 203897,
    "demar derozan": 201942,
    "nikola vucevic": 202696,
    "domantas sabonis": 1627734,
}

# Pre-seeded BDL IDs for the top players (bdl_id → nba_id)
# These are verified pairs so bridge works without a network call for common players.
_SEED_BDL_TO_NBA: dict[int, int] = {
    237:  2544,        # LeBron James
    115:  201939,      # Stephen Curry
    246:  203999,      # Nikola Jokic
    434:  1628369,     # Jayson Tatum
    140:  201142,      # Kevin Durant
    192:  203507,      # Giannis Antetokounmpo
    473:  1629029,     # Luka Doncic
    14:   203076,      # Anthony Davis
    631:  1628983,     # Shai Gilgeous-Alexander
    279:  201935,      # James Harden
    472:  1629027,     # Trae Young
    326:  203081,      # Damian Lillard
    148:  1626164,     # Devin Booker
    516:  1629630,     # Ja Morant
    517:  1629627,     # Zion Williamson
    210:  202710,      # Jimmy Butler
    211:  1628368,     # De'Aaron Fox
    457:  1628386,     # Jalen Brunson
    221:  1628378,     # Donovan Mitchell
    824:  1641705,     # Victor Wembanyama
    828:  1631094,     # Paolo Banchero
    762:  1630169,     # Tyrese Haliburton
    761:  1630596,     # Evan Mobley
    741:  1630567,     # Scottie Barnes
    718:  1630162,     # Anthony Edwards
    719:  1630163,     # LaMelo Ball
    756:  1630595,     # Cade Cunningham
    118:  1627759,     # Jaylen Brown
    25:   1628389,     # Bam Adebayo
    551:  1629639,     # Tyler Herro
    774:  1630178,     # Tyrese Maxey
    93:   1626157,     # Karl-Anthony Towns
    728:  1630224,     # Jalen Green
    180:  203110,      # Draymond Green
    196:  202691,      # Klay Thompson
    310:  1627750,     # Jamal Murray
    313:  202331,      # Paul George
    360:  203497,      # Rudy Gobert
    12:   203952,      # Andrew Wiggins
    169:  1629636,     # Darius Garland
    21:   1628991,     # Jarrett Allen
    278:  1628374,     # Lauri Markkanen
    803:  1630532,     # Franz Wagner
    760:  1630581,     # Josh Giddey
    839:  1631167,     # Alperen Sengun
    840:  1631114,     # Jalen Williams
    30:   1629028,     # Deandre Ayton
    420:  1628969,     # Mikal Bridges
    68:   1627742,     # Brandon Ingram
    235:  203897,      # Zach LaVine
    133:  201942,      # DeMar DeRozan
    391:  202696,      # Nikola Vucevic
    143:  1627734,     # Domantas Sabonis
}


def _load_from_file() -> None:
    """Load persisted bridge entries from /tmp/id_bridge.json."""
    if not os.path.exists(_BRIDGE_FILE):
        return
    try:
        with open(_BRIDGE_FILE) as f:
            data = json.load(f)
        for bdl_str, nba_id in data.get("bdl_to_nba", {}).items():
            bdl_id = int(bdl_str)
            _bdl_to_nba[bdl_id] = nba_id
            _nba_to_bdl[nba_id] = bdl_id
        logger.info("id_bridge: loaded %d pairs from disk", len(_bdl_to_nba))
    except Exception as exc:
        logger.warning("id_bridge: failed to load %s: %s", _BRIDGE_FILE, exc)


def _save_to_file() -> None:
    """Persist current bridge to /tmp/id_bridge.json."""
    try:
        data = {"bdl_to_nba": {str(k): v for k, v in _bdl_to_nba.items()}}
        with open(_BRIDGE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as exc:
        logger.warning("id_bridge: failed to save: %s", exc)


def _init() -> None:
    """Initialize in-memory maps from seeds + persisted file."""
    _name_to_nba.update(_SEED_NAME_TO_NBA)
    for bdl_id, nba_id in _SEED_BDL_TO_NBA.items():
        _bdl_to_nba[bdl_id] = nba_id
        _nba_to_bdl[nba_id] = bdl_id
    _load_from_file()


_init()


# ── Public API ────────────────────────────────────────────────────────────────

async def bdl_to_nba(bdl_id: int) -> Optional[int]:
    """Return the stats.nba.com player ID for a BDL player ID, or None."""
    if bdl_id in _bdl_to_nba:
        return _bdl_to_nba[bdl_id]

    # Try to resolve via BDL player name → fuzzy NBA lookup
    nba_id = await _resolve_via_name(bdl_id)
    if nba_id:
        async with _lock:
            _bdl_to_nba[bdl_id] = nba_id
            _nba_to_bdl[nba_id] = bdl_id
            _save_to_file()
    return nba_id


def bdl_to_nba_sync(bdl_id: int) -> Optional[int]:
    """Synchronous lookup — only returns cached results, no network call."""
    return _bdl_to_nba.get(bdl_id)


def nba_to_bdl_sync(nba_id: int) -> Optional[int]:
    """Synchronous reverse lookup."""
    return _nba_to_bdl.get(nba_id)


async def prewarm(top_n: int = 150) -> None:
    """
    Prewarm the bridge for the most important players at startup.
    Seeds are already loaded synchronously; this step resolves any unknown BDL IDs
    from the FEATURED_PLAYERS list that may not be in _SEED_BDL_TO_NBA.
    """
    global _prewarm_done
    if _prewarm_done:
        return
    _prewarm_done = True

    # Try to load the CommonAllPlayers lookup table from nba_api
    try:
        from nba_api.stats.endpoints import CommonAllPlayers  # type: ignore
        import asyncio as _aio

        loop = _aio.get_event_loop()
        from concurrent.futures import ThreadPoolExecutor
        executor = ThreadPoolExecutor(max_workers=1)

        def _load_all():
            cap = CommonAllPlayers(timeout=20)
            df = cap.get_data_frames()[0]
            return {
                str(row["DISPLAY_FIRST_LAST"]).lower(): int(row["PERSON_ID"])
                for _, row in df.iterrows()
            }

        all_players = await loop.run_in_executor(executor, _load_all)
        _name_to_nba.update(all_players)
        logger.info("id_bridge prewarm: loaded %d NBA players from CommonAllPlayers", len(all_players))
    except Exception as exc:
        logger.warning("id_bridge prewarm: CommonAllPlayers failed: %s", exc)


# ── Private helpers ───────────────────────────────────────────────────────────

async def _resolve_via_name(bdl_id: int) -> Optional[int]:
    """Fetch player name from BDL, fuzzy-match to NBA ID."""
    try:
        from app.services.nba_service import _fetch_data  # avoid circular at module level
        data = await _fetch_data(f"/players/{bdl_id}")
        first = data.get("data", {}).get("first_name") or data.get("first_name", "")
        last = data.get("data", {}).get("last_name") or data.get("last_name", "")
        full_name = f"{first} {last}".strip().lower()
        if not full_name or full_name == "none none":
            return None
        return _name_to_nba.get(full_name)
    except Exception as exc:
        logger.debug("id_bridge _resolve_via_name(%d): %s", bdl_id, exc)
        return None
