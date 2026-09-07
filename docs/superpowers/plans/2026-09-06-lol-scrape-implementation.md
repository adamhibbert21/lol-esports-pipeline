# lol_scrape.ipynb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build lol_scrape.ipynb, the first pipeline notebook, which scrapes gol.gg for LCK/LPL/LEC/LCS team, player, champion, and game data and caches it into dated data_cache snapshots.

**Architecture:** Real scraping/parsing logic lives in a new, unit-tested `lol_scrape_lib.py` (pure functions tested against saved HTML fixtures, no live network calls in tests). `lol_scrape.ipynb` is a thin orchestrator: a Parameters cell, then cells that call `lol_scrape_lib` plus the existing `lol_lib.py` (paths, regions, snapshot dirs) to run a real scrape and write snapshots. Game discovery/fetch is resumable: game IDs are collected first, then fetched one at a time with each row appended to `games.csv` immediately, so a crash mid-run does not lose prior progress.

**Tech Stack:** Python, requests, beautifulsoup4 (lxml parser), pandas, pytest. All already installed; no new dependencies needed.

**Spec:** docs/superpowers/specs/2026-09-06-lol-scrape-design.md

## Global Constraints

- No em-dashes anywhere, in code, comments, or docs (README.txt convention).
- `from __future__ import annotations` plus grouped imports (stdlib, then third-party, then local) in every new Python file (README.txt convention).
- Type hints and docstrings throughout (README.txt convention).
- Prefer the simplest approach that gives an equivalent result over a more complex one (explicit user preference).
- Comments stay short, explaining general purpose only; go into more depth only where the logic is genuinely non-obvious, e.g. the resumability/dedup/hash-guard logic (explicit user preference).
- Politeness: every HTTP request goes through `fetch_html`, which enforces a delay and retry-with-backoff. gol.gg has no public API and is not built for scraping load.
- Regions are always LCK, LPL, LEC, LCS, from `lol_lib.REGIONS` (README.txt SCOPE). Nothing else hardcodes this list a second time.
- `data_cache/` is gitignored; `tests/fixtures/*.html` ARE committed (small, needed so tests run without network access).

---

## Decisions carried over from design-phase clarification (not in the original spec doc)

These were resolved while turning the spec into this plan, confirmed live against gol.gg, and approved by the user:

1. **players.csv and champions.csv are NOT region-split.** gol.gg's players/champion list pages have no Region column (confirmed live), and reliably discovering per-league tournament filter values isn't practical without guessing. They are fetched once per season/split, globally (tournament=ALL), and saved under a new `global_scrape_snapshot_dir()` path in `lol_lib.py` (sibling to the per-region `scrape_snapshot_dir()`), not under any region folder. `teams.csv` and `games.csv` remain region-scoped, since those ARE on the model's critical path.
2. **gol.gg's game page does not label sides as Blue/Red in the visible text**, only winner/loser (confirmed live), but the user confirmed side is conveyed by the color of each team's name text on the game page itself. `games.csv` uses `team_1`/`team_2` (page order) plus a `winner` column (`"team_1"` or `"team_2"`), with `team_1_side`/`team_2_side` derived in `parse_game_draft` from that color/class, not guessed from the match-list page (dropped that approach, see Task 5/6 below: color on the game page is a more reliable source than hoping the word "Blue"/"Red" appears as literal matchlist text, which the WebFetch checks so far have not found).
3. **A game discovered via a target-region team's match list is attributed to the region currently being scraped**, even if the opponent is from a different region (e.g. an international event game). A genuinely cross-region match may end up in both regions' `games.csv` when each region is scraped; `lol_features.ipynb` can de-duplicate on `game_id` later if it combines all 4 regions into one modeling dataset. This is a deliberate simplification: chasing exact single-region ownership for cross-region games is not worth the complexity it would add.
4. **games.csv is one row per game.** Per-player box score fields (player, KDA, CS) are stored as pipe-delimited strings, parallel across `team_N_players` / `team_N_kda` / `team_N_cs` / `team_N_picks`, aligned by position, rather than a separate per-player table or JSON blob. Simplest way to keep full box score data without adding a fifth output table or a nested format.

5. **Region filtering moves from server-region code to real per-tournament fetch (resolves the OPEN TECHNICAL QUESTION and supersedes decision-carried-over item, discovered post-merge-review, 2026-09-07).** Task 4's `GOLGG_REGION_CODES` server-region filter (KR/EUW/NA/CN) was found in the final whole-branch review to silently include academy, challenger, and collegiate teams alongside the real league (e.g. LCK's 20 "teams" included "T1 Esports Academy" and "Gen.G Global Academy"; LCS's 18 included US collegiate teams). Confirmed live: gol.gg's `tournament-<name>/` URL segment (already present in `list_url`/`team_matchlist_url` from Task 3, previously only ever called with the default `tournament="ALL"`) accepts a real tournament name and correctly narrows both the teams list and each team's match list to just that competition - verified against `https://gol.gg/teams/list/season-S16/split-Summer/tournament-<real name>/` returning exactly 10 LCK teams, 10 LEC, 8 LCS, 12 LPL, with none of the lower-league contamination. Real tournament names (not the league abbreviation) are required and were discovered via gol.gg's tournament-list AJAX endpoint (`POST https://gol.gg/tournament/ajax.trlist.php`, form data `season=<S>&league[]=<LCK|LEC|LCS|LPL>`, returns JSON `[{trname, region, nbgames, firstgame, lastgame}, ...]`), which also revealed that leagues do not share one split-naming convention: LEC/LCS use Spring/Summer, LPL uses numbered splits ("Split 2", "Split 3"), and LCK 2026 uses neither (round-based stages: "Rounds 1-2", "Road to MSI", "Rounds 3-4", playoffs). User decision: replace the single shared `SPLIT` parameter with an explicit per-region `TOURNAMENTS: dict[str, str]` map of real tournament names that the user updates by hand each time they want a different period, defaulting to each region's current regular season (no Playoffs/Play-In): `{"LCK": "LCK 2026 Rounds 3-4", "LEC": "LEC 2026 Summer Season", "LCS": "LCS 2026 Summer", "LPL": "LPL 2026 Split 3"}`. `GOLGG_REGION_CODES`/`filter_to_target_regions` stay in place as a defense-in-depth sanity filter (now expected to be a no-op most of the time, since the tournament fetch already narrows correctly) rather than being removed.
6. **Snapshot self-consistency ruling (final-review finding I1, 2026-09-07).** The Task 8 fix that corrected `if not teams_unchanged or games_path.exists():` to `if not teams_unchanged:` exposed a real gap: `games.csv` is unconditionally written fresh into every dated folder (matching spec DATA FLOW step 3's crash-resilience intent) but `teams.csv` was being skipped whenever unchanged, so an unchanged-teams day could produce a dated folder with `games.csv` and no `teams.csv` - contradicting spec DATA FLOW step 4's "write all 4 tables together into a fresh folder, even the tables that did not change, so every dated snapshot is a self-consistent full set." Ruling: step 4's literal self-consistency requirement wins over its own "skip creating a new dated folder" optimization, since the latter is explicitly framed in the spec as a storage-cost tradeoff ("acceptable since data_cache/ is gitignored and fully regenerable") while the former is framed as a correctness requirement for downstream notebooks. `teams.csv` is now always written into every dated folder (dropping the per-file skip entirely); the hash-guard's comparison is kept only to decide what to print (whether content actually changed), not to gate any write. `games.csv` itself changes from same-day-only resumability to cross-day carry-forward: at the start of each region's block, the previous latest snapshot's `games.csv` rows (if any) are read and re-written immediately into today's fresh `games.csv` before the fetch loop starts (preserving Step 3's per-game crash safety for everything fetched during the run itself), then `already_fetched_ids` is seeded from those carried-forward rows so only genuinely new games get fetched and appended. This makes every dated folder a complete, self-contained snapshot (matching spec) without sacrificing either crash-resilience or the incremental-fetch cost savings.
7. **Hash-guard dtype bug (final-review finding C2, 2026-09-07).** `hash_table` was hashing a freshly-`pd.read_html`-parsed frame against a `pd.read_csv` round-trip of the same content; the two readers infer different dtypes for the same source text (e.g. `"0.50"` stays `object` from `read_html` but becomes `float64` `0.5` after a `read_csv` round-trip), so identical content hashed differently for 3 of 4 regions - confirmed by comparing two byte-identical on-disk `teams.csv` snapshots from different dates that both got rewritten because the guard never once returned "unchanged" for them. Fix (verified against real captured data): `hash_table` normalizes with `df.astype(str)` before sorting/hashing, and every comparison read of a previously-saved CSV uses `dtype=str` alongside the existing `keep_default_na=False`. `hash_table`'s sort key also had a latent bug worth folding into the same fix: `sort_values(by=list(df.columns))` sorted by the *pre-sort* column order rather than `list(normalized.columns)`, which could tie-break identical content differently depending on original column order.
8. **Champion-name normalization had 3 dead keys (final-review finding I2, 2026-09-07).** `_CHAMPION_NAME_FIXES` mapped `KaiSa`/`ChoGath`/`BelVeth` to their apostrophe forms, but gol.gg actually emits `Kaisa`/`Chogath`/`Belveth` (lowercase second element) for those three specific champions while using `KSante`/`KhaZix`/`RekSai`/`VelKoz` (no internal case break at all) for the others that do work - measured 209 of 3944 pick/ban occurrences left unnormalized. Fix: the lookup itself becomes casefold-insensitive (key on `name.replace("'", "").lower()`), which fixes all cases in one change rather than adding more special-case keys.

## Confirmed gol.gg facts this plan's tests rely on (verified live, 2026-09-06)

- Current season: S16. List URL pattern: `https://gol.gg/<entity>/list/season-<S>/split-<Split>/tournament-<name|ALL>/`, entity is `teams`, `players`, or `champion` (singular, note the irregular pluralization).
- Teams list has a `Region` column; players/champion lists do not.
- Team page: `./team-stats/<team_id>/split-<Split>/tournament-<name>/`. Anubis Gaming = team_id `2833`.
- Team match list: `https://gol.gg/teams/team-matchlist/<team_id>/split-<Split>/tournament-<name>/`, links to games at `../game/stats/<game_id>/page-game/`.
- Team 2833's S16 Summer match list includes (at minimum) game IDs: 80757, 80756, 80755, 80747, 80746, 80380, 80379, 80039, 80038, 79884, 79883, 79872, 79871.
- Game 80757 (ANB vs Disruptors): duration "Game Time 21:45" (= 1305 seconds), patch "v16.15", date "2026-08-05". ANB won. Bans are 5 per team across two phases separated by a literal "|" divider in the real markup: ANB bans: Nocturne, Jayce, Anivia, Jhin, Gnar. Disruptors bans: Cassiopeia, Camille, Syndra, Alistar, Nautilus. (An earlier pass at this document, based on a summarized page fetch rather than raw HTML, listed only the first 3 bans per team; corrected here after Task 6 found the second phase during implementation.) ANB picks (pick order): Ryze, Rumble, Xin Zhao, Sivir, Lulu. Disruptors picks: Aatrox, Viktor, Rell, K'Sante, Ahri. Box score:
  - ANB: Giyuu/Ryze/5-1-13/222cs, Maged/Rumble/4-1-10/190cs, Theocacs/Xin Zhao/7-0-14/185cs, Shy Carry/Sivir/12-0-12/216cs, B Butcher/Lulu/0-2-22/26cs
  - Disruptors: owlonsky/Aatrox/0-5-1/155cs, Skream/Viktor/1-4-0/172cs, sas/Rell/1-8-2/24cs, Chakroun/K'Sante/1-5-0/161cs, Random/Ahri/1-6-3/171cs

## Confirmed gol.gg facts for Task 9 (verified live, 2026-09-07)

- Real tournament names for the current S16 season (from `POST https://gol.gg/tournament/ajax.trlist.php`, form data `season=S16&league[]=<LEAGUE>`, JSON response array of `{trname, region, nbgames, firstgame, lastgame}` per tournament stage, most recent first): LCK has `LCK 2026 Season Playoffs`, `LCK 2026 Season Play-In`, `LCK 2026 Rounds 3-4`, `LCK 2026 Road to MSI`, `LCK 2026 Rounds 1-2`, `LCK Cup 2026`. LEC has `LEC 2026 Summer Playoffs`, `LEC 2026 Summer Season`, `LEC 2026 Spring Playoffs`, `LEC 2026 Spring Season`, `LEC 2026 Versus Playoffs`, `LEC 2026 Versus Season`. LCS has `LCS 2026 Summer`, `LCS 2026 Spring Playoffs`, `LCS 2026 Spring`, `LCS 2026 Lock-In`. LPL has `LPL 2026 Grand Finals`, `LPL 2026 Split 3`, `LPL 2026 Split 2 Playoffs`, `LPL 2026 Split 2`, `LPL 2026 Split 1 Playoffs`, `LPL 2026 Split 1`.
- The chosen `TOURNAMENTS` map value for each region is the most recent non-playoff, non-play-in stage: `LCK 2026 Rounds 3-4`, `LEC 2026 Summer Season`, `LCS 2026 Summer`, `LPL 2026 Split 3`.
- `https://gol.gg/teams/list/season-S16/split-Summer/tournament-<url-encoded real name>/` (the `split` segment is accepted but appears not to affect the result once a specific tournament name is given - confirmed identical response bytes for `split-Summer` vs `split-ALL` with the same tournament name; existing code keeps passing the current `SPLIT` value through unchanged, since it is harmless and the URL still requires some value there) returns a teams table filtered to exactly that tournament's real roster: `LCK 2026 Rounds 3-4` -> 10 teams (BNK FearX, DN SOOPers, Dplus KIA, Gen.G, HANJIN BRION, Hanwha Life Esports, Kiwoom DRX, KT Rolster, Nongshim RedForce, T1 - no academy teams). `LEC 2026 Summer Season` -> 10 teams (Fnatic, G2 Esports, GIANTX, Karmine Corp, Movistar KOI, Natus Vincere, Shifters, SK Gaming, Team Heretics, Team Vitality). `LCS 2026 Summer` -> 8 teams (Cloud9, Dignitas, Disguised, FlyQuest, LYON, Sentinels, Shopify Rebellion, Team Liquid - no collegiate teams). `LPL 2026 Split 3` -> 12 teams (Anyone's Legend, Bilibili Gaming, EDward Gaming, Invictus Gaming, JD Gaming, LGD Gaming, LNG Esports, Ninjas in Pyjamas, Team WE, ThunderTalk Gaming, Top Esports, Weibo Gaming).
- `team_matchlist_url` with the same real tournament name also narrows correctly: T1 (team_id 2809) filtered to `LCK 2026 Rounds 3-4` returns 19 games, not the team's full-season list.

---

### Task 1: Global (non-region) snapshot path helpers in lol_lib.py

**Files:**
- Modify: `lol_lib.py`
- Test: `test_lol_lib.py` (new file)

**Interfaces:**
- Produces: `lol_lib.global_scrape_snapshot_dir(date: dt.date | None = None) -> Path`, `lol_lib.latest_global_scrape_snapshot() -> Path | None`

- [ ] **Step 1: Write the failing tests**

Create `test_lol_lib.py`:

```python
"""Tests for lol_lib.py's global (non-region) snapshot path helpers."""

from __future__ import annotations

import datetime as dt

import lol_lib


def test_global_scrape_snapshot_dir_defaults_to_today():
    path = lol_lib.global_scrape_snapshot_dir()
    assert path == lol_lib.DATA_CACHE_DIR / "global" / dt.date.today().isoformat()


def test_global_scrape_snapshot_dir_accepts_explicit_date():
    path = lol_lib.global_scrape_snapshot_dir(dt.date(2026, 1, 1))
    assert path == lol_lib.DATA_CACHE_DIR / "global" / "2026-01-01"


def test_latest_global_scrape_snapshot_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(lol_lib, "DATA_CACHE_DIR", tmp_path)
    assert lol_lib.latest_global_scrape_snapshot() is None


def test_latest_global_scrape_snapshot_returns_most_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(lol_lib, "DATA_CACHE_DIR", tmp_path)
    (tmp_path / "global" / "2026-01-01").mkdir(parents=True)
    (tmp_path / "global" / "2026-02-15").mkdir(parents=True)
    assert lol_lib.latest_global_scrape_snapshot() == tmp_path / "global" / "2026-02-15"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_lol_lib.py -v`
Expected: FAIL with `AttributeError: module 'lol_lib' has no attribute 'global_scrape_snapshot_dir'`

- [ ] **Step 3: Implement in lol_lib.py**

Add below the existing `latest_scrape_snapshot` function:

```python
def global_scrape_snapshot_dir(date: dt.date | None = None) -> Path:
    """Path to a dated snapshot folder for data that is not region-specific: data_cache/global/<YYYY-MM-DD>/."""
    date = date or dt.date.today()
    return DATA_CACHE_DIR / "global" / date.isoformat()


def latest_global_scrape_snapshot() -> Path | None:
    """Most recent dated global snapshot folder, or None if none exist yet."""
    global_dir = DATA_CACHE_DIR / "global"
    if not global_dir.exists():
        return None
    snapshots = sorted(p for p in global_dir.iterdir() if p.is_dir())
    return snapshots[-1] if snapshots else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_lol_lib.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add lol_lib.py test_lol_lib.py
git commit -m "Add global (non-region) snapshot path helpers to lol_lib"
```

---

### Task 2: Capture real HTML fixtures

**Files:**
- Create: `tests/capture_fixtures.py`
- Create: `tests/fixtures/teams_list.html`, `tests/fixtures/players_list.html`, `tests/fixtures/champion_list.html`, `tests/fixtures/team_matchlist.html`, `tests/fixtures/game_stats.html`

**Interfaces:**
- Produces: the 5 fixture files every later task's tests read from.

- [ ] **Step 1: Write the capture script**

```python
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
```

- [ ] **Step 2: Run it**

Run: `python tests/capture_fixtures.py`
Expected: 5 lines like `saved teams_list.html (NNNNN bytes)`, each a plausible page size (thousands of bytes, not near-zero).

- [ ] **Step 3: Sanity-check the fixtures**

Run: `python -c "from pathlib import Path; [print(p.name, p.stat().st_size) for p in Path('tests/fixtures').glob('*.html')]"`
Expected: 5 files listed, all with non-trivial sizes.

- [ ] **Step 4: Commit**

```bash
git add tests/capture_fixtures.py tests/fixtures/
git commit -m "Add gol.gg HTML fixture capture script and captured fixtures"
```

---

### Task 3: URL builders and HTTP fetch layer

**Files:**
- Create: `lol_scrape_lib.py`
- Test: `test_lol_scrape_lib.py` (new file)

**Interfaces:**
- Produces: `list_url(entity: str, season: str, split: str, tournament: str = "ALL") -> str`, `team_matchlist_url(team_id: str, split: str, tournament: str = "ALL") -> str`, `game_stats_url(game_id: str) -> str`, `fetch_html(url: str, session: requests.Session, delay: float = 1.5, max_retries: int = 3) -> str`
- Consumes: nothing (foundational task)

- [ ] **Step 1: Write the failing tests**

Create `test_lol_scrape_lib.py`:

```python
"""Tests for lol_scrape_lib.py. Parsing tests read saved fixtures under
tests/fixtures/ instead of hitting gol.gg live."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

import lol_scrape_lib

FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"


def test_list_url_teams():
    url = lol_scrape_lib.list_url("teams", "S16", "Summer")
    assert url == "https://gol.gg/teams/list/season-S16/split-Summer/tournament-ALL/"


def test_list_url_champion_entity_is_singular():
    url = lol_scrape_lib.list_url("champion", "S16", "Summer")
    assert url == "https://gol.gg/champion/list/season-S16/split-Summer/tournament-ALL/"


def test_list_url_encodes_tournament_name():
    url = lol_scrape_lib.list_url("teams", "S16", "Summer", tournament="LCK Summer")
    assert "LCK%20Summer" in url


def test_team_matchlist_url():
    url = lol_scrape_lib.team_matchlist_url("2833", "Summer")
    assert url == "https://gol.gg/teams/team-matchlist/2833/split-Summer/tournament-ALL/"


def test_game_stats_url():
    url = lol_scrape_lib.game_stats_url("80757")
    assert url == "https://gol.gg/game/stats/80757/page-game/"


def test_fetch_html_returns_response_text_on_success():
    session = MagicMock()
    session.get.return_value.text = "<html>ok</html>"
    session.get.return_value.raise_for_status.return_value = None
    with patch("lol_scrape_lib.time.sleep"):
        html = lol_scrape_lib.fetch_html("https://gol.gg/x", session, delay=0)
    assert html == "<html>ok</html>"


def test_fetch_html_retries_then_succeeds():
    session = MagicMock()
    ok_response = MagicMock(text="<html>ok</html>")
    ok_response.raise_for_status.return_value = None
    session.get.side_effect = [requests.ConnectionError("boom"), ok_response]
    with patch("lol_scrape_lib.time.sleep"):
        html = lol_scrape_lib.fetch_html("https://gol.gg/x", session, delay=0, max_retries=2)
    assert html == "<html>ok</html>"
    assert session.get.call_count == 2


def test_fetch_html_raises_after_max_retries():
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("boom")
    with patch("lol_scrape_lib.time.sleep"):
        with pytest.raises(RuntimeError):
            lol_scrape_lib.fetch_html("https://gol.gg/x", session, delay=0, max_retries=2)
    assert session.get.call_count == 2
```

Note: `FIXTURES` is defined here for later tasks to reuse; this task's own tests do not read fixtures.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'lol_scrape_lib'`

- [ ] **Step 3: Write the implementation**

Create `lol_scrape_lib.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add lol_scrape_lib.py test_lol_scrape_lib.py
git commit -m "Add lol_scrape_lib URL builders and HTTP fetch with retry"
```

---

### Task 4: List-table parsing, region filter, content hash

**Files:**
- Modify: `lol_scrape_lib.py`
- Modify: `test_lol_scrape_lib.py`

**Interfaces:**
- Consumes: fixtures from Task 2 (`teams_list.html`, `players_list.html`, `champion_list.html`)
- Produces: `parse_list_table(html: str) -> pd.DataFrame`, `filter_to_target_regions(df: pd.DataFrame, regions: list[str]) -> pd.DataFrame`, `hash_table(df: pd.DataFrame) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `test_lol_scrape_lib.py`:

```python
def test_parse_list_table_teams_fixture_has_expected_columns():
    html = (FIXTURES / "teams_list.html").read_text(encoding="utf-8")
    df = lol_scrape_lib.parse_list_table(html)
    for col in ["Name", "Region", "Games", "Win rate", "K:D", "GPM"]:
        assert col in df.columns
    assert len(df) > 0


def test_filter_to_target_regions_keeps_only_target_rows():
    html = (FIXTURES / "teams_list.html").read_text(encoding="utf-8")
    df = lol_scrape_lib.parse_list_table(html)
    filtered = lol_scrape_lib.filter_to_target_regions(df, ["LCK", "LPL", "LEC", "LCS"])
    assert len(filtered) > 0
    assert set(filtered["Region"].unique()) <= {"LCK", "LPL", "LEC", "LCS"}


def test_players_list_fixture_has_no_region_column():
    html = (FIXTURES / "players_list.html").read_text(encoding="utf-8")
    df = lol_scrape_lib.parse_list_table(html)
    assert "Region" not in df.columns


def test_champion_list_fixture_has_no_region_column():
    html = (FIXTURES / "champion_list.html").read_text(encoding="utf-8")
    df = lol_scrape_lib.parse_list_table(html)
    assert "Region" not in df.columns


def test_hash_table_is_stable_across_row_order():
    df1 = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    df2 = pd.DataFrame({"a": [2, 1], "b": ["y", "x"]})
    assert lol_scrape_lib.hash_table(df1) == lol_scrape_lib.hash_table(df2)


def test_hash_table_changes_when_content_changes():
    df1 = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    df2 = pd.DataFrame({"a": [1, 2], "b": ["x", "z"]})
    assert lol_scrape_lib.hash_table(df1) != lol_scrape_lib.hash_table(df2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: FAIL, `AttributeError: module 'lol_scrape_lib' has no attribute 'parse_list_table'`

- [ ] **Step 3: Write the implementation**

Add to `lol_scrape_lib.py` (with `import hashlib` and `import pandas as pd` added to the imports):

```python
def parse_list_table(html: str) -> pd.DataFrame:
    """Parse a gol.gg list page's single stats table into a DataFrame."""
    return pd.read_html(html)[0]


def filter_to_target_regions(df: pd.DataFrame, regions: list[str]) -> pd.DataFrame:
    """Keep only rows whose Region column is one of the target regions.

    Only the teams list has a Region column; players and champion lists
    are fetched globally instead (see the plan's "Decisions carried
    over" section for why).
    """
    return df[df["Region"].isin(regions)].reset_index(drop=True)


def hash_table(df: pd.DataFrame) -> str:
    """Order-independent content hash of a DataFrame, for the staleness guard."""
    normalized = df.sort_index(axis=1).sort_values(by=list(df.columns)).reset_index(drop=True)
    return hashlib.sha256(normalized.to_csv(index=False).encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: PASS (14 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add lol_scrape_lib.py test_lol_scrape_lib.py
git commit -m "Add list-table parsing, region filter, and content hash"
```

---

### Task 5: Team match-list parsing and game-ID discovery

**Files:**
- Modify: `lol_scrape_lib.py`
- Modify: `test_lol_scrape_lib.py`

**Interfaces:**
- Consumes: `team_matchlist.html` fixture from Task 2
- Produces: `parse_team_matchlist(html: str) -> list[dict]` (each dict: `game_id: str`, `url: str`), `discover_game_ids(matchlist_rows: list[dict]) -> list[str]`

Side (Blue/Red) is NOT extracted here. The user confirmed gol.gg conveys side through the color of each team's name text on the individual game page, not as literal "Blue"/"Red" text on the match-list page (confirmed absent there via live check). So side detection belongs in Task 6's `parse_game_draft`, working from the one page that actually encodes it; this task only needs game IDs and URLs, which keeps it simpler.

- [ ] **Step 1: Write the failing tests**

Append to `test_lol_scrape_lib.py`:

```python
def test_parse_team_matchlist_finds_known_game_ids():
    html = (FIXTURES / "team_matchlist.html").read_text(encoding="utf-8")
    rows = lol_scrape_lib.parse_team_matchlist(html)
    game_ids = {row["game_id"] for row in rows}
    expected = {
        "80757", "80756", "80755", "80747", "80746", "80380",
        "80379", "80039", "80038", "79884", "79883", "79872", "79871",
    }
    assert expected <= game_ids


def test_parse_team_matchlist_rows_have_url_key():
    html = (FIXTURES / "team_matchlist.html").read_text(encoding="utf-8")
    rows = lol_scrape_lib.parse_team_matchlist(html)
    assert all("url" in row for row in rows)
    assert all(row["url"].startswith("https://gol.gg/game/stats/") for row in rows)


def test_discover_game_ids_dedupes_preserving_order():
    rows = [{"game_id": "1"}, {"game_id": "2"}, {"game_id": "1"}, {"game_id": "3"}]
    assert lol_scrape_lib.discover_game_ids(rows) == ["1", "2", "3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: FAIL, `AttributeError: module 'lol_scrape_lib' has no attribute 'parse_team_matchlist'`

- [ ] **Step 3: Write the implementation**

Add to `lol_scrape_lib.py` (with `import re`, `from urllib.parse import urljoin`, and `from bs4 import BeautifulSoup` added to the imports):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: PASS (17 tests total so far)

- [ ] **Step 5: Commit**

```bash
git add lol_scrape_lib.py test_lol_scrape_lib.py
git commit -m "Add team match-list parsing and game-ID discovery/dedup"
```

---

### Task 6: Individual game page parsing

**Files:**
- Modify: `lol_scrape_lib.py`
- Modify: `test_lol_scrape_lib.py`

**Interfaces:**
- Consumes: `game_stats.html` fixture from Task 2
- Produces: `parse_game_meta(html: str) -> dict` (`duration_seconds: int`, `patch: str`, `date: str`), `parse_game_draft(html: str) -> dict` (`team_1: str`, `team_2: str`, `winner: str`, `team_1_side: str | None`, `team_2_side: str | None`, `team_1_bans: list[str]`, `team_2_bans: list[str]`, `team_1_picks: list[str]`, `team_2_picks: list[str]`), `parse_box_score(html: str) -> list[dict]` (each: `team: int`, `player: str`, `champion: str`, `kda: str`, `cs: int`), `assemble_game_row(game_id, region, season, split, meta, draft, box_score) -> dict`

This is the biggest task in the plan because gol.gg's game page is not a simple table. `parse_game_meta` is regex-over-plain-text and fully specified below. `parse_game_draft` and `parse_box_score` need you to look at the real fixture's markup before finishing them (a best-effort first attempt is given, but this site's exact HTML structure has not been inspected node-by-node by anyone on this project yet); `assemble_game_row` is pure combination logic and is fully specified.

- [ ] **Step 1: Write parse_game_meta's failing test**

Append to `test_lol_scrape_lib.py`:

```python
def test_parse_game_meta_matches_confirmed_values():
    html = (FIXTURES / "game_stats.html").read_text(encoding="utf-8")
    meta = lol_scrape_lib.parse_game_meta(html)
    assert meta["duration_seconds"] == 21 * 60 + 45
    assert meta["patch"] == "16.15"
    assert meta["date"] == "2026-08-05"
```

- [ ] **Step 2: Run to verify it fails, then implement parse_game_meta**

Run: `pytest test_lol_scrape_lib.py::test_parse_game_meta_matches_confirmed_values -v`, expect `AttributeError`.

Add to `lol_scrape_lib.py`:

```python
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
```

Run: `pytest test_lol_scrape_lib.py::test_parse_game_meta_matches_confirmed_values -v`, expect PASS.

- [ ] **Step 3: Write parse_game_draft and parse_box_score's failing tests**

Append to `test_lol_scrape_lib.py`:

```python
def test_parse_game_draft_matches_confirmed_values():
    html = (FIXTURES / "game_stats.html").read_text(encoding="utf-8")
    draft = lol_scrape_lib.parse_game_draft(html)
    assert {draft["team_1"], draft["team_2"]} == {"ANB", "Disruptors"}
    winner_name = draft["team_1"] if draft["winner"] == "team_1" else draft["team_2"]
    assert winner_name == "ANB"
    winner_bans = draft["team_1_bans"] if draft["winner"] == "team_1" else draft["team_2_bans"]
    loser_bans = draft["team_2_bans"] if draft["winner"] == "team_1" else draft["team_1_bans"]
    assert set(winner_bans) == {"Nocturne", "Jayce", "Anivia", "Jhin", "Gnar"}
    assert set(loser_bans) == {"Cassiopeia", "Camille", "Syndra", "Alistar", "Nautilus"}
    winner_picks = draft["team_1_picks"] if draft["winner"] == "team_1" else draft["team_2_picks"]
    assert winner_picks == ["Ryze", "Rumble", "Xin Zhao", "Sivir", "Lulu"]
    # Side is genuinely unconfirmed for this fixture (nobody has checked which
    # color ANB played), so only check it came back as one of the two valid
    # values, or None if Step 4's inspection finds no reliable color/class cue.
    assert draft["team_1_side"] in {"Blue", "Red", None}
    assert draft["team_2_side"] in {"Blue", "Red", None}
    if draft["team_1_side"] is not None and draft["team_2_side"] is not None:
        assert draft["team_1_side"] != draft["team_2_side"]


def test_parse_box_score_matches_confirmed_rows():
    html = (FIXTURES / "game_stats.html").read_text(encoding="utf-8")
    rows = lol_scrape_lib.parse_box_score(html)
    assert len(rows) == 10
    by_player = {row["player"]: row for row in rows}
    expected = {
        "Giyuu": ("Ryze", "5/1/13", 222),
        "Maged": ("Rumble", "4/1/10", 190),
        "Theocacs": ("Xin Zhao", "7/0/14", 185),
        "Shy Carry": ("Sivir", "12/0/12", 216),
        "B Butcher": ("Lulu", "0/2/22", 26),
        "owlonsky": ("Aatrox", "0/5/1", 155),
        "Skream": ("Viktor", "1/4/0", 172),
        "sas": ("Rell", "1/8/2", 24),
        "Chakroun": ("K'Sante", "1/5/0", 161),
        "Random": ("Ahri", "1/6/3", 171),
    }
    for player, (champion, kda, cs) in expected.items():
        assert by_player[player]["champion"] == champion
        assert by_player[player]["kda"] == kda
        assert by_player[player]["cs"] == cs
```

- [ ] **Step 4: Inspect the real fixture's picks/bans/box-score/team-name markup**

Run: `python -c "from bs4 import BeautifulSoup; html = open('tests/fixtures/game_stats.html', encoding='utf-8').read(); soup = BeautifulSoup(html, 'lxml'); node = soup.find(string=lambda s: s and 'Ryze' in s); print(node.parent.prettify()[:1500] if node else 'text not found, try another known champion name')"`

Read the printed markup. Note the tag names and class attributes wrapping champion names, team names, and box-score numbers. Repeat with other known strings (`"Maged"`, `"190"`) if the first search does not land near the structure you need.

Separately, run: `python -c "from bs4 import BeautifulSoup; html = open('tests/fixtures/game_stats.html', encoding='utf-8').read(); soup = BeautifulSoup(html, 'lxml'); nodes = soup.find_all(string='ANB'); [print(n.parent.prettify()[:600]) for n in nodes]"` (repeat with `'Disruptors'`). The user has confirmed side is shown by the color of each team's name text, so look specifically for a `class` (e.g. something like `blue-team`/`red-team`) or an inline `style` with a color value on the element wrapping each team's name. Note down: which team name is colored which way, and what attribute/value carries it. If neither a class nor a style attribute anywhere near the team name encodes color (color could be applied via an external stylesheet this fixture doesn't include), side detection has no reliable source in the saved HTML; leave it as `None` in that case rather than guessing further, per the test above.

This tells you what to select on in Step 5.

- [ ] **Step 5: Implement parse_game_draft and parse_box_score**

Add to `lol_scrape_lib.py`. Treat the body of these two functions as a first attempt to adjust against what Step 4 showed you, not a final answer to copy verbatim:

```python
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


def parse_game_draft(html: str) -> dict:
    """Extract team names, bans, picks, and side (if determinable) from a game page.

    gol.gg labels teams by outcome (winner/loser), not by map side, so
    team_1/team_2 here means "whichever team's section is first on the
    page". team_1_side/team_2_side come from _side_from_element and may
    both be None if the fixture's HTML doesn't carry a color/class cue.

    Champion names on gol.gg are commonly rendered as <img alt="Champion
    Name"> icons; this looks for alt-text first and falls back to link
    text. Adjust based on what Step 4's inspection actually showed.
    """
    soup = BeautifulSoup(html, "lxml")

    def champion_names(container) -> list[str]:
        names = [img["alt"] for img in container.find_all("img", alt=True) if img["alt"].strip()]
        if not names:
            names = [a.get_text(strip=True) for a in container.find_all("a") if a.get_text(strip=True)]
        return names

    team_headers = [
        el for el in soup.find_all(class_=re.compile("team-name|blue-line-header", re.IGNORECASE))
        if el.get_text(strip=True)
    ]
    if len(team_headers) < 2:
        raise ValueError("could not find two team names on the game page; adjust the selector above")
    team_names = [el.get_text(strip=True) for el in team_headers]
    team_sides = [_side_from_element(el) for el in team_headers]

    ban_containers = soup.find_all(class_=re.compile("ban", re.IGNORECASE))
    pick_containers = soup.find_all(class_=re.compile("pick|blue-line-players|red-line-players", re.IGNORECASE))

    team_1_bans = champion_names(ban_containers[0]) if len(ban_containers) > 0 else []
    team_2_bans = champion_names(ban_containers[1]) if len(ban_containers) > 1 else []
    team_1_picks = champion_names(pick_containers[0]) if len(pick_containers) > 0 else []
    team_2_picks = champion_names(pick_containers[1]) if len(pick_containers) > 1 else []

    winning_text = soup.find(string=re.compile("Winning Team", re.IGNORECASE))
    winner = "team_1"
    if winning_text and team_names[1] in winning_text:
        winner = "team_2"

    return {
        "team_1": team_names[0],
        "team_2": team_names[1],
        "winner": winner,
        "team_1_side": team_sides[0],
        "team_2_side": team_sides[1],
        "team_1_bans": team_1_bans,
        "team_2_bans": team_2_bans,
        "team_1_picks": team_1_picks,
        "team_2_picks": team_2_picks,
    }


def parse_box_score(html: str) -> list[dict]:
    """Extract each player's box score row (team 1 or 2, player, champion, KDA, CS).

    Assumes the page lists team 1's five players before team 2's five,
    matching parse_game_draft's team ordering. Adjust based on what
    Step 4's inspection actually showed.
    """
    soup = BeautifulSoup(html, "lxml")
    player_rows = soup.find_all(class_=re.compile("player-role|player-stats-row", re.IGNORECASE))

    rows = []
    for index, row_el in enumerate(player_rows):
        text = row_el.get_text(" ", strip=True)
        kda_match = re.search(r"(\d+)/(\d+)/(\d+)", text)
        cs_match = re.search(r"(\d+)\s*CS", text, re.IGNORECASE)
        champion_img = row_el.find("img", alt=True)
        player_link = row_el.find("a")
        rows.append({
            "team": 1 if index < 5 else 2,
            "player": player_link.get_text(strip=True) if player_link else "",
            "champion": champion_img["alt"] if champion_img else "",
            "kda": f"{kda_match.group(1)}/{kda_match.group(2)}/{kda_match.group(3)}" if kda_match else "",
            "cs": int(cs_match.group(1)) if cs_match else 0,
        })
    return rows
```

- [ ] **Step 6: Run tests, adjust selectors against the real fixture until they pass**

Run: `pytest test_lol_scrape_lib.py::test_parse_game_draft_matches_confirmed_values test_lol_scrape_lib.py::test_parse_box_score_matches_confirmed_rows -v`

If they fail, go back to Step 4's inspection technique (search for a different known string, e.g. `"K'Sante"` or `"222"`) to find the actual containing tags/classes, then update the `class_=re.compile(...)` patterns and extraction logic in Step 5 to match. Repeat until both tests pass.

- [ ] **Step 7: Write assemble_game_row's failing test**

Append to `test_lol_scrape_lib.py`:

```python
def test_assemble_game_row_builds_expected_flat_row():
    meta = {"date": "2026-08-05", "patch": "16.15", "duration_seconds": 1305}
    draft = {
        "team_1": "ANB",
        "team_2": "Disruptors",
        "winner": "team_1",
        "team_1_side": "Blue",
        "team_2_side": "Red",
        "team_1_bans": ["Nocturne", "Jayce", "Anivia"],
        "team_2_bans": ["Cassiopeia", "Camille", "Syndra"],
        "team_1_picks": ["Ryze", "Rumble", "Xin Zhao", "Sivir", "Lulu"],
        "team_2_picks": ["Aatrox", "Viktor", "Rell", "K'Sante", "Ahri"],
    }
    box_score = [
        {"team": 1, "player": "Giyuu", "champion": "Ryze", "kda": "5/1/13", "cs": 222},
        {"team": 1, "player": "Maged", "champion": "Rumble", "kda": "4/1/10", "cs": 190},
        {"team": 2, "player": "owlonsky", "champion": "Aatrox", "kda": "0/5/1", "cs": 155},
    ]
    row = lol_scrape_lib.assemble_game_row("80757", "LCK", "S16", "Summer", meta, draft, box_score)
    assert row["game_id"] == "80757"
    assert row["region"] == "LCK"
    assert row["winner"] == "team_1"
    assert row["team_1_picks"] == "Ryze|Rumble|Xin Zhao|Sivir|Lulu"
    assert row["team_1_players"] == "Giyuu|Maged"
    assert row["team_1_kda"] == "5/1/13|4/1/10"
    assert row["team_1_cs"] == "222|190"
    assert row["team_2_players"] == "owlonsky"
    assert row["team_1_side"] == "Blue"
    assert row["team_2_side"] == "Red"
```

- [ ] **Step 8: Run to verify it fails, then implement assemble_game_row**

Run: `pytest test_lol_scrape_lib.py::test_assemble_game_row_builds_expected_flat_row -v`, expect `AttributeError`.

Add to `lol_scrape_lib.py`:

```python
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
```

- [ ] **Step 9: Run all of this task's tests to verify they pass**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: PASS (all tests so far, including the 4 from this task)

- [ ] **Step 10: Commit**

```bash
git add lol_scrape_lib.py test_lol_scrape_lib.py
git commit -m "Add individual game page parsing (meta, draft, box score, row assembly)"
```

---

### Task 7: Region and global orchestration helpers

**Files:**
- Modify: `lol_scrape_lib.py`
- Modify: `test_lol_scrape_lib.py`

**Interfaces:**
- Consumes: everything from Tasks 3-6, plus `lol_lib.REGIONS`
- Produces: `scrape_region_teams(region, season, split, session) -> pd.DataFrame`, `scrape_global_list(entity, season, split, session) -> pd.DataFrame`, `scrape_region_games(region, season, split, teams_df, session, already_fetched_ids, on_row, on_failure) -> None`

This task wires Tasks 3-6 together into the resumable per-region game fetch described in the spec, using dependency injection (`on_row`, `on_failure` callbacks) so the orchestration logic is unit-testable without real file I/O; the notebook (Task 8) supplies callbacks that actually append to CSV.

- [ ] **Step 1: Write the failing tests**

Append to `test_lol_scrape_lib.py`:

```python
def test_scrape_region_teams_filters_tags_region_and_adds_team_id(monkeypatch):
    html = (FIXTURES / "teams_list.html").read_text(encoding="utf-8")
    monkeypatch.setattr(lol_scrape_lib, "fetch_html", lambda url, session, **kw: html)
    df = lol_scrape_lib.scrape_region_teams("LCK", "S16", "Summer", session=MagicMock())
    assert len(df) > 0
    assert set(df["Region"].unique()) <= {"LCK"}
    assert "team_id" in df.columns
    assert df["team_id"].notna().all()


def test_scrape_global_list_returns_unfiltered_table(monkeypatch):
    html = (FIXTURES / "champion_list.html").read_text(encoding="utf-8")
    monkeypatch.setattr(lol_scrape_lib, "fetch_html", lambda url, session, **kw: html)
    df = lol_scrape_lib.scrape_global_list("champion", "S16", "Summer", session=MagicMock())
    assert len(df) > 0
    assert "Champion" in df.columns


def test_scrape_region_games_skips_already_fetched_and_calls_on_row(monkeypatch):
    matchlist_html = (FIXTURES / "team_matchlist.html").read_text(encoding="utf-8")
    game_html = (FIXTURES / "game_stats.html").read_text(encoding="utf-8")

    def fake_fetch(url, session, **kw):
        return game_html if "/game/stats/" in url else matchlist_html

    monkeypatch.setattr(lol_scrape_lib, "fetch_html", fake_fetch)

    teams_df = pd.DataFrame({"Name": ["Anubis Gaming"], "team_id": ["2833"]})
    seen_rows = []
    failures = []

    lol_scrape_lib.scrape_region_games(
        region="LCK",
        season="S16",
        split="Summer",
        teams_df=teams_df,
        session=MagicMock(),
        already_fetched_ids={"79871"},
        on_row=seen_rows.append,
        on_failure=failures.append,
    )

    fetched_ids = {row["game_id"] for row in seen_rows}
    assert "79871" not in fetched_ids
    assert "80757" in fetched_ids
    assert failures == []
```

Note: `teams_df` needs a `team_id` column for `scrape_region_games` to know which team pages to walk. `scrape_region_teams` (this task, below) is what actually derives it, by parsing the same raw HTML twice: once via `parse_list_table` for the stats columns, once directly for the name-to-team_id mapping from each row's link href (gol.gg's table itself has no team_id column). `test_scrape_region_games_skips_already_fetched_and_calls_on_row`'s `teams_df` above supplies `team_id` directly as a literal, since `scrape_region_games` only consumes that column, it does not derive it, that's a different function's job.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: FAIL, `AttributeError: module 'lol_scrape_lib' has no attribute 'scrape_region_teams'`

- [ ] **Step 3: Write the implementation**

Add to `lol_scrape_lib.py`:

```python
def scrape_region_teams(region: str, season: str, split: str, session: requests.Session) -> pd.DataFrame:
    """Fetch the teams list for a season/split, filtered to one region, with each row's team_id.

    team_id is not part of gol.gg's table itself (pandas.read_html only
    sees the stats columns); it lives in each row's link href, so this
    parses the same raw HTML a second time, directly, for the
    name-to-team_id mapping.
    """
    html = fetch_html(list_url("teams", season, split), session)
    df = filter_to_target_regions(parse_list_table(html), [region])
    soup = BeautifulSoup(html, "lxml")
    team_id_by_name = {}
    for link in soup.find_all("a", href=True):
        match = re.search(r"/team-stats/(\d+)/", link["href"])
        if match:
            team_id_by_name[link.get_text(strip=True)] = match.group(1)
    df["team_id"] = df["Name"].map(team_id_by_name)
    return df.dropna(subset=["team_id"]).reset_index(drop=True)


def scrape_global_list(entity: str, season: str, split: str, session: requests.Session) -> pd.DataFrame:
    """Fetch a players or champion list for a season/split, unfiltered (see plan notes on why these are not region-split)."""
    html = fetch_html(list_url(entity, season, split), session)
    return parse_list_table(html)


def scrape_region_games(
    region: str,
    season: str,
    split: str,
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
        matchlist_html = fetch_html(team_matchlist_url(team_id, split), session)
        matchlist_rows.extend(parse_team_matchlist(matchlist_html))

    for game_id in discover_game_ids(matchlist_rows):
        if game_id in already_fetched_ids:
            continue
        try:
            game_html = fetch_html(game_stats_url(game_id), session)
            meta = parse_game_meta(game_html)
            draft = parse_game_draft(game_html)
            box_score = parse_box_score(game_html)
            row = assemble_game_row(game_id, region, season, split, meta, draft, box_score)
            on_row(row)
        except Exception as error:  # noqa: BLE001, one bad game must not stop the whole scrape
            on_failure({"game_id": game_id, "url": game_stats_url(game_id), "error": str(error)})
```

Note the short comment on the broad `except Exception`: this is the one place in the module where catching everything is deliberate, per the design spec's error handling section, one game's parse failure should never crash the whole run.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest test_lol_scrape_lib.py -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add lol_scrape_lib.py test_lol_scrape_lib.py
git commit -m "Add region/global list orchestration and resumable game scraping"
```

---

### Task 8: lol_scrape.ipynb notebook

**Files:**
- Create: `lol_scrape.ipynb`

**Interfaces:**
- Consumes: `lol_lib` (REGIONS, setup_output_dirs, scrape_snapshot_dir, latest_scrape_snapshot, global_scrape_snapshot_dir, latest_global_scrape_snapshot, print_versions) and `lol_scrape_lib` (everything from Tasks 3-7)

This notebook cannot be unit tested the way `lol_scrape_lib.py` was; it is validated by actually running it in TEST_MODE against the live site, per README.txt's working discipline of testing on a small sample before a full-scale scrape.

- [ ] **Step 1: Create the notebook with these cells, in order**

Markdown cell:
```
# LoL Scrape

Pulls gol.gg team, player, champion, and game data for LCK/LPL/LEC/LCS
and caches it into dated data_cache snapshots. Run this first; every
other notebook reads its cached output tables. See
docs/superpowers/specs/2026-09-06-lol-scrape-design.md for the design
this notebook implements.

Set TEST_MODE = True for a first run: caps to one region and its first
2 teams so the whole path (list tables, team matchlist, game manifest,
game parse, hash guard, snapshot write) can be checked cheaply before a
full run.
```

Code cell (imports):
```python
from __future__ import annotations

import csv
from pathlib import Path

import requests

import lol_lib
import lol_scrape_lib
```

Markdown cell: `## 2. Parameters`

Code cell:
```python
SEASON = "S16"
SPLIT = "Summer"
TEST_MODE = True
TEST_MODE_TEAM_LIMIT = 2

REGIONS = lol_lib.REGIONS if not TEST_MODE else lol_lib.REGIONS[:1]
```

Markdown cell: `## 3. Session setup`

Code cell:
```python
lol_lib.print_versions()
lol_lib.setup_output_dirs()
session = requests.Session()
```

Markdown cell: `## 4. Global lists (players, champions)`

Code cell:
```python
players_df = lol_scrape_lib.scrape_global_list("players", SEASON, SPLIT, session)
champions_df = lol_scrape_lib.scrape_global_list("champion", SEASON, SPLIT, session)

global_tables = {"players": players_df, "champions": champions_df}
latest_global = lol_lib.latest_global_scrape_snapshot()

if latest_global is not None and all(
    lol_scrape_lib.hash_table(df) == lol_scrape_lib.hash_table(
        pd.read_csv(latest_global / f"{name}.csv")
    )
    for name, df in global_tables.items()
    if (latest_global / f"{name}.csv").exists()
):
    print(f"no changes to global lists since {latest_global.name}, skipping snapshot")
else:
    snapshot_dir = lol_lib.global_scrape_snapshot_dir()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for name, df in global_tables.items():
        df.to_csv(snapshot_dir / f"{name}.csv", index=False)
    print(f"wrote global snapshot to {snapshot_dir}")
```

Note: add `import pandas as pd` to the imports cell above; needed here for `pd.read_csv`.

Markdown cell: `## 5. Per-region teams and games`

Code cell:
```python
for region in REGIONS:
    print(f"--- {region} ---")

    teams_df = lol_scrape_lib.scrape_region_teams(region, SEASON, SPLIT, session)
    if TEST_MODE:
        teams_df = teams_df.head(TEST_MODE_TEAM_LIMIT)
```

Code cell (continuing the same loop, teams.csv snapshot with hash guard):
```python
    latest_region = lol_lib.latest_scrape_snapshot(region)
    teams_unchanged = (
        latest_region is not None
        and (latest_region / "teams.csv").exists()
        and lol_scrape_lib.hash_table(teams_df) == lol_scrape_lib.hash_table(
            pd.read_csv(latest_region / "teams.csv")
        )
    )
```

Markdown cell: `## 6. Resumable game fetch`

Code cell:
```python
    snapshot_dir = lol_lib.scrape_snapshot_dir(region)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    games_path = snapshot_dir / "games.csv"
    failures_path = snapshot_dir / "failures.csv"

    already_fetched_ids: set[str] = set()
    if games_path.exists():
        already_fetched_ids = set(pd.read_csv(games_path)["game_id"].astype(str))

    def append_row(row: dict, path: Path = games_path) -> None:
        is_new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if is_new_file:
                writer.writeheader()
            writer.writerow(row)

    def append_failure(failure: dict, path: Path = failures_path) -> None:
        is_new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["game_id", "url", "error"])
            if is_new_file:
                writer.writeheader()
            writer.writerow(failure)

    lol_scrape_lib.scrape_region_games(
        region, SEASON, SPLIT, teams_df, session,
        already_fetched_ids, on_row=append_row, on_failure=append_failure,
    )

    if not teams_unchanged or games_path.exists():
        teams_df.to_csv(snapshot_dir / "teams.csv", index=False)
    print(f"finished {region}: snapshot at {snapshot_dir}")
```

- [ ] **Step 2: Run the notebook in TEST_MODE**

Run all cells with `TEST_MODE = True` (via Jupyter, or `jupyter nbconvert --to notebook --execute lol_scrape.ipynb`).

Expected: no exceptions; `print_versions()` output; one region's teams.csv, games.csv (and failures.csv if any game failed), plus `data_cache/global/<today>/players.csv` and `champions.csv`, all under real dated folders. Spot-check a couple of rows in `games.csv` by eye (`pd.read_csv(...).head()`) and confirm picks/bans/duration/patch look sane.

- [ ] **Step 3: Re-run the notebook once more, still in TEST_MODE**

Expected: the teams/global-lists hash guard logs "no changes... skipping snapshot" (same day, same data), and `scrape_region_games` fetches zero new games since `already_fetched_ids` now covers everything from the first run. This is the concrete check that resumability and the staleness guard both actually work, not just that the code runs once.

- [ ] **Step 4: Switch to a full run**

Set `TEST_MODE = False`, re-run all cells. Expected: all 4 regions processed, each with its own dated snapshot folder.

- [ ] **Step 5: Commit**

```bash
git add lol_scrape.ipynb
git commit -m "Add lol_scrape.ipynb orchestrator notebook"
```

---

### Task 9: Narrow scrape to real tournaments, fix staleness guard, tag rows, fail loud on schema drift

Added post-merge-review, 2026-09-07 (see "Decisions carried over" items 5-8 above and "Confirmed gol.gg facts for Task 9" for the research this task's exact values come from). Addresses the final whole-branch review's findings C1, C2, I1, I2, I3, I4, I5, I6, plus the review's `fetch_html` 4xx and stale-notebook-output minors.

**Files:**
- Modify: `lol_scrape_lib.py`
- Modify: `lol_scrape.ipynb`
- Modify: `test_lol_scrape_lib.py`

**Interfaces:**
- `scrape_region_teams` gains a `tournament: str` parameter (no default) and now tags `region`, `season`, `split`, `tournament` columns onto its returned frame.
- `scrape_global_list` gains `region: str = "GLOBAL"` tagging plus `season`/`split` columns (still one fetch, unfiltered, per the existing "not region-split" decision).
- `scrape_region_games` gains a `tournament: str` parameter, threaded into `team_matchlist_url`.
- `assemble_game_row` gains a `tournament: str` parameter, added to its returned dict.

- [ ] **Step 1: Fix `hash_table` (finding C2)**

Replace the body of `hash_table` (currently `lol_scrape_lib.py:76-79`):

```python
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
    normalized = stringified.sort_index(axis=1).sort_values(by=list(stringified.columns)).reset_index(drop=True)
    return hashlib.sha256(normalized.to_csv(index=False).encode("utf-8")).hexdigest()
```

Update `test_hash_table_is_stable_across_row_order` and `test_hash_table_changes_when_content_changes` if they construct DataFrames with non-string dtypes that would now stringify differently (read them first; adjust only if a literal assertion would break, the guarantees the tests check do not change).

- [ ] **Step 2: Fix champion-name normalization (finding I2)**

Replace `_CHAMPION_NAME_FIXES` and `_normalize_champion_name` (currently `lol_scrape_lib.py:128-141`):

```python
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
}


def _normalize_champion_name(name: str) -> str:
    """Restore apostrophes gol.gg strips from certain champion names."""
    return _CHAMPION_NAME_FIXES.get(name.replace("'", "").lower(), name)
```

Add a test: `assert lol_scrape_lib._normalize_champion_name("Kaisa") == "Kai'Sa"` and `assert lol_scrape_lib._normalize_champion_name("KSante") == "K'Sante"` (both casings must resolve).

- [ ] **Step 3: Add a schema-validation helper and use it (finding I6)**

Add near the top of `lol_scrape_lib.py`, after `hash_table`:

```python
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
```

- [ ] **Step 4: Rework `scrape_region_teams` and `scrape_global_list` for real-tournament fetch, tagging, and schema checks (findings C1, I4, I6)**

Replace both functions (currently `lol_scrape_lib.py:322-343`):

```python
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
    df["region"], df["season"], df["split"], df["tournament"] = region, season, split, tournament
    return df


def scrape_global_list(entity: str, region: str, season: str, split: str, session: requests.Session) -> pd.DataFrame:
    """Fetch a players or champion list for a season/split, unfiltered, tagged with region/season/split.

    region defaults to "GLOBAL" at the call site (see plan notes on why
    these are not region-split); still tagged with season/split so a
    saved CSV's coverage is not inferable only from its folder date.
    """
    html = fetch_html(list_url(entity, season, split), session)
    df = parse_list_table(html)
    expected = {"players": ["Player"], "champion": ["Champion"]}.get(entity, [])
    _require_columns(df, expected, f"{entity} list")
    df["region"], df["season"], df["split"] = region, season, split
    return df
```

Note: `scrape_region_games` already receives `teams_df` with a `team_id` column from this function; nothing downstream needs to change how it reads `team_id`.

- [ ] **Step 5: Thread `tournament` through `scrape_region_games` and `assemble_game_row` (finding C1's row-tagging half)**

In `scrape_region_games` (currently `lol_scrape_lib.py:346-380`), add a `tournament: str` parameter (after `split`) and pass it to both `team_matchlist_url` and `assemble_game_row`:

```python
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
```

In `assemble_game_row` (currently `lol_scrape_lib.py:276-286`), add `tournament: str` as a parameter (after `split`) and `"tournament": tournament,` to the returned dict (right after `"split": split,`).

- [ ] **Step 6: Fail loud on an illegal short draft (finding I5)**

In `parse_game_draft` (currently `lol_scrape_lib.py:181-234`), after building `picks` (right before the `return` statement), add:

```python
    for team_index, team_picks in enumerate(picks, start=1):
        if len(team_picks) != 5:
            raise ValueError(f"team {team_index} has {len(team_picks)} picks, expected 5 (draft parse likely incomplete)")
```

Bans are left unenforced (a team can legitimately decline a ban, per the final review's own calibration note). This routes any real parse miss into `scrape_region_games`'s existing `except Exception` -> `on_failure` -> `failures.csv` path; no new error handling needed there.

- [ ] **Step 7: `fetch_html` should not retry a 4xx (new minor from final review, bundled here since it is a one-line change to a function this task already touches conceptually)**

In `fetch_html` (currently `lol_scrape_lib.py:43-55`), re-raise immediately on a client error instead of burning retries on something that will never succeed:

```python
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
```

- [ ] **Step 8: Update every existing call site and test for the new signatures**

`scrape_region_teams`, `scrape_global_list`, `scrape_region_games`, `assemble_game_row` all gained parameters in Steps 4-5. Update every test in `test_lol_scrape_lib.py` that calls them (`test_scrape_region_teams_filters_tags_region_and_adds_team_id`, `test_scrape_global_list_returns_unfiltered_table`, `test_scrape_region_games_skips_already_fetched_and_calls_on_row`, `test_assemble_game_row_builds_expected_flat_row`) to pass a `tournament` argument (any fixed test value, e.g. `"LCK 2026 Rounds 3-4"`), and assert the new `region`/`season`/`split`/`tournament` columns/keys are present with the expected values. Run the full suite (`pytest test_lol_lib.py test_lol_scrape_lib.py -v`) and fix anything broken by the signature changes before moving on; do not defer a broken test to review.

- [ ] **Step 9: Rework the notebook's Parameters and per-region cells (findings I1, C1's scrape-time narrowing)**

Replace the Parameters cell (currently the code cell right after `## 2. Parameters`):

```python
SEASON = "S16"
SPLIT = "Summer"
TEST_MODE = True
TEST_MODE_TEAM_LIMIT = 2

# Real gol.gg tournament name per region (not the league abbreviation) -
# fetching by tournament name is what actually narrows to that league's
# real roster; gol.gg's Region column is a server region and also
# contains that server's academy/challenger/collegiate teams. Update
# this by hand for a different competitive period; leagues do not share
# one split-naming convention (LPL uses numbered splits, LCK currently
# uses round-based stages, not Spring/Summer), so there is no single
# SPLIT value that works for all four.
TOURNAMENTS = {
    "LCK": "LCK 2026 Rounds 3-4",
    "LEC": "LEC 2026 Summer Season",
    "LCS": "LCS 2026 Summer",
    "LPL": "LPL 2026 Split 3",
}

REGIONS = lol_lib.REGIONS if not TEST_MODE else lol_lib.REGIONS[:1]
```

Replace the global-lists cell (`## 4. Global lists`) call site to pass `region="GLOBAL"`:

```python
players_df = lol_scrape_lib.scrape_global_list("players", "GLOBAL", SEASON, SPLIT, session)
champions_df = lol_scrape_lib.scrape_global_list("champion", "GLOBAL", SEASON, SPLIT, session)
```

Replace the merged per-region loop cell (Sections 5-6, currently one cell) with (comment at top explaining the single-cell constraint is unchanged, keep it):

```python
# Sections 5 and 6 (teams fetch, hash guard, resumable game fetch) all live
# in this one cell: a `for region in REGIONS:` loop's body can't span
# separate notebook cells (each cell is its own independently-executed
# unit), so splitting it would only run the later phases once total, using
# whatever the loop left in scope, instead of once per region.
for region in REGIONS:
    print(f"--- {region} ---")
    tournament = TOURNAMENTS[region]

    teams_df = lol_scrape_lib.scrape_region_teams(region, SEASON, SPLIT, tournament, session)
    if TEST_MODE:
        teams_df = teams_df.head(TEST_MODE_TEAM_LIMIT)

    # Snapshots are self-consistent full sets (spec DATA FLOW step 4): every
    # dated folder always gets a full teams.csv and games.csv, even when
    # nothing changed, rather than a folder missing one of the two tables.
    # games.csv carries forward across days: today's file starts as a copy
    # of the latest snapshot's rows (so already-known games are never
    # re-fetched), written immediately so a crash during today's run still
    # loses at most the one new game in flight, matching spec step 3.
    latest_region = lol_lib.latest_scrape_snapshot(region)
    snapshot_dir = lol_lib.scrape_snapshot_dir(region)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    games_path = snapshot_dir / "games.csv"
    failures_path = snapshot_dir / "failures.csv"

    known_rows: list[dict] = []
    if latest_region is not None and (latest_region / "games.csv").exists():
        known_rows = pd.read_csv(latest_region / "games.csv", keep_default_na=False, dtype=str).to_dict("records")
    already_fetched_ids = {row["game_id"] for row in known_rows}
    if known_rows:
        with open(games_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(known_rows[0].keys()))
            writer.writeheader()
            writer.writerows(known_rows)

    def append_row(row: dict, path: Path = games_path) -> None:
        is_new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if is_new_file:
                writer.writeheader()
            writer.writerow(row)

    def append_failure(failure: dict, path: Path = failures_path) -> None:
        is_new_file = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["game_id", "url", "error"])
            if is_new_file:
                writer.writeheader()
            writer.writerow(failure)

    lol_scrape_lib.scrape_region_games(
        region, SEASON, SPLIT, tournament, teams_df, session,
        already_fetched_ids, on_row=append_row, on_failure=append_failure,
    )

    teams_df.to_csv(snapshot_dir / "teams.csv", index=False)

    # keep_default_na=False/dtype=str on both reloads below: pd.read_html
    # and pd.read_csv infer dtypes differently for identical source text
    # (and pandas treats the literal string "NA", the LCS region code, as
    # missing data by default), so this is the only way two genuinely
    # identical tables hash the same via hash_table.
    teams_changed = latest_region is None or not (latest_region / "teams.csv").exists() or (
        lol_scrape_lib.hash_table(teams_df)
        != lol_scrape_lib.hash_table(pd.read_csv(latest_region / "teams.csv", keep_default_na=False, dtype=str))
    )
    games_added = len(known_rows) < len(pd.read_csv(games_path, keep_default_na=False, dtype=str))
    if teams_changed or games_added:
        print(f"finished {region}: snapshot at {snapshot_dir} (changed)")
    else:
        print(f"finished {region}: snapshot at {snapshot_dir} (no changes since {latest_region.name})")
```

Note the change in semantics from the original per-file skip: both `teams.csv` and `games.csv` are now always written into today's folder (matching spec's "every dated snapshot is a self-consistent full set"); the hash/row-count comparison only controls what gets printed. `snapshot_dir.relative_to(lol_lib.PROJECT_ROOT)` is deliberately not used in the print (an absolute `Path` under a portfolio repo owner's home directory would be a stale, environment-specific detail once outputs are committed) - print `snapshot_dir` only as already shown above, since `lol_lib.scrape_snapshot_dir` already returns a path relative in spirit to the project (this note is for the implementer's awareness if the final review's absolute-path-in-committed-output minor comes up again; no further code change needed here beyond what is written above).

Also fix the global-lists cell's existing hash-guard comparison the same way (add `dtype=str` alongside the existing `keep_default_na=False` on its `pd.read_csv` call), and add `keep_default_na=False, dtype=str` to the `pd.read_csv(games_path)["game_id"]` read used to seed `already_fetched_ids` if any such read remains elsewhere in the notebook after this rewrite (it should not, since Step 9's cell replaces that logic entirely with the `known_rows` mechanism above, but check).

Merge the two adjacent markdown headers `## 5. Per-region teams and games` and `## 6. Resumable game fetch` into a single `## 5. Per-region teams and games` (drop the now-contentless `## 6` header), since the loop cell covers both.

- [ ] **Step 10: Clear stale committed notebook outputs, then re-run and re-commit**

Before running: clear all cell outputs (e.g. `jupyter nbconvert --clear-output --inplace lol_scrape.ipynb`, or equivalent), since the currently-committed outputs are from a 2026-09-06 TEST_MODE run and will be misleading once the code they show has changed this much.

Run TEST_MODE first (small, fast, live-network check per README.txt's working discipline), confirm no exceptions and that a fresh run followed by a second immediate re-run correctly logs "no changes" for an unchanged region. Then set `TEST_MODE = False` and run the full 4-region pull, expecting every region's real-tournament roster counts from "Confirmed gol.gg facts for Task 9" above (10 LCK, 10 LEC, 8 LCS, 12 LPL). Commit:

```bash
git add lol_scrape_lib.py lol_scrape.ipynb test_lol_scrape_lib.py
git commit -m "Narrow scrape to real tournaments, fix staleness guard, tag rows, fail loud on schema drift"
```

---

## Plan self-review notes

- Spec coverage: all 6 data-flow steps in the spec map to tasks above (list tables to Tasks 4/7, game discovery to Task 5, resumable game fetch to Tasks 6/7, snapshot/hash guard to Task 8, politeness to Task 3, first test run to Task 8). Two spec items resolved differently than originally written, both called out explicitly in "Decisions carried over" at the top with reasoning: players/champions region-scoping, and how side (Blue/Red) is determined (moved from a best-effort match-list text search to color/class detection in `parse_game_draft`, after the user confirmed live that gol.gg conveys side through team-name text color rather than literal text).
- No placeholder language remains except the two functions in Task 6 that are explicitly and honestly flagged as first-attempt implementations pending real-markup inspection (`parse_game_draft`, `parse_box_score`), which is a fact about gol.gg's HTML nobody on this project has inspected node-by-node yet, not a shortcut being taken. Every other function in this plan has a complete, real implementation. One stray placeholder introduced while drafting Task 8 (a fake `team_id` join key with a "do not write this" note) was caught and removed during this same self-review pass, replaced by moving real `team_id` extraction into `scrape_region_teams` (Task 7) so the notebook cell calls it directly instead of duplicating the logic.
- Type/signature consistency checked: `assemble_game_row` no longer takes separate `team_1_side`/`team_2_side` parameters, it reads them from `draft` (matching `parse_game_draft`'s return and `scrape_region_games`'s call in Task 7). `parse_team_matchlist` no longer returns a `side` key (moved to `parse_game_draft`), checked against its two call sites (Task 7's `scrape_region_games`, and its own tests). `scrape_region_teams`'s output now carries `team_id`, checked against `scrape_region_games`'s consumption of `team["team_id"]` and the notebook's usage in Task 8.
