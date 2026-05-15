# Data Model

All data is stored in a single SQLite database at `output/reviews.db`.
After each crawl run, the database is exported to `output/stations.csv` and `output/reviews.csv`.

---

## Tables

### `stations`

One row per SBB train station. Populated from the input CSV and enriched with Google Maps data during crawling.

| Column | Type | Description |
|---|---|---|
| `opuic` | TEXT PK | Unique station identifier from the SBB open data CSV (e.g. `8503000`) |
| `name` | TEXT | Station name as listed in the SBB CSV (e.g. `Zürich HB`) |
| `stop_name` | TEXT | Stop name used for Google Maps search queries |
| `abbreviation` | TEXT | SBB station abbreviation (e.g. `ZUE`) |
| `sloid` | TEXT | SBB SLOID identifier (e.g. `ch:1:sloid:3000`) |
| `latitude` | REAL | WGS84 latitude from the SBB CSV |
| `longitude` | REAL | WGS84 longitude from the SBB CSV |
| `google_place_id` | TEXT | Raw place ID extracted from the Google Maps URL |
| `google_maps_url` | TEXT | Full URL of the resolved Google Maps place page |
| `overall_rating` | REAL | Aggregate star rating shown on Google Maps (e.g. `4.2`) |
| `review_count_google` | INTEGER | Total number of reviews Google reports for this station |
| `scrape_status` | TEXT | Crawl lifecycle state — see values below |
| `scraped_at` | TEXT | ISO 8601 UTC timestamp of the last status update |
| `error_message` | TEXT | Last error message when `scrape_status = 'failed'`, otherwise NULL |

**`scrape_status` values**

| Value | Meaning |
|---|---|
| `pending` | Not yet attempted |
| `place_found` | Google Maps place resolved, rating saved, review collection in progress |
| `done` | Fully complete — rating and all reviews saved |
| `failed` | Last attempt errored; `error_message` contains the reason. Re-run with `--retry-failed` |

---

### `reviews`

One row per individual Google Maps review. Linked to `stations` via `opuic`.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-incrementing internal ID |
| `opuic` | TEXT FK | Links to `stations.opuic` |
| `review_hash` | TEXT UNIQUE | SHA-256 of `opuic + author_url + date_relative` — deduplication key, safe to re-crawl |
| `author_name` | TEXT | Display name of the reviewer |
| `author_profile_url` | TEXT | Google Maps contributor profile URL |
| `rating` | INTEGER | Star rating given by the reviewer (1–5) |
| `text` | TEXT | Full review body text, NULL if the reviewer left a star rating only |
| `date_relative` | TEXT | Raw relative date string scraped from Google Maps in the UI language (e.g. `vor 3 Monaten`, `2 months ago`) |
| `date_estimated` | TEXT | Estimated ISO date (YYYY-MM-DD) calculated from `date_relative` and `scraped_at` |
| `language` | TEXT | ISO 639-1 language code detected from `text` (e.g. `de`, `fr`, `en`), empty if text is NULL or too short |
| `owner_reply` | TEXT | Text of an operator reply to the review, NULL if none |
| `scraped_at` | TEXT | ISO 8601 UTC timestamp of when this row was written |

---

## Relationships

```
stations (opuic) ──< reviews (opuic)
```

One station has zero or more reviews. Stations with no Google Maps reviews still have a row in `stations` with `overall_rating` and `review_count_google = 0`.

---

## Notes

- **Timestamps** are stored as ISO 8601 strings in UTC (e.g. `2026-05-07T10:23:45.123456+00:00`).
- **`date_estimated`** is approximate. Google Maps only exposes relative dates (`"3 months ago"`), so the absolute date is back-calculated from the crawl timestamp. Precision is roughly ±15 days for month-level entries.
- **`review_hash`** makes re-crawling idempotent — duplicate rows are silently ignored on insert.
- **Indexes:** `idx_reviews_opuic` on `reviews(opuic)` for fast per-station lookups.
