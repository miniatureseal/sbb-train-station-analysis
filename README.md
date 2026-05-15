# SBB Train Station Review Analysis

**[Read the full analysis online →](https://miniatureseal.github.io/sbb-train-station-analysis/)**

What do travellers actually think about Swiss train stations? This project scrapes Google Maps reviews for 61 SBB stations, runs them through an AI classifier to extract aspect-level sentiment (Safety, Cleanliness, Connections, etc.), and turns the results into a ranked list of action items.

The narrative write-up lives in `notebooks/station_reviews.ipynb` (also rendered to the link above).

## Repo layout

```
data/                       all data, never imported as code
├── input/                  source CSV from SBB's trafimage dataset
├── raw/                    crawler output (reviews.db, reviews.csv, stations.csv)
└── derived/                analysis output (action_items.csv, etc.)

src/sbb_reviews/            one installable Python package
├── crawler/                Playwright-driven Google Search review scraper
├── analysis/               AI classification + complaint/strength summarisation
├── paths.py                centralised filesystem paths
└── cli.py                  `sbb-reviews <subcommand>` entry point

notebooks/                  Jupyter narrative (not part of the pipeline)
```

## Install

```bash
pip install -e .
playwright install chromium
```

> **A note on the crawler**: The Playwright crawler in `src/sbb_reviews/crawler/` was written for one-off research scraping of publicly visible Google Maps review pages. Google Maps' Terms of Service prohibit automated access. This project treats the scrape as a constrained research snapshot of public data; it is not redistributed as a service and runs at a conservative rate with stall-retry handling. For any production, commercial, or continuously-running use, please switch to the official [Google Places API](https://developers.google.com/maps/documentation/places/web-service/overview).

## Pipeline

Run from the repo root. Each step writes a CSV that the next step reads, so you can stop and resume at any stage.

```bash
# 1. Scrape Google Maps reviews into data/raw/reviews.db (+ CSV exports)
sbb-reviews crawl --headless

# 2. AI-classify each review by aspect and sentiment (needs OPENAI_API_KEY)
sbb-reviews classify

# 3. Build action_items.csv for the worst-rated stations
sbb-reviews summarize-complaints

# 4. Build station_highlights.csv + strength_gaps.csv from the best-rated stations
sbb-reviews summarize-strengths

# 5. Open the narrative notebook
jupyter notebook notebooks/station_reviews.ipynb
```

Useful crawl flags:
- `--limit N`: stop after N stations
- `--max-reviews N`: per-station cap
- `--opuic 8500010 8503000 ...`: process specific stations only
- `--retry-failed`: reset stations marked failed and retry them
- `--language {en,de,fr,it}`: Google Maps UI language

## Outputs

| File | Produced by | Contents |
|---|---|---|
| `data/raw/reviews.db`             | `crawl`                | SQLite store (source of truth) |
| `data/raw/reviews.csv`            | `crawl`                | Flat export of `reviews` table |
| `data/raw/stations.csv`           | `crawl`                | Flat export of `stations` table |
| `data/derived/complaint_aspects_ai.csv` | `classify`        | One row per (review, aspect, sentiment) |
| `data/derived/action_items.csv`   | `summarize-complaints` | Top complaint groups, scored and described |
| `data/derived/station_highlights.csv` | `summarize-strengths` | What top stations get praised for |
| `data/derived/strength_gaps.csv`  | `summarize-strengths`  | "Visible win" vs "hygiene fix" gap analysis |

See `DATA_MODEL.md` for the SQLite schema.

## Notes

- `data/raw/*` and `data/derived/*` are gitignored. The pipeline reproduces them from `data/input/`.
- The crawler is non-destructive: stations marked `done` are skipped on subsequent runs.
- The analysis is descriptive, not causal — see the Takeaways section in the notebook for limitations.
