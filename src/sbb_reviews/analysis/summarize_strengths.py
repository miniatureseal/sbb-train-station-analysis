"""
Summarize AI-classified positive mentions for the 20 best-rated SBB stations
into a highlights report: what makes each station great, and what others can learn.

Reads complaint_aspects_ai.csv, filters to positive sentiment for the top 20
best-rated stations, groups by (station, aspect), and uses AI to produce a
concise summary.

Filters:
  MIN_MENTIONS  – absolute minimum positive mentions per (station, aspect) group
  MIN_PCT       – group must represent ≥ MIN_PCT of the station's total positive mentions
                  (prevents trivial aspects from appearing for high-volume stations)

Sorting: by station_overall_rating DESC, then highlight_count DESC
  (best stations first, dominant aspects first within each station)

Reads: data/derived/complaint_aspects_ai.csv, data/raw/stations.csv,
       data/derived/action_items.csv
Writes: data/derived/station_highlights.csv, data/derived/strength_gaps.csv

Set OPENAI_API_KEY in your environment before running.
"""

import json
import time
from collections import Counter

import openai
import pandas as pd
from tqdm import tqdm

from ..paths import (
    ACTION_ITEMS_CSV,
    COMPLAINT_ASPECTS_AI_CSV,
    STATION_HIGHLIGHTS_CSV,
    STATIONS_CSV,
    STRENGTH_GAPS_CSV,
)

MODEL = "gpt-5.4"
MIN_MENTIONS = 5
MIN_PCT = 0.10  # aspect must be ≥ 10 % of station's total positive mentions
TOP_N_STATIONS = 20
GROUPS_PER_CALL = 5
MAX_RETRIES = 3

SYSTEM_PROMPT = """\
You are a customer-experience analyst for SBB Swiss Federal Railways.
You will receive summaries of *positive* traveller feedback for specific train stations and aspects \
(e.g. Cleanliness, Staff/Service, Atmosphere).
Each entry is already reduced to a short reason phrase from a real review.
You are analysing the 20 best-rated stations in the SBB network.

For each group, return:
  summary – 1–2 sentences capturing the essence of what travellers praise about this \
aspect at this station. Be specific — reference the actual reasons mentioned \
(e.g. "Reviewers consistently highlight the warm, helpful attitude of staff, especially \
during disruptions and with international travellers.").

Respond with a JSON array, one object per group, in input order:
[{"group_index": <int>, "summary": "<str>"}, ...]
Output ONLY the JSON array, no markdown, no commentary.
"""


def top_keywords(kw_series: pd.Series, n: int = 8) -> str:
    all_kws = []
    for val in kw_series.dropna():
        all_kws.extend([k.strip().lower() for k in str(val).split(",") if k.strip()])
    return ", ".join(w for w, _ in Counter(all_kws).most_common(n))


def build_prompt(groups: list[dict]) -> str:
    lines = []
    for g in groups:
        lines.append(
            f"Group {g['group_index']} — {g['station_name']} | {g['aspect']} | "
            f"{g['highlight_count']} positive mentions | avg rating {g['avg_rating']:.1f}★ | "
            f"Station Google rating: {g['station_rating']:.1f}★ (one of the 20 best in the SBB network)"
        )
        for reason in g["reasons"]:
            lines.append(f"  - {reason}")
        lines.append("")
    return "\n".join(lines)


def summarise_batch(client: openai.OpenAI, groups: list[dict]) -> list[dict]:
    user_msg = build_prompt(groups)
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.splitlines()[1:])
            if raw.endswith("```"):
                raw = "\n".join(raw.splitlines()[:-1])
            return json.loads(raw.strip())
        except Exception as e:
            print(f"    Error (attempt {attempt + 1}): {e}")
            if attempt == MAX_RETRIES - 1:
                return [
                    {"group_index": g["group_index"], "summary": ""} for g in groups
                ]
            time.sleep(2**attempt)
    return [{"group_index": g["group_index"], "summary": ""} for g in groups]


def main() -> None:
    if STATION_HIGHLIGHTS_CSV.exists():
        print(f"Output already exists: {STATION_HIGHLIGHTS_CSV}")
        print("Delete it to re-run.")
        return

    if not COMPLAINT_ASPECTS_AI_CSV.exists():
        print(f"Input not found: {COMPLAINT_ASPECTS_AI_CSV}")
        print("Run run_ai_classification.py first.")
        return

    stations = pd.read_csv(STATIONS_CSV)
    done = stations[stations["scrape_status"] == "done"].copy()
    done = done.sort_values("review_count_google", ascending=False).drop_duplicates(
        subset="name"
    )
    best20 = done.nlargest(TOP_N_STATIONS, "overall_rating")[["name", "overall_rating"]]
    station_ratings = dict(zip(best20["name"], best20["overall_rating"]))

    print(f"Top {TOP_N_STATIONS} best-rated stations:")
    for name, rating in sorted(station_ratings.items(), key=lambda x: -x[1]):
        print(f"  {rating:.1f}★  {name}")
    print()

    ai = pd.read_csv(COMPLAINT_ASPECTS_AI_CSV)
    ai["date_estimated"] = pd.to_datetime(ai["date_estimated"], errors="coerce")
    pos = ai[
        (ai["sentiment"] == "positive") & (ai["station_name"].isin(station_ratings))
    ].copy()

    station_pos_totals = pos.groupby("station_name").size().rename("station_pos_total")

    grouped = (
        pos.groupby(["station_name", "linguistic_region", "aspect"])
        .agg(
            highlight_count=("reason", "count"),
            avg_rating=("rating", "mean"),
            earliest=("date_estimated", "min"),
            latest=("date_estimated", "max"),
            reasons=("reason", list),
            keywords=("keywords", lambda x: top_keywords(x)),
        )
        .reset_index()
    )
    grouped = grouped.join(station_pos_totals, on="station_name")
    grouped["pct_of_station"] = (
        grouped["highlight_count"] / grouped["station_pos_total"]
    )
    grouped = grouped[
        (grouped["highlight_count"] >= MIN_MENTIONS)
        & (grouped["pct_of_station"] >= MIN_PCT)
    ].copy()

    grouped["station_overall_rating"] = grouped["station_name"].map(station_ratings)

    aspect_station_counts = (
        grouped.groupby("aspect")["station_name"]
        .nunique()
        .rename("stations_sharing_strength")
    )
    grouped = grouped.join(aspect_station_counts, on="aspect")
    grouped = grouped.sort_values(
        ["stations_sharing_strength", "pct_of_station"], ascending=[False, False]
    ).reset_index(drop=True)

    print(f"Qualifying (station, aspect) pairs: {len(grouped)}")
    print(
        f"Model: {MODEL}  |  Groups per call: {GROUPS_PER_CALL}"
        f"  |  Min mentions: {MIN_MENTIONS} & {MIN_PCT * 100:.0f}%"
    )
    print()

    group_dicts = []
    for i, row in grouped.iterrows():
        reason_counts = Counter(
            r.strip() for r in row["reasons"] if isinstance(r, str) and r.strip()
        )
        top_reasons = [
            f"{r} ({c}×)" if c > 1 else r for r, c in reason_counts.most_common(15)
        ]
        group_dicts.append(
            {
                "group_index": i,
                "station_name": row["station_name"],
                "aspect": row["aspect"],
                "highlight_count": row["highlight_count"],
                "avg_rating": row["avg_rating"],
                "station_rating": row["station_overall_rating"],
                "reasons": top_reasons,
            }
        )

    client = openai.OpenAI()
    results = {}

    batches = [
        group_dicts[i : i + GROUPS_PER_CALL]
        for i in range(0, len(group_dicts), GROUPS_PER_CALL)
    ]
    for batch in tqdm(batches, unit="batch", desc="Summarising"):
        parsed = summarise_batch(client, batch)
        for item in parsed:
            results[item["group_index"]] = {"summary": item.get("summary", "")}
        time.sleep(0.3)

    rows = []
    for i, row in grouped.iterrows():
        r = results.get(i, {"summary": ""})
        rows.append(
            {
                "station_name": row["station_name"],
                "station_overall_rating": row["station_overall_rating"],
                "linguistic_region": row["linguistic_region"],
                "aspect": row["aspect"],
                "stations_sharing_strength": row["stations_sharing_strength"],
                "highlight_count": row["highlight_count"],
                "avg_rating": round(row["avg_rating"], 2),
                "pct_of_station": round(row["pct_of_station"] * 100, 1),
                "station_pos_total": row["station_pos_total"],
                "earliest_mention": (
                    row["earliest"].date() if pd.notna(row["earliest"]) else ""
                ),
                "latest_mention": (
                    row["latest"].date() if pd.notna(row["latest"]) else ""
                ),
                "top_keywords": row["keywords"],
                "summary": r["summary"],
            }
        )

    out_df = pd.DataFrame(rows)
    out_df.to_csv(STATION_HIGHLIGHTS_CSV, index=False)

    print(f"\n✓ Done. {len(out_df)} highlights saved to {STATION_HIGHLIGHTS_CSV}")
    print()
    print("Top 15 highlights (most transferable aspects first):")
    print(
        out_df.head(15)[
            [
                "stations_sharing_strength",
                "aspect",
                "station_name",
                "station_overall_rating",
                "highlight_count",
                "pct_of_station",
            ]
        ].to_string(index=False)
    )

    if not ACTION_ITEMS_CSV.exists():
        print(f"\nSkipping gap analysis: {ACTION_ITEMS_CSV} not found.")
        print("Run summarize_complaints.py first to generate action_items.csv.")
        return

    action_df = pd.read_csv(ACTION_ITEMS_CSV)
    complaint_by_aspect = (
        action_df.groupby("aspect")["complaint_count"].sum().rename("complaint_rows")
    )
    strength_by_aspect = (
        out_df.groupby("aspect")["stations_sharing_strength"]
        .first()
        .rename("strength_stations")
    )

    gap_df = (
        pd.DataFrame({"complaint_rows": complaint_by_aspect})
        .join(strength_by_aspect, how="outer")
        .fillna(0)
        .astype(int)
        .sort_values("complaint_rows", ascending=False)
    )

    def classify(row):
        if row["strength_stations"] >= 3 and row["complaint_rows"] >= 20:
            return ("learnable",)
        if row["strength_stations"] == 0 and row["complaint_rows"] >= 20:
            return "systemic_gap"
        return "minor"

    gap_df[["category"]] = gap_df.apply(lambda r: pd.Series(classify(r)), axis=1)
    gap_df = gap_df.reset_index().rename(columns={"index": "aspect"})
    gap_df.to_csv(STRENGTH_GAPS_CSV, index=False)

    print(f"\n✓ Gap analysis saved to {STRENGTH_GAPS_CSV}")
    print()
    print("Learnable gaps (best stations set the standard):")
    learnable = gap_df[gap_df["category"] == "learnable"]
    print(
        learnable[["aspect", "strength_stations", "complaint_rows"]].to_string(
            index=False
        )
    )
    print()
    print("Systemic gaps (no best-practice to copy — needs investment):")
    systemic = gap_df[gap_df["category"] == "systemic_gap"]
    print(systemic[["aspect", "complaint_rows"]].to_string(index=False))


if __name__ == "__main__":
    main()
