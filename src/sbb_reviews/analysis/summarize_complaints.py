"""
Summarize AI-classified complaints for the 10 worst-rated SBB stations
into a prioritized, ranked list of action items.

Priority score = pct_of_station × severity_weight × recency_weight × (1 + station_weight)
  pct_of_station   = complaint_count / station total negative complaints  (station-size neutral)
  severity_weight  = (6 - avg_rating) / 5
  station_weight   = (5 - station_overall_rating)
  recency_weight   based on latest_complaint date (see RECENCY_WEIGHTS)

Reads: data/derived/complaint_aspects_ai.csv, data/raw/stations.csv
Writes: data/derived/action_items.csv (sorted by priority_rank ascending)

Set OPENAI_API_KEY in your environment before running.
"""

import json
import time
from collections import Counter
from datetime import date

import openai
import pandas as pd
from tqdm import tqdm

from ..paths import ACTION_ITEMS_CSV, COMPLAINT_ASPECTS_AI_CSV, STATIONS_CSV

MODEL = "gpt-5.4"
MIN_COMPLAINTS = 5
MIN_PCT = 0.10  # aspect must be ≥ 10 % of station's total negative complaints
TOP_N_STATIONS = 20
GROUPS_PER_CALL = 5
MAX_RETRIES = 3
TODAY = date.today()

RECENCY_WEIGHTS = [
    (90, 2.0, "active"),
    (180, 1.5, "recent"),
    (365, 1.0, "moderate"),
    (730, 0.5, "aging"),
    (None, 0.2, "stale"),
]

SYSTEM_PROMPT = """\
You are an operations analyst for SBB Swiss Federal Railways.
You will receive complaint summaries for specific train stations and aspects (e.g. Cleanliness, Safety).
Each complaint is already reduced to a short reason phrase.
You are focusing on the 10 worst-rated stations in the network.

For each group, return:
  summary        – 1–2 sentences describing the core complaint pattern concisely,
                   referencing the specific issues mentioned.

Respond with a JSON array, one object per group, in input order:
[{"group_index": <int>, "summary": "<str>"}, ...]
Output ONLY the JSON array, no markdown, no commentary.
"""


def recency(latest: date) -> tuple[float, str]:
    days = (TODAY - latest).days
    for threshold, weight, tier in RECENCY_WEIGHTS:
        if threshold is None or days <= threshold:
            return weight, tier
    return 0.2, "stale"


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
            f"{g['complaint_count']} complaints | avg {g['avg_rating']:.1f}★ | "
            f"Station Google rating: {g['station_rating']:.1f}★ (one of the 10 worst in the SBB network) | "
            f"Recency: {g['recency_tier']} (latest complaint: {g['latest']})"
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
                    {
                        "group_index": g["group_index"],
                        "summary": "",
                        "recommendation": "",
                    }
                    for g in groups
                ]
            time.sleep(2**attempt)
    return [
        {"group_index": g["group_index"], "summary": "", "recommendation": ""}
        for g in groups
    ]


def main() -> None:
    if ACTION_ITEMS_CSV.exists():
        print(f"Output already exists: {ACTION_ITEMS_CSV}")
        print("Delete it to re-run.")
        return

    if not COMPLAINT_ASPECTS_AI_CSV.exists():
        print(f"Input not found: {COMPLAINT_ASPECTS_AI_CSV}")
        print("Run run_ai_classification.py first.")
        return

    # ── Identify the 10 worst-rated stations ─────────────────────────────────
    stations = pd.read_csv(STATIONS_CSV)
    done = stations[stations["scrape_status"] == "done"].copy()
    # Deduplicate stations with the same name (keep most-reviewed)
    done = done.sort_values("review_count_google", ascending=False).drop_duplicates(
        subset="name"
    )
    worst_n = done.nsmallest(TOP_N_STATIONS, "overall_rating")[
        ["name", "overall_rating"]
    ]
    station_ratings = dict(zip(worst_n["name"], worst_n["overall_rating"]))

    print(f"Top {TOP_N_STATIONS} worst-rated stations:")
    for name, rating in sorted(station_ratings.items(), key=lambda x: x[1]):
        print(f"  {rating:.1f}★  {name}")
    print()

    # ── Load and filter complaints ────────────────────────────────────────────
    ai = pd.read_csv(COMPLAINT_ASPECTS_AI_CSV)
    ai["date_estimated"] = pd.to_datetime(ai["date_estimated"], errors="coerce")
    neg = ai[
        (ai["sentiment"] == "negative") & (ai["station_name"].isin(station_ratings))
    ].copy()

    station_neg_totals = neg.groupby("station_name").size().rename("station_neg_total")

    grouped = (
        neg.groupby(["station_name", "linguistic_region", "aspect"])
        .agg(
            complaint_count=("reason", "count"),
            avg_rating=("rating", "mean"),
            earliest=("date_estimated", "min"),
            latest=("date_estimated", "max"),
            reasons=("reason", list),
            keywords=("keywords", lambda x: top_keywords(x)),
        )
        .reset_index()
    )
    grouped = grouped.join(station_neg_totals, on="station_name")
    grouped["pct_of_station"] = (
        grouped["complaint_count"] / grouped["station_neg_total"]
    )
    grouped = grouped[
        (grouped["complaint_count"] >= MIN_COMPLAINTS)
        & (grouped["pct_of_station"] >= MIN_PCT)
    ].copy()

    # ── Compute priority score ────────────────────────────────────────────────
    def compute_priority(row):
        stn_rating = station_ratings.get(row["station_name"], 4.0)
        sev_weight = (6 - row["avg_rating"]) / 5
        stn_weight = 5 - stn_rating
        latest_date = (
            row["latest"].date() if pd.notna(row["latest"]) else date(2000, 1, 1)
        )
        rec_weight, rec_tier = recency(latest_date)
        score = row["pct_of_station"] * 100 * sev_weight * rec_weight * (1 + stn_weight)
        return pd.Series(
            {
                "priority_score": round(score, 2),
                "recency_tier": rec_tier,
                "station_overall_rating": stn_rating,
                "latest_date": latest_date,
            }
        )

    extra = grouped.apply(compute_priority, axis=1)
    grouped = pd.concat([grouped, extra], axis=1)
    grouped = grouped.sort_values("priority_score", ascending=False).reset_index(
        drop=True
    )
    grouped["priority_rank"] = grouped.index + 1

    print(f"Qualifying (station, aspect) pairs: {len(grouped)}")
    print(
        f"Model: {MODEL}  |  Groups per call: {GROUPS_PER_CALL}  |  Min complaints: {MIN_COMPLAINTS} & {MIN_PCT*100:.0f}%"
    )
    print()

    # ── Build group dicts for the API ─────────────────────────────────────────
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
                "complaint_count": row["complaint_count"],
                "avg_rating": row["avg_rating"],
                "station_rating": row["station_overall_rating"],
                "recency_tier": row["recency_tier"],
                "latest": str(row["latest_date"]),
                "reasons": top_reasons,
            }
        )

    # ── Summarise in batches ──────────────────────────────────────────────────
    client = openai.OpenAI()
    results = {}

    batches = [
        group_dicts[i : i + GROUPS_PER_CALL]
        for i in range(0, len(group_dicts), GROUPS_PER_CALL)
    ]
    for batch in tqdm(batches, unit="batch", desc="Summarising"):
        parsed = summarise_batch(client, batch)
        for item in parsed:
            results[item["group_index"]] = {
                "summary": item.get("summary", ""),
                "recommendation": item.get("recommendation", ""),
            }
        time.sleep(0.3)

    # ── Assemble output ───────────────────────────────────────────────────────
    rows = []
    for i, row in grouped.iterrows():
        r = results.get(i, {"summary": "", "recommendation": ""})
        rows.append(
            {
                "priority_rank": row["priority_rank"],
                "priority_score": row["priority_score"],
                "station_name": row["station_name"],
                "station_overall_rating": row["station_overall_rating"],
                "linguistic_region": row["linguistic_region"],
                "aspect": row["aspect"],
                "complaint_count": row["complaint_count"],
                "avg_rating": round(row["avg_rating"], 2),
                "recency_tier": row["recency_tier"],
                "earliest_complaint": (
                    row["earliest"].date() if pd.notna(row["earliest"]) else ""
                ),
                "latest_complaint": row["latest_date"],
                "top_keywords": row["keywords"],
                "station_neg_total": row["station_neg_total"],
                "pct_of_station": round(row["pct_of_station"] * 100, 1),
                "summary": r["summary"],
                "recommendation": r["recommendation"],
            }
        )

    out_df = pd.DataFrame(rows)
    out_df.to_csv(ACTION_ITEMS_CSV, index=False)

    print(f"\n✓ Done. {len(out_df)} ranked action items saved to {ACTION_ITEMS_CSV}")
    print()
    print("Top 10 priority action items:")
    print(
        out_df.head(10)[
            [
                "priority_rank",
                "station_name",
                "aspect",
                "complaint_count",
                "recency_tier",
                "priority_score",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
