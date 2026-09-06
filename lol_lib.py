"""Shared paths, scope constants, and output helpers for the LoL pipeline notebooks.

Every notebook in the pipeline (lol_scrape, lol_qc, lol_eda, lol_features,
lol_win_model) imports this module instead of redefining paths or output
helpers locally. Each notebook still keeps its own "Parameters" cell for
values that change per run, such as SEASON: this module only holds what is
fixed across the whole project (the four covered regions) and the plumbing
every notebook needs regardless of what it is processing.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata as metadata
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
from matplotlib.figure import Figure

# ── Scope ─────────────────────────────────────────────────────────────────
# Fixed for the whole project. See README.txt "SCOPE". A notebook's own
# Parameters cell selects which of these to run against, it does not
# redefine this list.
REGIONS: list[str] = ["LCK", "LPL", "LEC", "LCS"]

# ── Paths ─────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_CACHE_DIR = PROJECT_ROOT / "data_cache"
TABLE_DIR = PROJECT_ROOT / "outputs" / "tables"
FIGURE_DIR = PROJECT_ROOT / "outputs" / "figures"


def _check_region(region: str) -> None:
    if region not in REGIONS:
        raise ValueError(f"Unknown region {region!r}, expected one of {REGIONS}")


def setup_output_dirs(regions: Iterable[str] = REGIONS) -> None:
    """Create the data cache and per-region output folders if missing."""
    DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for region in regions:
        _check_region(region)
        (TABLE_DIR / region).mkdir(parents=True, exist_ok=True)
        (FIGURE_DIR / region).mkdir(parents=True, exist_ok=True)


def scrape_snapshot_dir(region: str, date: dt.date | None = None) -> Path:
    """Path to a dated raw-scrape snapshot folder: data_cache/<region>/<YYYY-MM-DD>/.

    lol_scrape.ipynb writes each run's pull into its own dated folder rather
    than overwriting a single cache in place. That way every later notebook,
    and every prediction, can be traced back to the exact day it was scraped,
    and lol_scrape.ipynb can diff a fresh pull's content hash against
    latest_scrape_snapshot() before deciding whether a new folder is even
    needed.
    """
    _check_region(region)
    date = date or dt.date.today()
    return DATA_CACHE_DIR / region / date.isoformat()


def latest_scrape_snapshot(region: str) -> Path | None:
    """Most recent dated scrape snapshot folder for a region, or None if none exist yet."""
    _check_region(region)
    region_dir = DATA_CACHE_DIR / region
    if not region_dir.exists():
        return None
    snapshots = sorted(p for p in region_dir.iterdir() if p.is_dir())
    return snapshots[-1] if snapshots else None


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


def save_table(df: pd.DataFrame, name: str, region: str) -> Path:
    """Write a DataFrame to outputs/tables/<region>/<name>.csv and return the path."""
    _check_region(region)
    path = TABLE_DIR / region / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def savefig(fig: Figure, name: str, region: str) -> Path:
    """Write a Matplotlib figure to outputs/figures/<region>/<name>.png and return the path."""
    _check_region(region)
    path = FIGURE_DIR / region / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", dpi=150)
    return path


def print_versions() -> None:
    """Print package versions to help diagnose workstation compatibility issues."""
    package_names = ["pandas", "numpy", "matplotlib", "requests"]
    print("Package versions:")
    print(f"python: {sys.version.split()[0]}")
    for name in package_names:
        try:
            print(f"{name}: {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            print(f"{name}: not installed")
