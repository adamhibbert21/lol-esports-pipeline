LOL_SCRAPE.IPYNB DESIGN
=======================

Status: approved, not yet implemented.
Date: 2026-09-06


PURPOSE
--------

lol_scrape.ipynb is the first notebook in the pipeline described in
README.txt. It pulls gol.gg data for LCK, LPL, LEC, and LCS and caches it
to data_cache/<region>/<YYYY-MM-DD>/, so every later notebook (lol_qc,
lol_eda, lol_features, lol_win_model) reads from its cached output tables
instead of re-scraping. It does not resolve entity or data-quality
problems (team rebrands, missing/partial games); that is lol_qc.ipynb's
job. This notebook's job is only to fetch what gol.gg has and record it
faithfully, including what failed to fetch.


CONFIRMED SITE STRUCTURE (verified live, 2026-09-06)
------------------------------------------------------

Current season on gol.gg is S16. URL pattern for list pages:

  https://gol.gg/<entity>/list/season-<S..>/split-<Split>/tournament-<name|ALL>/

  - entity in {teams, players, champion}
  - Split in {ALL, Pre-Season, Winter, Spring, Summer}
  - tournament is ALL or a specific tournament name (LEC, LCS, LCK, LPL,
    and 40+ others including minor regions)

Teams list table columns (confirmed): Name, Season, Region, Games, Win
rate, K:D, GPM, GDM, Game duration, FP%, Blue%, Kills/game, Deaths/game,
Towers killed, Towers lost, FB%, FT%, DRAPG, DRA%, VGPG, HER%, DRA@15,
TD@15, GD@15, PPG, NASHPG, NASH%, CSM, DPM, WPM, VWPM, WCPM. Has a Region
column (values seen: AL, BR, PT, and presumably LCK/LPL/LEC/LCS). Team
row links to ./team-stats/<team_id>/split-<Split>/tournament-<name>/,
with a numeric team_id that appears stable across a team's matches.

Players list table columns (confirmed): Player, Country, Games, Win
rate, KDA, Avg kills, Avg deaths, Avg assists, CSM, GPM, KP%, DMG%,
Gold%, VS%, DPM, VSPM, Avg WPM, Avg WCPM, Avg VWPM, GD@15, CSD@15,
XPD@15, FB%, FB Victim, Penta Kills, Solo Kills. No Region column seen;
filtering to the 4 target regions is NOT confirmed to work by post-fetch
column filter (see OPEN TECHNICAL QUESTIONS).

Champion list table columns (confirmed): Champion, Picks, Bans,
PrioScore, Wins, Losses, Winrate, KDA, Avg BT, Avg RP, BP%, GT, CSM, DPM,
GPM, CSD@15, GD@15, XPD@15. Aggregated per whatever tournament filter is
passed; no region column, same open question as players.

Team match list: ./teams/team-matchlist/<team_id>/split-<Split>/
tournament-<name>/. Table has per-match summary columns (result, score,
side, kills/golds/towers/dragons for both teams, duration, patch, week,
tournament) and links to individual games at
./game/stats/<game_id>/page-game/.

Individual game page (confirmed via example game 80757): shows game
duration as text (e.g. "Game Time 21:45"), patch as text (e.g.
"v16.15"), a date (e.g. "2026-08-05 (WEEK4)"), bans and picks per side
(with pick order), and a per-player box score table (champion, player
name, runes, KDA, CS). This is NOT a simple table, needs BeautifulSoup
element parsing rather than pandas.read_html.


FILE LAYOUT
------------

  lol_scrape_lib.py       (new), all real scraping/parsing logic:
                            URL builders, HTTP fetch with delay/retry,
                            list-table parsers, game-page parser, content
                            hashing, game-id discovery/dedup from a
                            matchlist page. Pure functions where
                            possible, so they can be unit tested against
                            saved HTML fixtures without hitting the live
                            site.

  test_lol_scrape_lib.py  (new), unit tests for lol_scrape_lib.py
                            against small saved HTML fixtures (a list
                            page and a game page, captured once during
                            development). No live network calls in
                            tests.

  tests/fixtures/         (new), the saved HTML fixtures used above.

  lol_scrape.ipynb        (new), thin orchestrator notebook. Parameters
                            cell (SEASON, SPLIT, TEST_MODE + team cap),
                            then cells that call lol_scrape_lib and the
                            existing lol_lib to do the actual run and
                            write snapshots.

  lol_lib.py               (existing, unchanged), still owns REGIONS,
                            paths, setup_output_dirs, save_table,
                            savefig, scrape_snapshot_dir,
                            latest_scrape_snapshot, print_versions. No
                            other notebook fetches web pages, so HTTP/
                            parsing logic does not belong here.


DATA FLOW
----------

1. List tables. For each of teams/players/champions: fetch the SEASON/
   SPLIT list page(s), parse into a DataFrame, keep only rows for LCK/
   LPL/LEC/LCS (mechanism TBD per region vs per-tournament fetch, see
   OPEN TECHNICAL QUESTIONS). Tag every row with region, season, split.

2. Game discovery. From the filtered teams, fetch each team's match-list
   page, extract game IDs plus basic per-match metadata (opponent, side,
   date), and dedupe by game ID (every game appears under both teams'
   match lists) into an in-memory manifest.

3. Game fetch (resumable). Walk the manifest. For each game ID not
   already present in that region's in-progress games.csv, fetch the
   game page, parse picks/bans by side, per-player box score, duration,
   patch (a plain column on the row, not a folder split), and date, then
   append that row to games.csv immediately. On a restart, already-
   fetched IDs are read back from games.csv and skipped, so a crash
   mid-run loses at most the one game in flight. Anything that fails to
   fetch or parse is appended to failures.csv (game_id, url, error) and
   the run continues; lol_qc.ipynb is responsible for deciding what to
   do about missing/partial games, not this notebook.

4. Snapshot + staleness guard. After all 4 tables (teams, players,
   champions, games) are built for a region, hash each one's normalized
   content and compare against lol_lib.latest_scrape_snapshot(region).
   If every table matches the latest existing snapshot, skip creating a
   new dated folder and log "no changes since <date>". If anything
   differs, write all 4 tables together into a fresh
   data_cache/<region>/<YYYY-MM-DD>/ folder, even the tables that did
   not change, so every dated snapshot is a self-consistent full set.
   Storage cost of this duplication is acceptable since data_cache/ is
   gitignored and fully regenerable.

5. Politeness. Fixed delay between HTTP requests (~1-2s), a descriptive
   User-Agent identifying this as a personal research project (not
   spoofing a browser), and retry-with-backoff (a few attempts) on
   transient network errors. gol.gg has no public API and is not built
   for scraping load.

6. First test run. SEASON=S16, SPLIT=Summer, TEST_MODE capped to one
   region (LCK) and its first 1-2 teams, to validate the entire path
   (list tables -> team matchlist -> game manifest -> game parse -> hash
   guard -> snapshot write) cheaply before removing the cap for a full
   4-region pull.


ERROR HANDLING
---------------

  - Per-game fetch/parse failures: logged to failures.csv, run continues.
  - Transient HTTP failures (timeouts, 5xx): retried with backoff, a few
    attempts, then treated as a per-game failure if still unsuccessful.
  - Unexpected list-table schema (a column gol.gg renamed or removed):
    should fail loudly and stop the notebook, since a silently-malformed
    list table would corrupt every later notebook. This is different
    from a single game's failure, which is expected to happen sometimes
    and is fine to log and skip.


TESTING
--------

  - test_lol_scrape_lib.py unit-tests parsing functions (list-table
    parser, game-page parser, content hash, game-id discovery/dedup)
    against saved HTML fixtures. No network access needed to run these.
  - The notebook's own orchestration (fetch, delay, retry, resumability,
    snapshot writing) is validated by actually running the small
    TEST_MODE pull against the live site, following the working
    discipline in README.txt: test on a small, fast sample before
    committing to a full-scale scrape.


OPEN TECHNICAL QUESTIONS (verify during build, not blocking design)
---------------------------------------------------------------------

  - Whether player and champion list stats can be filtered to LCK/LPL/
    LEC/LCS after one ALL-tournament fetch (like teams can, via a Region
    column), or whether they need one fetch per specific tournament name
    per target league instead, since those two tables looked aggregated
    per whatever tournament filter is passed rather than carrying a
    per-row region label. Resolve this empirically during the small
    TEST_MODE run.
