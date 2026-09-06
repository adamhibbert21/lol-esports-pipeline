"""Scraping and parsing helpers for lol_scrape.ipynb. Pure functions,
tested against saved HTML fixtures in tests/fixtures/ so tests do not
need network access. lol_scrape.ipynb calls into this module to do the
actual gol.gg fetch/parse work; it only orchestrates parameters and
snapshot writing itself.
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import quote

import pandas as pd
import requests

BASE_URL = "https://gol.gg"
USER_AGENT = "lol-esports-pipeline-research/1.0 (+https://github.com/adamhibbert21/lol-esports-pipeline)"

# gol.gg's Region column uses server-region codes, not league names.
# "NA" must use keep_default_na=False in pd.read_html or pandas treats it as missing data,
# silently dropping all LCS teams. See GOLGG_REGION_CODES usage in parse_list_table and filter_to_target_regions.
GOLGG_REGION_CODES: dict[str, str] = {"LCK": "KR", "LPL": "CN", "LEC": "EUW", "LCS": "NA"}


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


def parse_list_table(html: str) -> pd.DataFrame:
    """Parse a gol.gg list page's single stats table into a DataFrame."""
    from io import StringIO
    tables = pd.read_html(StringIO(html), keep_default_na=False)
    # Return the largest table by row count (data table, not filter UI tables)
    return max(tables, key=len)


def filter_to_target_regions(df: pd.DataFrame, regions: list[str]) -> pd.DataFrame:
    """Keep only rows whose Region column is one of the target regions.

    Only the teams list has a Region column; players and champion lists
    are fetched globally instead (see the plan's "Decisions carried
    over" section for why).
    Translates league names (LCK, LPL, LEC, LCS) to gol.gg region codes (KR, CN, EUW, NA).
    """
    golgg_codes = [GOLGG_REGION_CODES[region] for region in regions]
    return df[df["Region"].isin(golgg_codes)].reset_index(drop=True)


def hash_table(df: pd.DataFrame) -> str:
    """Order-independent content hash of a DataFrame, for the staleness guard."""
    normalized = df.sort_index(axis=1).sort_values(by=list(df.columns)).reset_index(drop=True)
    return hashlib.sha256(normalized.to_csv(index=False).encode("utf-8")).hexdigest()
