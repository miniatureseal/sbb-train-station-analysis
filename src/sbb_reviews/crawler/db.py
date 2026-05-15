import sqlite3
from pathlib import Path


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = get_connection(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stations (
            opuic                TEXT PRIMARY KEY,
            name                 TEXT,
            stop_name            TEXT,
            abbreviation         TEXT,
            sloid                TEXT,
            latitude             REAL,
            longitude            REAL,
            google_place_id      TEXT,
            google_maps_url      TEXT,
            overall_rating       REAL,
            review_count_google  INTEGER,
            scrape_status        TEXT DEFAULT 'pending',
            scraped_at           TEXT,
            error_message        TEXT
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            opuic                TEXT NOT NULL,
            review_hash          TEXT UNIQUE,
            author_name          TEXT,
            author_profile_url   TEXT,
            rating               INTEGER,
            text                 TEXT,
            date_relative        TEXT,
            date_estimated       TEXT,
            language             TEXT,
            owner_reply          TEXT,
            scraped_at           TEXT,
            FOREIGN KEY (opuic) REFERENCES stations(opuic)
        );

        CREATE INDEX IF NOT EXISTS idx_reviews_opuic ON reviews(opuic);
        """
    )
    conn.commit()
    return conn


def upsert_station_found(conn, station, place_id, maps_url, rating, review_count, now):
    conn.execute(
        """
        INSERT INTO stations
            (opuic, name, stop_name, abbreviation, sloid, latitude, longitude,
             google_place_id, google_maps_url, overall_rating, review_count_google,
             scrape_status, scraped_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'place_found',?)
        ON CONFLICT(opuic) DO UPDATE SET
            google_place_id     = excluded.google_place_id,
            google_maps_url     = excluded.google_maps_url,
            overall_rating      = excluded.overall_rating,
            review_count_google = excluded.review_count_google,
            scrape_status       = 'place_found',
            scraped_at          = excluded.scraped_at,
            error_message       = NULL
        """,
        (
            station.opuic, station.name, station.stop_name, station.abbreviation,
            station.sloid, station.latitude, station.longitude,
            place_id, maps_url, rating, review_count, now,
        ),
    )
    conn.commit()


def insert_review(conn, review: dict):
    conn.execute(
        """
        INSERT OR IGNORE INTO reviews
            (opuic, review_hash, author_name, author_profile_url, rating, text,
             date_relative, date_estimated, language, owner_reply, scraped_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            review["opuic"], review["review_hash"], review["author_name"],
            review["author_profile_url"], review["rating"], review["text"],
            review["date_relative"], review["date_estimated"], review["language"],
            review["owner_reply"], review["scraped_at"],
        ),
    )


def count_reviews(conn, opuic: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM reviews WHERE opuic=?", (opuic,)
    ).fetchone()[0]


def mark_done(conn, opuic: str, now: str):
    conn.execute(
        "UPDATE stations SET scrape_status='done', scraped_at=?, error_message=NULL WHERE opuic=?",
        (now, opuic),
    )
    conn.commit()


def mark_failed(conn, opuic: str, error: str, now: str):
    conn.execute(
        "UPDATE stations SET scrape_status='failed', error_message=?, scraped_at=? WHERE opuic=?",
        (error, now, opuic),
    )
    conn.commit()
