"""Project paths.

Paths are resolved relative to the current working directory by default,
which assumes commands are run from the repo root. Override by setting
the `SBB_PROJECT_ROOT` environment variable.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("SBB_PROJECT_ROOT", ".")).resolve()

DATA_DIR = PROJECT_ROOT / "data"
INPUT_DIR = DATA_DIR / "input"
RAW_DIR = DATA_DIR / "raw"
DERIVED_DIR = DATA_DIR / "derived"

# Input files
STATIONS_INPUT_CSV = INPUT_DIR / "haltestelle-karte-trafimage.csv"

# Crawler outputs
REVIEWS_DB = RAW_DIR / "reviews.db"
REVIEWS_CSV = RAW_DIR / "reviews.csv"
STATIONS_CSV = RAW_DIR / "stations.csv"

# Analysis outputs
COMPLAINT_ASPECTS_AI_CSV = DERIVED_DIR / "complaint_aspects_ai.csv"
COMPLAINT_ASPECTS_CSV = DERIVED_DIR / "complaint_aspects.csv"
ACTION_ITEMS_CSV = DERIVED_DIR / "action_items.csv"
STATION_HIGHLIGHTS_CSV = DERIVED_DIR / "station_highlights.csv"
STRENGTH_GAPS_CSV = DERIVED_DIR / "strength_gaps.csv"
