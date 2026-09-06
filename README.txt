LOL ESPORTS WIN-PROBABILITY PIPELINE
=====================================

This project builds a data pipeline for professional League of Legends
match data. The pipeline ends in a model. The model takes two draft
compositions, five champions per side, plus the two teams playing them.
It then predicts each team's win probability as a function of how long
the game runs.

This is a new, separate project. It is not part of the CPTAC project.
It does reuse that project's structure and conventions on purpose. It
also reuses some of the lessons that project learned the hard way. This
document explains what carries over. It then lays out the plan for this
project's own pipeline. It assumes you know nothing about either
project.


BACKGROUND: THE CPTAC PROJECT
------------------------------

CPTAC stands for Clinical Proteomic Tumor Analysis Consortium. It is an
earlier project in cancer multi-omics. It is unrelated to this one. It
ran pancreatic cancer patient data through one pipeline. That data was
proteomics and transcriptomics measurements per patient. The pipeline
had four steps. First it loaded and cleaned the raw data. Then it
selected informative features. Then it clustered patients into
molecular subtypes. It clustered using each data source alone, and then
using the two sources fused together. Last, it tested whether those
subtypes predict patient survival. The deliverable was a set of
notebooks, R scripts, and a shared Python helper module. All of it can
be re-run from cached intermediate tables.

That project is the template here. There are three reasons for this.

1. Its notebook and documentation conventions already work well. The
   user likes them. See "CONVENTIONS TO CARRY OVER FROM CPTAC" below.

2. Its pipeline shape fits this project fairly well. That shape is:
   clean, then explore, then build one feature view per source, then
   fuse those views, then model an outcome. Here the two sources are
   "team form" and "draft composition". They take the place of
   proteomics and transcriptomics.

3. Its late-stage survival modeling predicted the probability of an
   event as a function of elapsed time. It did not predict a single
   static yes/no outcome. That is the closest existing precedent for
   this project's prediction target. This project predicts win
   probability as a function of elapsed game time.

CPTAC also found several data problems too late. In each case the
problem had already distorted results. One normalization step pooled
two data sources before scaling them. That step collapsed real signal.
One clustering pairing looked fine, until someone ran a stability check
against it. What carries over here is not a specific fix. It is a
habit. Treat every promising result as unverified. A result counts only
after it survives a resampling check or a held-out check. Also confirm
that a rerun really used fresh data. Do this before you trust any
before/after comparison.


DATA SOURCES
-------------

gol.gg (https://gol.gg) is the primary source. It has no public API. It
has no bulk export either. All data comes from HTML tables at
parameterized URLs. The parameters are season, split, tournament, and
region. We scrape those tables directly. The fields below are confirmed
available per team, player, and champion. You can filter them by season
and split.

  - Player stats: games, win rate, KDA and its components, CSM, GPM,
    DPM, kill/damage/gold/vision share, vision stats, gold/CS/XP
    difference at 15 minutes, first blood involvement, solo kills.

  - Team stats: games, win rate, K:D, GPM, gold difference, average
    game duration, side win rates, first pick/blood/turret rates,
    dragon/herald/baron control, towers killed and lost, vision stats,
    the same 15-minute difference metrics.

  - Champion stats: picks, bans, win rate, KDA, ban timing, pick
    presence, the same per-minute and 15-minute metrics.

  - Individual game pages: picks and bans by side, per-player stat
    lines for that one game, plus tournament-level leaderboards.

Game duration is recorded per game. This project needs that field more
than any other. Comparable stat sites do not put it front and center.
It is the field that makes champion and composition power curves
possible. A power curve here means early game strength against late
game strength. We estimate those curves from win rate conditioned on
how long the game ran. So we do not need live per-minute telemetry.

Leaguepedia (lol.fandom.com) is a secondary source. Use it only when
gol.gg is missing something structural. Two cases matter most: team
roster history, and rebrand or org name changes. Both of those affect
entity resolution. See "OPEN QUESTIONS AND KNOWN RISKS" below.
Leaguepedia has a queryable Cargo database. That is easier to script
against than scraping its wiki pages.


SCOPE
------

Regions: LCK, LPL, LEC, and LCS only. No minor regions. No
international event data, except for the games those four regions'
teams play in such events. This keeps roster work and entity resolution
manageable. It also keeps the meta fairly consistent inside any modeled
period.

Prediction target: the model takes two teams and a 5-champion draft per
side. It outputs a win probability curve over elapsed game time. It
does not output a single static number. Two feature views feed it:

  - Composition view: per-champion and per-comp historical win rate,
    conditioned on game-duration buckets. This stands in for an early
    game against late game power curve.

  - Team view: team-level rolling form or rating over recent games.
    This is independent of any specific draft.

The two views are then fused into one prediction. This is the same
shape as CPTAC's SNF step. That step fused two omics sources into one
clustering. The difference is what the fusion feeds. Here it feeds a
supervised model. There it fed unsupervised clustering.

Output modes: the user picks how the prediction is shown. There are two
modes, and both are required.

  - A classification label. This says which team is predicted to win
    the matchup.

  - A full win-probability curve over elapsed game time. This is the
    original target.

These are almost certainly two display modes of one underlying
time-conditional win-probability model. They are not two separately
trained models. To get the label, threshold the curve at a chosen time.
That time can be game start, or whatever time the user is asking about.
No second model is needed. This is a decided requirement, not an open
question.

Out of scope for now: live per-minute in-game telemetry as a model
input. That means gold graphs and objective timers. Game duration plus
end-of-game box scores should be enough to estimate power curves
without it. Player-on-champion granularity is also out of scope for
now. The first working model uses team-on-champion instead.
Player-on-champion is a possible later refinement.


PIPELINE ORDER (PLANNED)
--------------------------

    lol_scrape.ipynb    (pull and cache gol.gg tables: players, teams,
    |                     champions, games; content-hash staleness
    |                     guard, so re-scraping a live season neither
    |                     goes stale silently nor re-pulls unchanged
    |                     data silently)
    |
    +--> lol_qc.ipynb    (entity resolution for team rebrands and
    |                      player transfers; patch-version
    |                      normalization; missing and partial game
    |                      checks)
    |
    +--> lol_eda.ipynb   (champion win rate by game-duration bucket,
    |                      meta shifts by patch, side and objective
    |                      biases, team form trends over a season)
    |
    +--> lol_features.ipynb   (builds the composition-view and
    |            |              team-view feature tables described
    |            |              above)
    |            |
    |            +--> lol_win_model.ipynb   (trains and validates the
    |                                         time-conditional win
    |                                         probability model; the
    |                                         train/test split is by
    |                                         season or patch, not
    |                                         random, to avoid meta
    |                                         leakage; check
    |                                         calibration before
    |                                         trusting any result)
    |
    +--> interface   (a small app: pick two teams and two 5-champion
                       drafts, then get a prediction back; the user
                       chooses the output mode, either a
                       classification label saying which team wins or
                       the full win% curve over elapsed game time;
                       both modes read the same model, and the label
                       comes from thresholding the curve at a chosen
                       time; build this once the model above is
                       validated, not before)

Run lol_scrape.ipynb first. Every later notebook reads its cached
output tables. CPTAC has the same rule. There,
cptac_analysis.ipynb runs first, and every other notebook reads its
cached tables instead of re-deriving them.


CONVENTIONS TO CARRY OVER FROM CPTAC
---------------------------------------

Notebook style
  - "from __future__ import annotations" plus grouped imports. Group
    them as standard library, then third-party, then local. This
    matches cptac_analysis.ipynb.
  - Type hints and docstrings throughout, not just on public functions.
  - Config-driven. Put a small parameters cell near the top. REGION and
    SEASON constants play the role CANCER played in CPTAC. Do not
    hardcode those values through the notebook.
  - Numbered markdown section headers, such as "## 2. Parameters" and
    "## 3. Load Data". One logical step per cell, so you can re-run a
    section on its own.
  - A shared savefig/save_table helper pattern for figure and table
    export. It matches CPTAC's outputs/tables and outputs/figures
    layout. CPTAC used per-source subfolders. Here they become
    per-region or per-league subfolders.

Documentation style
  - Plain prose READMEs. Section them with underlined headers. Include
    an ASCII pipeline diagram showing what feeds what. Include a
    MANIFEST-style file listing what is included and why.
  - No em-dashes anywhere, in prose or in code comments.
  - Hedge findings the way CPTAC hedged them. State a number only after
    it survives a resample or a held-out split. Do not state it on a
    first promising run.

Working discipline
  - Confirm a rerun really reflects fresh data before any before/after
    claim. Check timestamps and spot-check values.
  - Test pipeline logic on a small, fast sample first. Only then commit
    to a full-scale scrape or a full-scale model run.
  - Do not trust an apparent signal until you check it against a
    resampling or a held-out time split. This applies to a champion
    power curve, a team rating, and a model's validation score. CPTAC
    treated its clustering results the same way. Nobody trusted them
    until they were stability-checked.


OPEN QUESTIONS AND KNOWN RISKS
---------------------------------

All four items below are open. None is resolved.

  - Entity resolution. Teams change organization names. They rebrand
    mid-season or between seasons. This is common in the four covered
    leagues. Players also transfer teams between splits. A team's
    history will fragment silently if we do not resolve this. We must
    resolve it before building any team-level rolling-form feature.
    CPTAC had no equivalent problem, so there is no prior work to copy.
    This needs its own pass in lol_qc.ipynb. It will likely
    cross-reference Leaguepedia's roster history.

  - Sample size per duration bucket. Duration-conditioned win rates
    need enough games in each bucket to mean anything. That applies per
    champion and per comp. Four regions across enough seasons should be
    workable. Check this early. Do not assume it.

  - Patch and meta drift. A champion's power curve depends on the
    patch. Pooling across patches without accounting for that is risky.
    It could mask signal the same way CPTAC did when it pooled two
    mismatched sources before normalizing. We have not decided how to
    handle it. The options are to model patch as an explicit feature,
    or to restrict training data to a rolling patch window.

  - Calibration. The core deliverable is a probability curve, not just
    a classification label. So calibration matters as much as
    discrimination. Calibration asks whether a predicted 70% wins about
    70% of the time. How to check and enforce it is not settled.


VERSION CONTROL
-----------------

CPTAC is git-free. This project is different. It is meant to live on
GitHub. That is not set up yet. Nothing here is a git repository. No
remote exists. No .gitignore is in place. Setup is still needed before
the first commit. It needs: git init, a .gitignore that excludes large
scraped and cached data, creation of the remote repository, and a
decision about what belongs in the repository against what stays
local-only cache. The gol.gg pulls and any intermediate tables could
get large, the same way CPTAC's gdc_cache did. None of this is done
yet.


NEXT STEPS
-----------

  1. Set up git and GitHub for this project. That means a local
     repository, a remote, and a .gitignore that excludes large cached
     data.
  2. Scaffold the project folder to match CPTAC's layout. That means
     outputs/tables, outputs/figures, and a shared config pattern.
  3. Build lol_scrape.ipynb against LCK, LPL, LEC, and LCS only. Do a
     small-sample test run before any full-season pull.
  4. Everything after that follows the pipeline order above. Take one
     notebook and one implementation plan at a time.
