"""Unified command-line entry point for the sbb-reviews pipeline.

Subcommands:
  crawl                – scrape Google Maps reviews for SBB stations
  classify             – run GPT-4o-mini aspect classification on scraped reviews
  summarize-complaints – build action_items.csv from the worst-rated stations using GPT-5.4
  summarize-strengths  – build station_highlights.csv + strength_gaps.csv from the best-rated stations using GPT-5.4
"""

import argparse
import sys

from .paths import REVIEWS_DB, STATIONS_INPUT_CSV


def _cmd_crawl(args: argparse.Namespace) -> int:
    from .crawler import crawl_all_stations
    from .crawler.db import init_db
    from .crawler.parser import parse_stations

    REVIEWS_DB.parent.mkdir(parents=True, exist_ok=True)

    stations = parse_stations(STATIONS_INPUT_CSV)
    print(f"Loaded {len(stations)} stations from CSV")

    if args.opuic:
        opuic_set = set(args.opuic)
        stations = [s for s in stations if s.opuic in opuic_set]
        missing = opuic_set - {s.opuic for s in stations}
        if missing:
            print(f"Warning: OPUICs not found in CSV: {', '.join(sorted(missing))}")
        print(f"Filtered to {len(stations)} station(s) by OPUIC")

    conn = init_db(REVIEWS_DB)

    for s in stations:
        conn.execute(
            """
            INSERT OR IGNORE INTO stations
                (opuic, name, stop_name, abbreviation, sloid, latitude, longitude, scrape_status)
            VALUES (?,?,?,?,?,?,?,'pending')
            """,
            (
                s.opuic,
                s.name,
                s.stop_name,
                s.abbreviation,
                s.sloid,
                s.latitude,
                s.longitude,
            ),
        )
    conn.commit()

    if args.retry_failed:
        n = conn.execute(
            "UPDATE stations SET scrape_status='pending', error_message=NULL WHERE scrape_status='failed'"
        ).rowcount
        conn.commit()
        print(f"Reset {n} failed station(s) to pending")

    conn.close()

    crawl_all_stations(
        stations,
        REVIEWS_DB,
        headless=args.headless,
        limit=args.limit,
        max_reviews=args.max_reviews,
        language=args.language,
    )

    _export_csv()
    return 0


def _export_csv() -> None:
    try:
        import pandas as pd
        from .crawler.db import get_connection
        from .paths import REVIEWS_CSV, STATIONS_CSV

        conn = get_connection(REVIEWS_DB)
        pd.read_sql("SELECT * FROM stations", conn).to_csv(STATIONS_CSV, index=False)
        pd.read_sql("SELECT * FROM reviews", conn).to_csv(REVIEWS_CSV, index=False)
        conn.close()
        print("\nExported:")
        print(f"  {STATIONS_CSV}")
        print(f"  {REVIEWS_CSV}")
    except Exception as exc:
        print(f"CSV export failed: {exc}")


def _cmd_classify(args: argparse.Namespace) -> int:
    from .analysis.classify_aspects import main

    main()
    return 0


def _cmd_summarize_complaints(args: argparse.Namespace) -> int:
    from .analysis.summarize_complaints import main

    main()
    return 0


def _cmd_summarize_strengths(args: argparse.Namespace) -> int:
    from .analysis.summarize_strengths import main

    main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sbb-reviews",
        description="SBB train station review pipeline: crawl, classify, summarize.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_crawl = sub.add_parser("crawl", help="Scrape Google Maps reviews")
    p_crawl.add_argument(
        "--headless", action="store_true", help="Run browser headlessly"
    )
    p_crawl.add_argument(
        "--limit", type=int, default=0, help="Stop after N stations (0 = all)"
    )
    p_crawl.add_argument(
        "--max-reviews",
        type=int,
        default=0,
        help="Collect at most N reviews per station (0 = all)",
    )
    p_crawl.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset failed stations to pending before running",
    )
    p_crawl.add_argument(
        "--language",
        default="en",
        choices=["en", "de", "fr", "it"],
        help="Google Maps UI language (default: en)",
    )
    p_crawl.add_argument(
        "--opuic", nargs="+", metavar="OPUIC", help="Only process these station OPUICs"
    )
    p_crawl.set_defaults(func=_cmd_crawl)

    p_classify = sub.add_parser(
        "classify", help="Run AI aspect classification on scraped reviews"
    )
    p_classify.set_defaults(func=_cmd_classify)

    p_complaints = sub.add_parser(
        "summarize-complaints", help="Build action_items.csv from worst-rated stations"
    )
    p_complaints.set_defaults(func=_cmd_summarize_complaints)

    p_strengths = sub.add_parser(
        "summarize-strengths",
        help="Build station_highlights + strength_gaps from best-rated stations",
    )
    p_strengths.set_defaults(func=_cmd_summarize_strengths)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
