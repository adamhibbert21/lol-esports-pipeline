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
