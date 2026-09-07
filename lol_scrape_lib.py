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
        except requests.HTTPError as error:
            if error.response is not None and 400 <= error.response.status_code < 500:
                raise RuntimeError(f"Failed to fetch {url}: {error.response.status_code} (not retrying a client error)") from error
            last_error = error
            time.sleep(delay * (attempt + 1))
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
    """Order-independent content hash of a DataFrame, for the staleness guard.

    Stringifies every value first: pd.read_html and pd.read_csv infer
    different dtypes for identical source text (e.g. "0.50" stays object
    from read_html but becomes float64 0.5 after a read_csv round-trip),
    so hashing typed values made identical content hash differently.
    Every caller comparing a freshly-parsed frame against a reloaded CSV
    must also read that CSV with dtype=str for this to work.
    """
    stringified = df.astype(str)
    normalized = stringified.sort_index(axis=1)
    normalized = normalized.sort_values(by=list(normalized.columns)).reset_index(drop=True)
    return hashlib.sha256(normalized.to_csv(index=False).encode("utf-8")).hexdigest()


def _require_columns(df: pd.DataFrame, columns: list[str], context: str) -> None:
    """Fail loudly if a parsed list table is missing an expected column.

    A silently-malformed list table (gol.gg renamed or removed a column)
    would corrupt every later notebook, so this raises instead of letting
    a KeyError surface later somewhere less obvious, or letting a
    since-renamed column silently vanish from a downstream selection.
    """
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{context}: missing expected column(s) {missing}, gol.gg's table schema may have changed")


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
    """Extract duration (seconds), patch, date, and game-in-series number from a game page.

    Duration/patch/date come from plain-text search, since they're short,
    distinctively formatted strings ("Game Time MM:SS", "vXX.YY",
    "YYYY-MM-DD") that are easy to find reliably regardless of exactly
    which element wraps them.

    game_number_in_series comes from the page <title> instead (confirmed
    against the fixture: "ANB vs Disruptors game 3 - Arabian League 2026
    Summer WEEK4 - Games of Legends"), which is gol.gg's own record of a
    game's position within its Bo1/Bo3/Bo5 series - not derivable from a
    single game page's other content, and not something this pipeline
    tracked before (a game_id alone doesn't say whether it's game 1 of a
    Bo1 or game 3 of a Bo3).
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)

    duration_match = re.search(r"Game Time\s*(\d+):(\d+)", text)
    minutes, seconds = duration_match.groups()

    patch_match = re.search(r"\bv(\d+\.\d+)\b", text)
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)

    title = soup.title.get_text() if soup.title else ""
    game_number_match = re.search(r"\bgame (\d+)\b", title, re.IGNORECASE)
    if game_number_match is None:
        raise ValueError("could not find 'game N' in the page title; adjust the selector above")

    return {
        "duration_seconds": int(minutes) * 60 + int(seconds),
        "patch": patch_match.group(1),
        "date": date_match.group(1),
        "game_number_in_series": int(game_number_match.group(1)),
    }


# gol.gg strips apostrophes from champion img alt text, but inconsistently
# cases the surrounding letters when it does (e.g. "KSante" but "Kaisa",
# "ChoGath" but "Chogath" - confirmed against real pick/ban data, not a
# single fixture). Casefolding both sides of the lookup handles every
# variant in one place instead of enumerating each casing gol.gg happens
# to use.
_CHAMPION_NAME_FIXES = {
    "ksante": "K'Sante",
    "kaisa": "Kai'Sa",
    "khazix": "Kha'Zix",
    "chogath": "Cho'Gath",
    "reksai": "Rek'Sai",
    "velkoz": "Vel'Koz",
    "belveth": "Bel'Veth",
    "kogmaw": "Kog'Maw",
}


def _normalize_champion_name(name: str) -> str:
    """Restore apostrophes gol.gg strips from certain champion names."""
    return _CHAMPION_NAME_FIXES.get(name.replace("'", "").lower(), name)


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

    A declined ban renders as a bare <img alt=""> with no wrapping <a> and
    no real champion (confirmed live, gol.gg game 80200); find("img", alt=True)
    only matches an <a>-wrapped image, so a declined ban's empty alt is
    correctly (if incidentally) never collected here.
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

    for team_index, team_picks in enumerate(picks, start=1):
        if len(team_picks) != 5:
            raise ValueError(f"team {team_index} has {len(team_picks)} picks, expected 5 (draft parse likely incomplete)")

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
    tournament: str,
    meta: dict,
    draft: dict,
    box_score: list[dict],
) -> dict:
    """Combine parsed pieces into one flat games.csv row.

    Per-player fields are pipe-delimited strings aligned by pick order,
    e.g. team_1_players.split("|")[i] played team_1_picks.split("|")[i].

    series_id groups the individual games (maps) of one Bo1/Bo3/Bo5 series
    together: gol.gg has no single ID for this, so it's derived from the
    two team names (sorted, since team_1/team_2 is page order, not a
    stable per-team identity) plus the date, which two teams share across
    every game of the same series. game_number_in_series (from meta, the
    game page's own "game N" title text) says where in that series this
    particular row falls.
    """
    team_1_box = [row for row in box_score if row["team"] == 1]
    team_2_box = [row for row in box_score if row["team"] == 2]

    def join(rows: list[dict], field: str) -> str:
        return "|".join(str(row[field]) for row in rows)

    series_id = "|".join(sorted([draft["team_1"], draft["team_2"]])) + "_" + meta["date"]

    return {
        "game_id": game_id,
        "series_id": series_id,
        "game_number_in_series": meta["game_number_in_series"],
        "region": region,
        "season": season,
        "split": split,
        "tournament": tournament,
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


# Single source of truth for games.csv's schema: must exactly match the keys
# assemble_game_row returns. Used by lol_scrape.ipynb to detect a stale
# prior-schema games.csv before carrying its rows forward (see Task 9 review
# finding on silent schema-mismatched carry-forward corruption).
GAMES_ROW_COLUMNS = [
    "game_id", "series_id", "game_number_in_series", "region", "season",
    "split", "tournament", "date", "patch", "duration_seconds", "team_1",
    "team_2", "winner", "team_1_side", "team_2_side", "team_1_bans",
    "team_2_bans", "team_1_picks", "team_2_picks", "team_1_players",
    "team_2_players", "team_1_kda", "team_2_kda", "team_1_cs", "team_2_cs",
]


def scrape_region_teams(
    region: str, season: str, split: str, tournament: str, session: requests.Session
) -> pd.DataFrame:
    """Fetch one region's real tournament roster, tagged with region/season/split/tournament.

    Fetching by a specific real tournament name (not "ALL" plus a
    server-region filter) is what actually narrows to the target league:
    gol.gg's Region column is a server region, which also contains that
    server's academy/challenger/collegiate teams. filter_to_target_regions
    stays as a defense-in-depth sanity check, not the primary filter.

    team_id is not part of gol.gg's table itself (pandas.read_html only
    sees the stats columns); it lives in each row's link href, so this
    parses the same raw HTML a second time, directly, for the
    name-to-team_id mapping.
    """
    html = fetch_html(list_url("teams", season, split, tournament), session)
    table = parse_list_table(html)
    _require_columns(table, ["Name", "Region"], f"teams list ({region}/{tournament})")
    df = filter_to_target_regions(table, [region])
    if len(table) > 0 and len(df) == 0:
        raise ValueError(f"teams list for {region}/{tournament} returned rows but none matched region code {GOLGG_REGION_CODES[region]}")
    soup = BeautifulSoup(html, "lxml")
    team_id_by_name = {}
    for link in soup.find_all("a", href=True):
        match = re.search(r"/team-stats/(\d+)/", link["href"])
        if match:
            team_id_by_name[link.get_text(strip=True)] = match.group(1)
    df["team_id"] = df["Name"].map(team_id_by_name)
    df = df.dropna(subset=["team_id"]).reset_index(drop=True)
    if len(team_id_by_name) > 0 and len(df) == 0:
        raise ValueError(f"teams list for {region}/{tournament} matched region rows but none resolved a team_id; gol.gg's team-stats link format may have changed")
    # Renamed from gol.gg's own "Region" (server code, e.g. "KR") so it can't
    # collide with the new project-vocabulary "region" (e.g. "LCK") column
    # below under a case-insensitive column lookup.
    df = df.rename(columns={"Region": "golgg_region"})
    df["region"], df["season"], df["split"], df["tournament"] = region, season, split, tournament
    return df


def scrape_global_list(
    entity: str, region: str, season: str, split: str, tournament: str, session: requests.Session
) -> pd.DataFrame:
    """Fetch one region's real-tournament players or champion list, tagged with region/season/split/tournament.

    Narrowed the same way scrape_region_teams is (players/champion lists
    have no Region column to filter afterward, so fetching by real
    tournament name is the only way to scope this to one league - the
    same gol.gg mechanism, confirmed live to work for these two entities
    too: LCK 2026 Rounds 3-4 returns 55 players, not the ~1371-row global
    list). Callers fetch and concat one call per target region.
    """
    html = fetch_html(list_url(entity, season, split, tournament), session)
    df = parse_list_table(html)
    expected = {"players": ["Player"], "champion": ["Champion"]}.get(entity, [])
    _require_columns(df, expected, f"{entity} list ({region}/{tournament})")
    df["region"], df["season"], df["split"], df["tournament"] = region, season, split, tournament
    return df


def scrape_region_games(
    region: str,
    season: str,
    split: str,
    tournament: str,
    teams_df: pd.DataFrame,
    session: requests.Session,
    already_fetched_ids: set[str],
    on_row,
    on_failure,
) -> None:
    """Walk every team's match list, fetch each new game, and report each parsed row (or failure) via callback.

    Resumable by construction: already_fetched_ids is whatever games.csv
    already has on disk, so a game already written is never re-fetched.
    A game that fails to fetch or parse is reported to on_failure and the
    walk continues; it does not stop the whole run.
    """
    matchlist_rows: list[dict] = []
    for _, team in teams_df.iterrows():
        team_id = team["team_id"]
        matchlist_html = fetch_html(team_matchlist_url(team_id, split, tournament), session)
        matchlist_rows.extend(parse_team_matchlist(matchlist_html))

    for game_id in discover_game_ids(matchlist_rows):
        if game_id in already_fetched_ids:
            continue
        try:
            game_html = fetch_html(game_stats_url(game_id), session)
            meta = parse_game_meta(game_html)
            draft = parse_game_draft(game_html)
            box_score = parse_box_score(game_html)
            row = assemble_game_row(game_id, region, season, split, tournament, meta, draft, box_score)
            on_row(row)
        except Exception as error:  # noqa: BLE001, one bad game must not stop the whole scrape
            on_failure({"game_id": game_id, "url": game_stats_url(game_id), "error": str(error)})
