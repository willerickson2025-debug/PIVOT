import os
import sys
import time
import json
import requests
from datetime import datetime
from pathlib import Path

# Enforce root context for cloud execution
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))
os.chdir(ROOT_DIR)

API_KEY = os.environ.get("BALLDONTLIE_API_KEY")
if not API_KEY:
    raise ValueError("CRITICAL: BALLDONTLIE_API_KEY environment variable not set.")

BASE_GAMES_URL = "https://api.balldontlie.io/v1/games"
BASE_STATS_URL = "https://api.balldontlie.io/v1/stats"
HEADERS = {"Authorization": API_KEY}

POLL_INTERVAL_LIVE = 15
POLL_INTERVAL_IDLE = 60
RATE_LIMIT_SLEEP = 60

OUTPUT_DIR = ROOT_DIR / "data" / "live"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def now_iso():
    return datetime.utcnow().isoformat()

def append_jsonl(record, filename):
    path = OUTPUT_DIR / filename
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")

def safe_get(url, params=None):
    while True:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        
        if response.status_code == 429:
            time.sleep(RATE_LIMIT_SLEEP)
            continue
            
        if response.status_code >= 500:
            time.sleep(10)
            continue
            
        response.raise_for_status()
        return response.json()

def fetch_today_games():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    params = {
        "dates[]": [today],
        "seasons[]": [2025],
        "per_page": 100
    }
    data = safe_get(BASE_GAMES_URL, params)
    return data.get("data", [])

def get_live_games(games):
    live = []
    for g in games:
        status = g.get("status", "")
        if any(x in status for x in ["Qtr", "Halftime", "OT"]) or ":" in status:
            if "Final" not in status:
                live.append(g)
    return live

def fetch_box_score(game_id):
    params = {
        "game_ids[]": [game_id],
        "per_page": 100
    }
    data = safe_get(BASE_STATS_URL, params)
    return data.get("data", [])

def build_snapshot(game, stats):
    return {
        "timestamp": now_iso(),
        "game_id": game["id"],
        "status": game["status"],
        "home_team": game["home_team"]["full_name"],
        "visitor_team": game["visitor_team"]["full_name"],
        "home_score": game["home_team_score"],
        "visitor_score": game["visitor_team_score"],
        "period": game.get("period"),
        "clock": game.get("time"),
        "stats_count": len(stats),
        "player_stats": stats
    }

def run_live_tracker():
    print("PIVOT LIVE TRACKER STARTED")
    tracked_games = set()

    while True:
        try:
            games = fetch_today_games()
            live_games = get_live_games(games)

            if not live_games:
                print("No live games active. Idling.")
                time.sleep(POLL_INTERVAL_IDLE)
                continue

            print(f"Tracking {len(live_games)} live game(s).")

            for game in live_games:
                gid = game["id"]

                if gid not in tracked_games:
                    print(f"Init tracking: {game['visitor_team']['name']} @ {game['home_team']['name']}")
                    tracked_games.add(gid)

                stats = fetch_box_score(gid)
                snapshot = build_snapshot(game, stats)
                
                filename = f"game_{gid}.jsonl"
                append_jsonl(snapshot, filename)

                print(f"{snapshot['visitor_team']} {snapshot['visitor_score']} - {snapshot['home_team']} {snapshot['home_score']} | {snapshot['status']}")

            time.sleep(POLL_INTERVAL_LIVE)

        except KeyboardInterrupt:
            print("Tracker gracefully terminated by user.")
            break
            
        except Exception as e:
            print(f"System error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_live_tracker()