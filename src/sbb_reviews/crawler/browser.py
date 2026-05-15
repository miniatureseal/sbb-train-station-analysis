"""Browser context setup and consent handling for the crawler."""

from playwright.sync_api import Browser, BrowserContext, Page

from .utils import sleep_random

_LOCALE_MAP = {
    "en": "en-US",
    "de": "de-DE",
    "fr": "fr-FR",
    "it": "it-IT",
}

STATION_WORD = {
    "en": "train station",
    "de": "Bahnhof",
    "fr": "gare",
    "it": "stazione",
}


def make_context(browser: Browser, language: str = "en") -> BrowserContext:
    return browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale=_LOCALE_MAP.get(language, "en-US"),
        timezone_id="Europe/Zurich",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )


def dismiss_consent(page: Page):
    """Accept or reject the Google cookie consent banner if it appears."""
    try:
        for label in ("Accept all", "Reject all", "Alle ablehnen", "Tout refuser"):
            btn = page.locator(f'button:has-text("{label}")').first
            if btn.is_visible(timeout=2_000):
                btn.click()
                sleep_random(1, 2)
                return
    except Exception:
        pass
