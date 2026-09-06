"""Scraping and parsing helpers for lol_scrape.ipynb. Pure functions,
tested against saved HTML fixtures in tests/fixtures/ so tests do not
need network access. lol_scrape.ipynb calls into this module to do the
actual gol.gg fetch/parse work; it only orchestrates parameters and
snapshot writing itself.
"""

from __future__ import annotations

import time
from urllib.parse import quote

import requests

BASE_URL = "https://gol.gg"
USER_AGENT = "lol-esports-pipeline-research/1.0 (+https://github.com/adamhibbert21/lol-esports-pipeline)"


def list_url(entity: str, season: str, split: str, tournament: str = "ALL") -> str:
    """Build a gol.gg list-page URL for teams, players, or champion (singular) stats."""
    return f"{BASE_URL}/{entity}/list/season-{season}/split-{split}/tournament-{quote(tournament)}/"


def team_matchlist_url(team_id: str, split: str, tournament: str = "ALL") -> str:
    """Build a gol.gg team match-list URL for one team."""
    return f"{BASE_URL}/teams/team-matchlist/{team_id}/split-{split}/tournament-{quote(tournament)}/"


def game_stats_url(game_id: str) -> str:
    """Build a gol.gg individual game page URL."""
    return f"{BASE_URL}/game/stats/{game_id}/page-game/"


def fetch_html(url: str, session: requests.Session, delay: float = 1.5, max_retries: int = 3) -> str:
    """Fetch a URL's HTML with a polite delay and retry-with-backoff on transient errors."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            time.sleep(delay)
            return response.text
        except requests.RequestException as error:
            last_error = error
            time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts") from last_error
