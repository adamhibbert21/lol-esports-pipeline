"""One-off script to (re)capture the HTML fixtures test_lol_scrape_lib.py runs against.

Run manually when gol.gg's page structure needs re-verifying:
    python tests/capture_fixtures.py
"""

from __future__ import annotations

import time
from pathlib import Path

import requests

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
USER_AGENT = "lol-esports-pipeline-research/1.0 (+https://github.com/adamhibbert21/lol-esports-pipeline)"

PAGES = {
    "teams_list.html": "https://gol.gg/teams/list/season-S16/split-Summer/tournament-ALL/",
    "players_list.html": "https://gol.gg/players/list/season-S16/split-Summer/tournament-ALL/",
    "champion_list.html": "https://gol.gg/champion/list/season-S16/split-Summer/tournament-ALL/",
    "team_matchlist.html": "https://gol.gg/teams/team-matchlist/2833/split-Summer/tournament-ALL/",
    "game_stats.html": "https://gol.gg/game/stats/80757/page-game/",
}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for filename, url in PAGES.items():
        response = session.get(url, timeout=30)
        response.raise_for_status()
        (FIXTURES_DIR / filename).write_text(response.text, encoding="utf-8")
        print(f"saved {filename} ({len(response.text)} bytes)")
        time.sleep(1.5)


if __name__ == "__main__":
    main()
