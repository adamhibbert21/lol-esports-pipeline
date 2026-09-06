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
