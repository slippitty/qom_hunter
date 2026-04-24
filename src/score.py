"""
Score discovered segments against your calibrated fitness.

For each segment we fetch the detailed record, which includes the course record
time (xoms.qom for women, xoms.kom for men/overall). We pick the one you want
to chase with --gender. Then:

  rides: compute watts the record-holder needed; compare to your sustainable
         watts at that duration; score = (your_watts / their_watts) * 100.
         100 means "exactly matching their power output"; above 100 means the
         segment looks beatable, below means likely not.

  runs:  compute the record-holder's grade-adjusted pace; compare to yours at
         that duration; score = (their_gap_pace / your_gap_pace) * 100 so that
         above 100 means beatable (you are faster flat-equivalent).

This is a first-pass heuristic; it ignores pack dynamics, wind, lights,
pacing strategy, and gear. Use it as a filter to find candidates, then look
at the segments in the app before planning attempts.

Output: data/scored.json with each segment annotated, sorted best-first.
"""

import argparse
import json
import time
from pathlib import Path

from .discover import discover
from .efforts import fetch as fetch_activities
from .pace import (
    estimate_runner_gap,
    gap_seconds_per_km,
    your_sustainable_pace,
)
from .power import (
    estimate_rider_cp,
    required_watts,
    your_sustainable_watts,
)
from .strava import get_segment

DATA_PATH = Path(__file__).parent.parent / "data" / "scored.json"


def _parse_record_time(s: str) -> int | None:
    """Strava returns record times as 'M:SS', 'MM:SS', or 'H:MM:SS'. Return seconds."""
    if not s:
        return None
    parts = s.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def score_rides(
    segments: list[dict],
    cp: dict[str, float],
    target_xom: str,
    rider_mass: float,
    bike_mass: float,
) -> list[dict]:
    out = []
    for idx, seg in enumerate(segments, 1):
        print(f"  ride segment {idx}/{len(segments)}: {seg['name']}")
        try:
            detail = get_segment(seg["id"])
        except Exception as e:
            print(f"    error {e}, skipping")
            continue
        time.sleep(0.3)  # pacing

        record_str = (detail.get("xoms") or {}).get(target_xom)
        record_s = _parse_record_time(record_str)
        if not record_s:
            continue
        distance = detail.get("distance", 0)
        elev = detail.get("total_elevation_gain", 0)
        if distance < 100:
            continue

        their_watts = required_watts(
            distance, record_s, elev, rider_mass=rider_mass, bike_mass=bike_mass
        )
        your_watts = your_sustainable_watts(record_s, cp)
        if their_watts <= 0:
            continue
        score = (your_watts / their_watts) * 100

        out.append(
            {
                "id": detail["id"],
                "name": detail["name"],
                "activity_type": "Ride",
                "distance_m": distance,
                "elev_m": elev,
                "avg_grade": detail.get("average_grade"),
                "record_holder": "QOM" if target_xom == "qom" else "KOM",
                "record_time_s": record_s,
                "record_time_str": record_str,
                "their_est_watts": round(their_watts, 1),
                "your_est_watts": round(your_watts, 1),
                "score": round(score, 1),
                "start_latlng": detail.get("start_latlng"),
                "end_latlng": detail.get("end_latlng"),
                "map_polyline": (detail.get("map") or {}).get("polyline"),
                "effort_count": detail.get("effort_count"),
                "athlete_count": detail.get("athlete_count"),
            }
        )
    return out


def score_runs(
    segments: list[dict], gap: dict[str, float], target_xom: str
) -> list[dict]:
    out = []
    for idx, seg in enumerate(segments, 1):
        print(f"  run segment {idx}/{len(segments)}: {seg['name']}")
        try:
            detail = get_segment(seg["id"])
        except Exception as e:
            print(f"    error {e}, skipping")
            continue
        time.sleep(0.3)

        record_str = (detail.get("xoms") or {}).get(target_xom)
        record_s = _parse_record_time(record_str)
        if not record_s:
            continue
        distance = detail.get("distance", 0)
        if distance < 100:
            continue

        grade_decimal = (detail.get("average_grade") or 0) / 100.0
        their_pace = record_s / (distance / 1000.0)
        their_gap = gap_seconds_per_km(their_pace, grade_decimal)
        your_gap = your_sustainable_pace(record_s, gap)
        if your_gap <= 0:
            continue
        score = (their_gap / your_gap) * 100

        out.append(
            {
                "id": detail["id"],
                "name": detail["name"],
                "activity_type": "Run",
                "distance_m": distance,
                "elev_m": detail.get("total_elevation_gain"),
                "avg_grade": detail.get("average_grade"),
                "record_holder": "QOM" if target_xom == "qom" else "KOM",
                "record_time_s": record_s,
                "record_time_str": record_str,
                "their_gap_s_per_km": round(their_gap, 1),
                "your_gap_s_per_km": round(your_gap, 1),
                "score": round(score, 1),
                "start_latlng": detail.get("start_latlng"),
                "end_latlng": detail.get("end_latlng"),
                "map_polyline": (detail.get("map") or {}).get("polyline"),
                "effort_count": detail.get("effort_count"),
                "athlete_count": detail.get("athlete_count"),
            }
        )
    return out


def run(target_xom: str, rider_mass: float, bike_mass: float, limit: int | None):
    segs = discover(force=False)
    acts = fetch_activities(force=False)

    cp = estimate_rider_cp(acts)
    gap = estimate_runner_gap(acts)
    print(
        f"Calibration: rider {cp['short_watts']:.0f}/{cp['long_watts']:.0f} W, "
        f"runner {gap['short_s_per_km']:.0f}/{gap['long_s_per_km']:.0f} s/km."
    )

    ride_segs = segs.get("riding", [])
    run_segs = segs.get("running", [])
    if limit:
        ride_segs = ride_segs[:limit]
        run_segs = run_segs[:limit]

    print(f"Scoring {len(ride_segs)} ride segments and {len(run_segs)} run segments...")
    scored = []
    scored += score_rides(ride_segs, cp, target_xom, rider_mass, bike_mass)
    scored += score_runs(run_segs, gap, target_xom)

    scored.sort(key=lambda s: -s["score"])
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(scored, indent=2))
    print(f"\nSaved {len(scored)} scored segments to {DATA_PATH}.")

    print("\nTop 15 targets:")
    for s in scored[:15]:
        print(
            f"  {s['score']:>6.1f}  [{s['activity_type']:>4}] "
            f"{s['record_time_str']:>8}  {s['distance_m']:>5.0f}m "
            f"{s['avg_grade']:>+5.1f}%  {s['name']}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gender",
        choices=["qom", "kom"],
        default="qom",
        help="Which course record to chase (default: qom)",
    )
    parser.add_argument("--rider-mass", type=float, default=75.0)
    parser.add_argument("--bike-mass", type=float, default=9.0)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap segments per activity type, useful for quick tests",
    )
    args = parser.parse_args()
    run(args.gender, args.rider_mass, args.bike_mass, args.limit)
