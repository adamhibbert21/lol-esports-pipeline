"""Scraping and parsing helpers for lol_scrape.ipynb. Pure functions,
tested against saved HTML fixtures in tests/fixtures/ so tests do not
need network access. lol_scrape.ipynb calls into this module to do the
actual gol.gg fetch/parse work; it only orchestrates parameters and
snapshot writing itself.
"""

from __future__ import annotations

import hashlib
import re
import time
from urllib.parse import quote, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString

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


def parse_team_matchlist(html: str) -> list[dict]:
    """Extract each game link (game_id and URL) from a team's match-list page."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for link in soup.find_all("a", href=True):
        match = re.search(r"/game/stats/(\d+)/", link["href"])
        if match:
            rows.append({"game_id": match.group(1), "url": urljoin(BASE_URL, link["href"])})
    return rows


def discover_game_ids(matchlist_rows: list[dict]) -> list[str]:
    """Dedup game IDs across one or more teams' match lists, preserving first-seen order."""
    return list(dict.fromkeys(row["game_id"] for row in matchlist_rows))


def parse_game_meta(html: str) -> dict:
    """Extract duration (seconds), patch, and date from a game page's plain text.

    Uses text search rather than specific tags/classes, since these three
    values are short, distinctively formatted strings ("Game Time MM:SS",
    "vXX.YY", "YYYY-MM-DD") that are easy to find reliably in the page's
    full text regardless of exactly which element wraps them.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    duration_match = re.search(r"Game Time\s*(\d+):(\d+)", text)
    minutes, seconds = duration_match.groups()

    patch_match = re.search(r"\bv(\d+\.\d+)\b", text)
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)

    return {
        "duration_seconds": int(minutes) * 60 + int(seconds),
        "patch": patch_match.group(1),
        "date": date_match.group(1),
    }


# gol.gg strips apostrophes from champion img alt text (e.g. "KSante" instead
# of "K'Sante"). Confirmed against the real game_stats.html fixture, where the
# box score's champion icons for K'Sante come through with no apostrophe.
# Listed here are the known LoL champions whose names contain one.
_CHAMPION_NAME_FIXES = {
    "KSante": "K'Sante",
    "KaiSa": "Kai'Sa",
    "KhaZix": "Kha'Zix",
    "ChoGath": "Cho'Gath",
    "RekSai": "Rek'Sai",
    "VelKoz": "Vel'Koz",
    "BelVeth": "Bel'Veth",
}


def _normalize_champion_name(name: str) -> str:
    """Restore apostrophes gol.gg strips from certain champion names."""
    return _CHAMPION_NAME_FIXES.get(name, name)


def _side_from_element(el) -> str | None:
    """Best-effort Blue/Red side from a team-name element's class or inline color style.

    Confirmed (by the user, inspecting the live page) that gol.gg conveys
    side through team-name text color rather than a literal "Blue"/"Red"
    label, so this checks both the class list and any inline style for
    color-ish cues. Returns None if neither carries a usable signal, since
    an external stylesheet this fixture doesn't include could be what
    actually applies the color.
    """
    haystack = " ".join(el.get("class", [])) + " " + el.get("style", "")
    if re.search(r"blue", haystack, re.IGNORECASE):
        return "Blue"
    if re.search(r"red", haystack, re.IGNORECASE):
        return "Red"
    return None


def _champion_names_in_container(container) -> list[str]:
    """Collect champion img alt-text from a bans/picks container's direct children.

    Real markup (confirmed against the fixture): each container mixes
    <a><img alt="Champion"></a> tags with bare "|" text nodes marking draft
    phase boundaries (e.g. bans phase 1 vs phase 2, or picks phase 1 vs
    phase 2). Both bans and picks want the full ordered list regardless of
    phase, so "|" text nodes are just skipped.
    """
    names = []
    for child in container.children:
        if isinstance(child, NavigableString):
            continue
        img = child.find("img", alt=True)
        if img and img.get("alt", "").strip():
            names.append(_normalize_champion_name(img["alt"]))
    return names


def parse_game_draft(html: str) -> dict:
    """Extract team names, bans, picks, and side (if determinable) from a game page.

    gol.gg's per-side team-name-plus-outcome header (e.g. "Anubis Gaming -
    WIN") uses the team's long name, but the test oracle's team names are
    the short forms gol.gg uses in its page <h1> (e.g. "ANB vs
    Disruptors"), so team names come from there instead. team_1/team_2
    means "whichever team's <h1> entry and page section come first".
    gol.gg orders both consistently by outcome-header side, not by map
    side, and the header order matches the <h1> order in every fixture
    checked.

    Side comes from _side_from_element applied to each per-team header div,
    whose class literally contains "blue-line-header" / "red-line-header"
    on the real fixture (a genuine class-name signal, not a guess).
    """
    soup = BeautifulSoup(html, "lxml")

    h1 = next((h for h in soup.find_all("h1") if " vs " in h.get_text()), None)
    if h1 is None:
        raise ValueError("could not find the 'X vs Y' page header; adjust the selector above")
    team_names = [name.strip() for name in h1.get_text(strip=True).split(" vs ", 1)]

    # The "blue-line-header"/"red-line-header" class also decorates a box-score
    # table header and a small scoreboard abbreviation elsewhere on the page;
    # only the per-team outcome header carries "WIN"/"LOSS" text, so that's
    # what distinguishes it from those other reuses of the same class name.
    headers = [
        el for el in soup.find_all(class_=re.compile(r"^(blue|red)-line-header$"))
        if re.search(r"WIN|LOSS", el.get_text(), re.IGNORECASE)
    ]
    if len(headers) < 2:
        raise ValueError("could not find two team outcome headers on the game page; adjust the selector above")

    team_sides = [_side_from_element(header) for header in headers[:2]]
    winner = "team_1" if "WIN" in headers[0].get_text().upper() else "team_2"

    bans: list[list[str]] = []
    picks: list[list[str]] = []
    for header in headers[:2]:
        team_block = header.parent.parent
        bans_label = team_block.find(string=re.compile(r"^\s*Bans"))
        picks_label = team_block.find(string=re.compile(r"^\s*Picks"))
        ban_container = bans_label.find_parent("div").find_next_sibling("div", class_="col-10") if bans_label else None
        pick_container = picks_label.find_parent("div").find_next_sibling("div", class_="col-10") if picks_label else None
        bans.append(_champion_names_in_container(ban_container) if ban_container else [])
        picks.append(_champion_names_in_container(pick_container) if pick_container else [])

    return {
        "team_1": team_names[0],
        "team_2": team_names[1],
        "winner": winner,
        "team_1_side": team_sides[0],
        "team_2_side": team_sides[1],
        "team_1_bans": bans[0],
        "team_2_bans": bans[1],
        "team_1_picks": picks[0],
        "team_2_picks": picks[1],
    }


def parse_box_score(html: str) -> list[dict]:
    """Extract each player's box score row (team 1 or 2, player, champion, KDA, CS).

    Real markup (confirmed against the fixture): each team's box score is a
    separate <table class="playersInfosLine">, first table's players go to
    team 1 and second table's to team 2 (matching parse_game_draft's "first
    on the page" convention). Player rows are direct-child <tr>s of the
    table (its <thead> row must be skipped, and its nested per-player rune
    tooltip <table> must not be recursed into, hence recursive=False).
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table", class_="playersInfosLine")

    rows = []
    for team_index, table in enumerate(tables[:2], start=1):
        for row_el in table.find_all("tr", recursive=False):
            tds = row_el.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            first_td = tds[0]
            champion_img = first_td.find("img", alt=True)
            player_link = first_td.find("a", class_="link-blanc")
            kda_match = re.search(r"(\d+)/(\d+)/(\d+)", tds[-2].get_text(strip=True))
            cs_text = tds[-1].get_text(strip=True)
            rows.append({
                "team": team_index,
                "player": player_link.get_text(strip=True) if player_link else "",
                "champion": _normalize_champion_name(champion_img["alt"]) if champion_img else "",
                "kda": f"{kda_match.group(1)}/{kda_match.group(2)}/{kda_match.group(3)}" if kda_match else "",
                "cs": int(cs_text) if cs_text.isdigit() else 0,
            })
    return rows


def assemble_game_row(
    game_id: str,
    region: str,
    season: str,
    split: str,
    meta: dict,
    draft: dict,
    box_score: list[dict],
) -> dict:
    """Combine parsed pieces into one flat games.csv row.

    Per-player fields are pipe-delimited strings aligned by pick order,
    e.g. team_1_players.split("|")[i] played team_1_picks.split("|")[i].
    """
    team_1_box = [row for row in box_score if row["team"] == 1]
    team_2_box = [row for row in box_score if row["team"] == 2]

    def join(rows: list[dict], field: str) -> str:
        return "|".join(str(row[field]) for row in rows)

    return {
        "game_id": game_id,
        "region": region,
        "season": season,
        "split": split,
        "date": meta["date"],
        "patch": meta["patch"],
        "duration_seconds": meta["duration_seconds"],
        "team_1": draft["team_1"],
        "team_2": draft["team_2"],
        "winner": draft["winner"],
        "team_1_side": draft["team_1_side"],
        "team_2_side": draft["team_2_side"],
        "team_1_bans": "|".join(draft["team_1_bans"]),
        "team_2_bans": "|".join(draft["team_2_bans"]),
        "team_1_picks": "|".join(draft["team_1_picks"]),
        "team_2_picks": "|".join(draft["team_2_picks"]),
        "team_1_players": join(team_1_box, "player"),
        "team_2_players": join(team_2_box, "player"),
        "team_1_kda": join(team_1_box, "kda"),
        "team_2_kda": join(team_2_box, "kda"),
        "team_1_cs": join(team_1_box, "cs"),
        "team_2_cs": join(team_2_box, "cs"),
    }
