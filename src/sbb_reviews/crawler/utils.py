import hashlib
import math
import random
import re
import time
from datetime import datetime, timedelta


def sleep_random(min_s: float = 2.0, max_s: float = 5.0):
    time.sleep(random.uniform(min_s, max_s))


def make_review_hash(opuic: str, author_key: str, date_relative: str) -> str:
    key = f"{opuic}:{author_key}:{date_relative}"
    return hashlib.sha256(key.encode()).hexdigest()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_date(date_relative: str, scraped_at: str) -> str:
    """Convert a relative Google Maps date string to an estimated ISO date."""
    base = datetime.fromisoformat(scraped_at)
    s = date_relative.lower().strip()

    if not s:
        return ""

    n = _extract_number(s)

    if "minute" in s or "hour" in s:
        return base.date().isoformat()
    elif "day" in s:
        return (base - timedelta(days=n)).date().isoformat()
    elif "week" in s:
        return (base - timedelta(weeks=n)).date().isoformat()
    elif "month" in s:
        return (base - timedelta(days=n * 30)).date().isoformat()
    elif "year" in s:
        return (base - timedelta(days=n * 365)).date().isoformat()

    return ""


def _extract_number(s: str) -> int:
    m = re.search(r"\d+", s)
    if m:
        return int(m.group())
    if s.startswith("a ") or s.startswith("an "):
        return 1
    return 1


def detect_language(text: str) -> str:
    if not text or len(text.strip()) < 10:
        return ""
    try:
        from langdetect import detect, LangDetectException
        return detect(text)
    except Exception:
        return ""
