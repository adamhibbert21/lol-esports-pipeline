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
    assert set(filtered["Region"].unique()) <= set(lol_scrape_lib.GOLGG_REGION_CODES.values())


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


def test_filter_to_target_regions_includes_lcs_na_teams():
    """Verify LCS/NA teams survive the filter (critical: NA must use keep_default_na=False)."""
    html = (FIXTURES / "teams_list.html").read_text(encoding="utf-8")
    df = lol_scrape_lib.parse_list_table(html)
    filtered = lol_scrape_lib.filter_to_target_regions(df, ["LCK", "LPL", "LEC", "LCS"])
    team_names = filtered["Name"].str.lower()
    assert (team_names.str.contains("cloud9", na=False).any() or
            team_names.str.contains("team liquid", na=False).any())


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


def test_parse_game_meta_matches_confirmed_values():
    html = (FIXTURES / "game_stats.html").read_text(encoding="utf-8")
    meta = lol_scrape_lib.parse_game_meta(html)
    assert meta["duration_seconds"] == 21 * 60 + 45
    assert meta["patch"] == "16.15"
    assert meta["date"] == "2026-08-05"


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

