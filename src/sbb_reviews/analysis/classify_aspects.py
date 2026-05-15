"""
AI-powered aspect classification for SBB station reviews.

Sends written reviews to GPT-4o-mini in batches of 10. For each review, the
model identifies which aspects are mentioned, whether the mention is a
complaint or praise, the specific reason, and the key phrases from the
review text.

Reads: data/raw/reviews.csv, data/raw/stations.csv
Writes: data/derived/complaint_aspects_ai.csv

Set OPENAI_API_KEY in your environment before running.
"""

import json
import time

import openai
import pandas as pd
from tqdm import tqdm

from ..paths import COMPLAINT_ASPECTS_AI_CSV, REVIEWS_CSV, STATIONS_CSV

BATCH_SIZE = 10
MODEL = "gpt-4o-mini"
MAX_RETRIES = 3

ASPECTS_LIST = [
    "Cleanliness",
    "Toilets",
    "Lifts/Escalators",
    "Food & Shops",
    "Safety",
    "Signage/Nav",
    "Parking/Bikes",
    "Connections",
    "Crowds",
    "Accessibility",
    "Seating/Waiting",
    "Staff/Service",
]

GROUP_LABELS = {
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "en": "English",
    "rm": "Romansh",
}

SYSTEM_PROMPT = f"""You analyse Google Maps reviews of Swiss train stations.
For each review, identify which aspects of the station are meaningfully mentioned,
and whether each mention is a complaint, praise, or neutral observation.

Available aspects (use exact names):
{chr(10).join(f"  - {a}" for a in ASPECTS_LIST)}

For each aspect found, return:
  aspect    – exact name from the list above
  sentiment – "negative", "positive", or "neutral"
  reason    – concise English description of the specific issue or praise.
              Be concrete: not just "safety issue" but "drug addicts loitering
              near the entrance"; not just "cleanliness" but "urine smell in
              underpass", "overflowing rubbish bins at platform 3".
              If the mention is positive, describe what was praised specifically.
  keywords  – list of 2–5 exact phrases copied from the review text that
              support this classification (preserving original language).

Rules:
- Only include aspects that are clearly and meaningfully mentioned.
- Do NOT include an aspect just because a single neutral word appears.
- A review praising cleanliness should appear with sentiment "positive",
  not be omitted — we track both praise and complaints.
- Respond with a JSON array, one element per review, in the same order as input.
  Each element: {{"review_index": <int>, "aspects": [...]}}
- If a review has no relevant aspects, use {{"review_index": <int>, "aspects": []}}
- Output ONLY the JSON array, no markdown fences, no commentary.
"""


def build_user_message(batch: pd.DataFrame) -> str:
    lines = []
    for i, (_, row) in enumerate(batch.iterrows(), start=1):
        lang = row.get("language") or "unknown"
        date = str(row.get("date_estimated") or "unknown date")[:10]  # YYYY-MM-DD
        lines.append(
            f"Review {i} (rating: {int(row['rating'])}/5, language: {lang}, date: {date}):"
        )
        lines.append(str(row["text"]).strip())
        lines.append("")
    return "\n".join(lines)


FORMAT_REMINDER = (
    "Your previous response had the wrong format. "
    "Each element in the array must be: "
    '{"review_index": <int>, "aspects": [{"aspect": <str>, "sentiment": <str>, "reason": <str>, "keywords": [<str>, ...]}, ...]}. '
    "aspects must be a list of objects, NOT a list of strings. "
    "Output ONLY the JSON array, no markdown, no commentary."
)


def validate(parsed: list) -> None:
    """Raise ValueError if any aspect entry is not a dict."""
    for item in parsed:
        for asp in item.get("aspects", []):
            if not isinstance(asp, dict):
                raise ValueError(f"aspect entry is not a dict: {asp!r}")


def classify_batch(client: openai.OpenAI, batch: pd.DataFrame) -> list[dict]:
    user_msg = build_user_message(batch)
    messages: list = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]
    for attempt in range(MAX_RETRIES):
        raw = ""
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=messages,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.splitlines()[1:])
            if raw.endswith("```"):
                raw = "\n".join(raw.splitlines()[:-1])
            parsed = json.loads(raw.strip())
            validate(parsed)
            return parsed
        except (json.JSONDecodeError, ValueError) as e:
            print(f"    Format error (attempt {attempt + 1}): {e}")
            print(f"    Raw payload: {raw[:500]}{'…' if len(raw) > 500 else ''}")
            if attempt == MAX_RETRIES - 1:
                return [
                    {"review_index": i + 1, "aspects": []} for i in range(len(batch))
                ]
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": FORMAT_REMINDER},
            ]
            time.sleep(2**attempt)
        except openai.RateLimitError:
            wait = 60 * (attempt + 1)
            print(f"    Rate limit — waiting {wait}s…")
            time.sleep(wait)
        except Exception as e:
            print(f"    Error (attempt {attempt + 1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return [
                    {"review_index": i + 1, "aspects": []} for i in range(len(batch))
                ]
            time.sleep(2**attempt)
    return [{"review_index": i + 1, "aspects": []} for i in range(len(batch))]


def main() -> None:
    if COMPLAINT_ASPECTS_AI_CSV.exists():
        print(f"Output already exists: {COMPLAINT_ASPECTS_AI_CSV}")
        print("Delete it to re-run classification.")
        return

    reviews = pd.read_csv(REVIEWS_CSV)
    stations = pd.read_csv(STATIONS_CSV)

    done = stations[stations["scrape_status"] == "done"]
    station_meta = done.set_index("opuic")[["name", "linguistic_region"]]

    reviews["year"] = pd.to_datetime(reviews["date_estimated"], errors="coerce").dt.year
    target = (
        reviews[reviews["text"].notna()]
        .join(station_meta, on="opuic", how="left")
        .copy()
        .reset_index(drop=True)
    )
    target["reviewer_label"] = target["language"].apply(
        lambda l: GROUP_LABELS.get(l, "Tourist")
    )

    print(f"Reviews to classify : {len(target):,}")
    print(f"Batch size          : {BATCH_SIZE}")
    print(f"Estimated batches   : {(len(target) + BATCH_SIZE - 1) // BATCH_SIZE}")
    print(f"Model               : {MODEL}")
    print(f"Output              : {COMPLAINT_ASPECTS_AI_CSV}")
    print()

    client = openai.OpenAI()
    all_rows = []

    with tqdm(total=len(target), unit="review", desc="Classifying") as pbar:
        for batch_start in range(0, len(target), BATCH_SIZE):
            batch = target.iloc[batch_start : batch_start + BATCH_SIZE]
            parsed = classify_batch(client, batch)

            for item in parsed:
                ri = item.get("review_index", 0) - 1  # convert to 0-based
                if ri < 0 or ri >= len(batch):
                    continue
                row = batch.iloc[ri]
                for asp in item.get("aspects", []):
                    kws = asp.get("keywords", [])
                    all_rows.append(
                        {
                            "opuic": row["opuic"],
                            "station_name": row["name"],
                            "linguistic_region": row["linguistic_region"],
                            "rating": int(row["rating"]),
                            "language": row.get("language", ""),
                            "reviewer_label": row["reviewer_label"],
                            "date_estimated": row.get("date_estimated", ""),
                            "text": row["text"],
                            "aspect": asp.get("aspect", ""),
                            "sentiment": asp.get("sentiment", ""),
                            "reason": asp.get("reason", ""),
                            "keywords": (
                                ", ".join(kws) if isinstance(kws, list) else str(kws)
                            ),
                        }
                    )

            pbar.update(len(batch))
            pbar.set_postfix(aspect_rows=len(all_rows))
            time.sleep(0.3)  # gentle pacing

    ai_df = (
        pd.DataFrame(all_rows)
        .sort_values(["station_name", "aspect", "sentiment", "rating"])
        .reset_index(drop=True)
    )
    ai_df.to_csv(COMPLAINT_ASPECTS_AI_CSV, index=False)

    print(f"\n✓ Done.")
    print(f"Total aspect rows: {len(ai_df):,}")
    print(
        f"Unique reviews: {ai_df.drop_duplicates(subset=['station_name','date_estimated','text']).shape[0]:,}"
    )
    print(f"  Saved to: {COMPLAINT_ASPECTS_AI_CSV}")
    print()
    print("Sentiment breakdown:")
    print(ai_df["sentiment"].value_counts().to_string())
    print()
    print("Aspect breakdown:")
    print(ai_df["aspect"].value_counts().to_string())


if __name__ == "__main__":
    main()
