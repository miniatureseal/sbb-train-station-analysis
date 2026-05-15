"""Top-level crawler orchestration.

Iterates over stations, drives Playwright through Google Search local results
to open the knowledge panel, and hands off to extract.collect_reviews to drain
the reviews tab.
"""

from datetime import datetime, timezone
from urllib.parse import urlencode

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

from .browser import STATION_WORD, dismiss_consent, make_context
from .db import get_connection, mark_done, mark_failed, upsert_station_found
from .extract import collect_reviews, extract_aggregate, extract_place_id
from .utils import sleep_random


def crawl_all_stations(
    stations: list,
    db_path,
    headless: bool = False,
    limit: int = 0,
    max_reviews: int = 0,
    language: str = "en",
) -> None:
    """Iterate stations and crawl reviews for any not yet marked done."""
    with sync_playwright() as p:
        browser: Browser = p.chromium.launch(headless=headless)
        context: BrowserContext = make_context(browser, language)
        conn = get_connection(db_path)

        done_count = 0
        for station in stations:
            if limit and done_count >= limit:
                print(f"[LIMIT] Reached limit of {limit} stations, stopping.")
                break

            row = conn.execute(
                "SELECT scrape_status FROM stations WHERE opuic=?", (station.opuic,)
            ).fetchone()

            if row and row["scrape_status"] == "done":
                print(f"[SKIP]  {station.name} — already done")
                continue

            print(f"\n[START] {station.name} ({station.opuic})")

            page = context.new_page()
            try:
                n = _scrape_station(page, station, conn, max_reviews, language)
                print(f"[DONE]  {station.name} — {n} reviews collected")
                done_count += 1
            except Exception as exc:
                print(f"[ERROR] {station.name} — {exc}")
                mark_failed(
                    conn,
                    station.opuic,
                    str(exc),
                    datetime.now(timezone.utc).isoformat(),
                )
            finally:
                try:
                    page.close()
                except Exception:
                    pass

            sleep_random(4, 8)

        browser.close()
        conn.close()


def _scrape_station(page: Page, station, conn, max_reviews: int = 0, language: str = "en") -> int:
    now = datetime.now(timezone.utc).isoformat()

    place_url = _navigate_to_place(page, station, language)
    place_id = extract_place_id(page.url)
    print(f"  URL: {place_url[:80]}")

    overall_rating, review_count = extract_aggregate(page)
    upsert_station_found(conn, station, place_id, place_url, overall_rating, review_count, now)
    print(f"  Place found — rating={overall_rating}, reviews={review_count}")

    if not review_count:
        mark_failed(
            conn, station.opuic,
            "No reviews found (review_count=0 or missing)",
            datetime.now(timezone.utc).isoformat(),
        )
        return 0

    _open_reviews_tab(page)

    n = collect_reviews(page, station.opuic, review_count, conn, max_reviews)

    if n == 0:
        mark_failed(
            conn, station.opuic,
            f"Reviews tab opened but 0 reviews collected (expected ~{review_count})",
            datetime.now(timezone.utc).isoformat(),
        )
        return 0

    mark_done(conn, station.opuic, datetime.now(timezone.utc).isoformat())
    return n


def _navigate_to_place(page: Page, station, language: str = "en") -> str:
    """Navigate via Google Search local results (tbm=lcl) and open the
    embedded knowledge panel for the station.

    Returns the final URL after the panel is open.
    """
    station_word = STATION_WORD.get(language, "Bahnhof")
    query = f"{station.stop_name} {station_word}"
    search_url = "https://www.google.com/search?" + urlencode({
        "q": query,
        "tbm": "lcl",
        "hl": language,
    })

    page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
    sleep_random(1.5, 3)
    dismiss_consent(page)

    # Case 1: Google redirected directly to a Maps place page
    try:
        page.wait_for_url("**/maps/place/**", timeout=4_000)
        return page.url
    except PWTimeout:
        pass

    # Case 2: Local results list.
    # The clickable station entry is <a data-cid="..."> (role="button", jsaction).
    # Clicking it updates the URL hash (#rlfi=...si:...) and opens a detail panel
    # on the right — there is NO full page navigation.
    try:
        page.wait_for_selector('a[data-cid]', timeout=10_000)
    except PWTimeout:
        raise RuntimeError(f"No local results found for '{query}'")

    result = page.locator('a[data-cid]').first
    try:
        result.wait_for(state="visible", timeout=5_000)
        result.click()
        sleep_random(1.5, 3)
    except Exception as exc:
        raise RuntimeError(f"Could not click first search result: {exc}") from exc

    # Wait for the detail panel to load.
    # The Search panel uses <a role="tab"> (not <button role="tab">)
    # and review cards are <div class="bwb7ce" data-id="...">.
    try:
        page.wait_for_selector('a[role="tab"], div.bwb7ce[data-id]', timeout=12_000)
        return page.url
    except PWTimeout:
        pass

    raise RuntimeError(f"Detail panel did not open. Current URL: {page.url[:100]}")


def _open_reviews_tab(page: Page):
    """Click the Reviews tab on the Search knowledge panel.

    The Search panel uses <a role="tab" data-index="N"> — data-index="1" is
    always the Reviews tab regardless of UI language.
    """
    try:
        tab = page.locator('a[role="tab"][data-index="1"]').first
        tab.wait_for(state="visible", timeout=20_000)
        tab.click()
        sleep_random(1.5, 3)
    except Exception as exc:
        raise RuntimeError(f"Could not open Reviews tab: {exc}") from exc
