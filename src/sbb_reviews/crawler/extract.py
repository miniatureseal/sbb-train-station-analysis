"""DOM extraction for the Google Search local results review panel.

Contains:
  - The two browser-side JS snippets (scroll the panel, read review cards).
  - Helpers to read the aggregate rating, review count, and place id.
  - The infinite-scroll loop that drains the reviews tab and persists each
    card to the database.
"""

import re
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

from playwright.sync_api import Page

from .db import count_reviews, insert_review
from .utils import detect_language, estimate_date, make_review_hash, sleep_random

# Scroll-loop tuning
STALL_CYCLES = 5  # no-progress scrolls per burst before considering stalled
STALL_RETRIES = 3  # stall bursts before giving up entirely
STALL_SLEEP = 5.0  # seconds to sleep between retry bursts
SCROLL_PAUSE_MIN = 0.7
SCROLL_PAUSE_MAX = 1.3


# Search-panel-specific scroll JS.
# Walk up from the review cards to find their scrollable ancestor, then
# scroll it. Falls back to window.scrollBy if no scrollable ancestor is found.
SCROLL_JS = """
() => {
    const card = document.querySelector('div.bwb7ce[data-id]');
    if (card) {
        let el = card.parentElement;
        while (el && el !== document.body) {
            const style = window.getComputedStyle(el);
            const ov = style.overflowY;
            if ((ov === 'auto' || ov === 'scroll') && el.scrollHeight > el.clientHeight + 50) {
                el.scrollBy(0, 3000);
                return 'panel';
            }
            el = el.parentElement;
        }
    }
    window.scrollBy(0, 3000);
    return 'window';
}
"""


# Search-panel-specific JS: expands truncated reviews and reads card data.
# Card container: div.bwb7ce[data-id]
# Author: div.Vpc5Fe
# Author URL: a[href*="contrib"]
# Stars: div.dHX2k[role="img"][aria-label="Bewertung: X von 5"]
# Date: span.y3Ibjb
# Text: div.OA1nbd
#
# Accepts startIndex so only newly loaded cards are returned each iteration,
# avoiding transferring the full (ever-growing) card list on every scroll.
EXTRACT_JS = """
(startIndex) => {
    const allCards = [...document.querySelectorAll('div.bwb7ce[data-id]')];
    allCards.slice(startIndex).forEach(card => {
        card.querySelectorAll(
            'button[aria-label="See more"], button[aria-label="Mehr"], ' +
            'button[aria-label="Plus"], button[aria-label="Altro"]'
        ).forEach(b => { try { b.click(); } catch(e) {} });
    });

    return allCards.slice(startIndex).map(card => {
        const nameEl  = card.querySelector('div.Vpc5Fe');
        const linkEl  = card.querySelector('a[href*="contrib"]');
        const starsEl = card.querySelector('div.dHX2k[role="img"]');
        const dateEl  = card.querySelector('span.y3Ibjb');
        const textEl  = card.querySelector('div.OA1nbd');

        const ariaLabel = starsEl ? starsEl.getAttribute('aria-label') : '';
        const rm = ariaLabel ? ariaLabel.match(/\\d/) : null;

        return {
            authorName:  nameEl  ? nameEl.textContent.trim()  : null,
            authorUrl:   linkEl  ? linkEl.href                : null,
            rating:      rm      ? parseInt(rm[0])            : null,
            dateRelative:dateEl  ? dateEl.textContent.trim()  : null,
            text:        textEl  ? textEl.textContent.trim()  : null,
            ownerReply:  null,
        };
    });
}
"""


def extract_place_id(url: str) -> Optional[str]:
    """Extract a place identifier from either a Search or Maps URL.

    Search fragment:  #rlfi=...;si:12345678901234567890,...
    Maps data string: !1s(ChIJ...|0x...)
    """
    m = re.search(r"si:(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"!1s(ChIJ[^!&]+|0x[^!&]+)", url)
    return m.group(1) if m else None


def extract_aggregate(page: Page) -> Tuple[Optional[float], Optional[int]]:
    """Read overall rating and total review count from the Search knowledge panel."""
    overall_rating = None
    review_count = None

    try:
        rating_el = page.locator("span.fzTgPe").first
        text = rating_el.text_content(timeout=5_000)
        if text:
            m = re.search(r"\d+[,\.]\d+|\d+", text.strip())
            if m:
                overall_rating = float(m.group().replace(",", "."))
    except Exception:
        pass

    try:
        count_el = page.locator("span.z5jxId").first
        text = count_el.text_content(timeout=5_000)
        if text:
            m = re.search(r"\d[\d\s .,]*", text.strip())
            if m:
                review_count = int(re.sub(r"[^\d]", "", m.group()))
    except Exception:
        pass

    return overall_rating, review_count


def collect_reviews(
    page: Page, opuic: str, expected: Optional[int], conn, max_reviews: int = 0
) -> int:
    """Scroll the reviews panel and persist each review card found.

    Returns the total number of reviews stored for this station.
    """
    stall = 0
    retry = 0
    last_count = -1
    dom_index = 0  # how many cards have already been extracted
    now = datetime.now(timezone.utc).isoformat()

    while True:
        raw_cards: list[dict] = page.evaluate(EXTRACT_JS, dom_index)
        dom_index += len(raw_cards)

        for raw in raw_cards:
            if not raw.get("authorName"):
                continue
            author_key = raw.get("authorUrl") or raw["authorName"]
            date_relative = raw.get("dateRelative") or ""
            text = raw.get("text") or None

            review = {
                "opuic": opuic,
                "review_hash": make_review_hash(opuic, author_key, date_relative),
                "author_name": raw["authorName"],
                "author_profile_url": raw.get("authorUrl"),
                "rating": raw.get("rating"),
                "text": text,
                "date_relative": date_relative or None,
                "date_estimated": (
                    estimate_date(date_relative, now) if date_relative else ""
                ),
                "language": detect_language(text) if text else "",
                "owner_reply": raw.get("ownerReply") or None,
                "scraped_at": now,
            }
            insert_review(conn, review)

        conn.commit()
        current = count_reviews(conn, opuic)
        cap = max_reviews or expected
        print(f"  Scrolling… {current}/{cap or '?'} reviews stored")

        if max_reviews and current >= max_reviews:
            break

        if expected and current >= expected:
            break

        if current == last_count:
            stall += 1
            if stall >= STALL_CYCLES:
                retry += 1
                if retry >= STALL_RETRIES:
                    print(f"  No new reviews after {STALL_RETRIES} retries — stopping")
                    break
                print(
                    f"  Stall #{retry}/{STALL_RETRIES} — sleeping {STALL_SLEEP:.0f}s then retrying"
                )
                time.sleep(STALL_SLEEP)
                stall = 0
        else:
            stall = 0
            retry = 0

        last_count = current

        page.evaluate(SCROLL_JS)
        sleep_random(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX)

    return count_reviews(conn, opuic)
