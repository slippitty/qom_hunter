"""
Enrich segments.json with segments pulled from your own Strava activities.

Why: Strava's explore endpoint only returns the most popular segments in each
tile, missing the obscure ones — including, often, the very segments where
you hold the QOM. Activities, by contrast, list every segment you crossed,
popular or not. Pulling from activities surfaces those soft segments.

How it works:
1. Fetch your last N activities (default 200, tune via --limit).
   On subsequent runs, only fetches activities newer than the last one we
   processed — tracked in docs/.enrich_checkpoint.json.
2. For each, fetch the detailed activity which includes segment_efforts.
3. For every unique segment id we haven't already seen, fetch segment detail
   and add it to the dataset.

Pass --backfill to ignore the checkpoint and walk the full --limit window.
This is what you want on the first run, or to catch up if you want to scan
older activities.

Cost: roughly N activity-detail calls + (new segments) detail calls. Steady
state with the checkpoint is just N for new activities since last run, plus
their unfamiliar segments.

Output: appends to docs/segments.json (does not overwrite existing entries).
Idempotent — re-running only fetches segments not already present.

Run: python -m src.enrich_from_activities [--limit 200] [--backfill] [--dry]
"""

import argparse
import json
import time
from pathlib import Path

from .strava import _request, get_segment, recent_activities

ROOT = Path(__file__).parent.parent
OUTPUT = ROOT / "docs" / "segments.json"
CHECKPOINT = ROOT / "docs" / ".enrich_checkpoint.json"


def _parse_record(s):
    if not s:
        return None
    try:
        parts = [int(p) for p in s.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _load_existing() -> dict:
    """Return existing dataset or a fresh skeleton."""
    if OUTPUT.exists():
        return json.loads(OUTPUT.read_text())
    return {"segments": [], "built_at": int(time.time())}


def _load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text())
        except Exception:
            pass
    return {"last_activity_id": 0, "fully_backfilled": False}


def _save_checkpoint(state: dict):
    CHECKPOINT.write_text(json.dumps(state))


def _segment_record(d: dict) -> dict:
    """Convert raw Strava segment detail into our compact frontend shape."""
    xoms = d.get("xoms") or {}
    qom_s = _parse_record(xoms.get("qom"))
    kom_s = _parse_record(xoms.get("kom"))
    dist_m = d.get("distance") or 0
    record = {
        "id": d["id"],
        "name": d.get("name"),
        "type": d.get("activity_type"),
        "dist_m": dist_m,
        "elev_m": d.get("total_elevation_gain"),
        "grade": d.get("average_grade"),
        "max_grade": d.get("maximum_grade"),
        "start": d.get("start_latlng"),
        "end": d.get("end_latlng"),
        "city": d.get("city"),
        "state": d.get("state"),
        "poly": (d.get("map") or {}).get("polyline"),
        "effort_count": d.get("effort_count"),
        "athlete_count": d.get("athlete_count"),
        "qom_s": qom_s,
        "kom_s": kom_s,
        "qom_str": xoms.get("qom"),
        "kom_str": xoms.get("kom"),
        "from_activity": True,  # marks segments added via enrichment
    }
    if dist_m and qom_s:
        dist_km = dist_m / 1000
        record["qom_kph"] = dist_km / (qom_s / 3600)
        record["qom_min_per_km"] = qom_s / 60 / dist_km
    else:
        record["qom_kph"] = None
        record["qom_min_per_km"] = None
    if dist_m and kom_s:
        dist_km = dist_m / 1000
        record["kom_kph"] = dist_km / (kom_s / 3600)
        record["kom_min_per_km"] = kom_s / 60 / dist_km
    else:
        record["kom_kph"] = None
        record["kom_min_per_km"] = None
    return record


def _get_activity(activity_id: int) -> dict:
    """Detailed activity, includes segment_efforts."""
    return _request("GET", f"/activities/{activity_id}", params={"include_all_efforts": "true"})


def run(limit: int, dry: bool, backfill: bool):
    existing = _load_existing()
    known_ids = {s["id"] for s in existing["segments"]}
    print(f"Existing dataset: {len(known_ids)} segments")

    cp = _load_checkpoint()
    last_id = cp.get("last_activity_id", 0)
    if backfill or last_id == 0:
        print(f"Mode: backfill — walking last {limit} activities regardless of checkpoint")
    else:
        print(f"Mode: incremental — only activities with id > {last_id}")

    print(f"Fetching activities...")
    if dry:
        print("(dry run — would fetch activities and segment details now)")
        return
    all_acts = recent_activities(per_page=100)[:limit]

    if backfill or last_id == 0:
        activities = all_acts
    else:
        activities = [a for a in all_acts if a["id"] > last_id]
    print(f"Got {len(activities)} activities to scan ({len(all_acts)} fetched, {len(all_acts) - len(activities)} skipped via checkpoint)")
    if not activities:
        print("No new activities since last run. Exiting.")
        return

    # phase 1: collect segment ids from each activity
    print("\n>>> Phase 1: extract segment efforts from each activity")
    candidate_segments: dict[int, str] = {}  # id -> activity_type ("Ride"/"Run")
    highest_act_id = last_id
    for idx, act in enumerate(activities, 1):
        try:
            full = _get_activity(act["id"])
        except Exception as e:
            print(f"  [{idx}/{len(activities)}] act {act['id']}: error {e}")
            time.sleep(2)
            continue
        if full["id"] > highest_act_id:
            highest_act_id = full["id"]
        efforts = full.get("segment_efforts") or []
        new_in_this = 0
        for eff in efforts:
            seg = eff.get("segment") or {}
            sid = seg.get("id")
            if not sid or sid in known_ids or sid in candidate_segments:
                continue
            candidate_segments[sid] = seg.get("activity_type") or full.get("type", "Ride")
            new_in_this += 1
        print(f"  [{idx}/{len(activities)}] {full.get('name','?')[:40]:40s} +{new_in_this} new candidates")
        time.sleep(0.4)

    print(f"\nCandidate segments to fetch: {len(candidate_segments)}")
    if not candidate_segments:
        print("Nothing new to add.")
        cp["last_activity_id"] = highest_act_id
        _save_checkpoint(cp)
        return

    # phase 2: fetch segment detail for each new id
    print("\n>>> Phase 2: fetch detail for each new segment")
    added = []
    rate_limited = False
    for idx, sid in enumerate(candidate_segments, 1):
        try:
            d = get_segment(sid)
        except Exception as e:
            print(f"  [{idx}/{len(candidate_segments)}] seg {sid}: error {e}")
            # if we're hitting 429s consistently, stop early so we don't waste retries
            if "429" in str(e):
                rate_limited = True
                break
            time.sleep(2)
            continue
        record = _segment_record(d)
        if not record.get("start") or not record.get("dist_m"):
            print(f"  [{idx}/{len(candidate_segments)}] seg {sid}: skip (incomplete)")
            continue
        added.append(record)
        print(
            f"  [{idx}/{len(candidate_segments)}] seg {sid}: "
            f"{record['name'][:35]:35s} {record['dist_m']:>5.0f}m "
            f"QOM {record.get('qom_str') or '—':>7s} "
            f"({record.get('athlete_count') or 0} athletes)"
        )
        time.sleep(0.4)
        # save periodically so we don't lose progress on rate-limit
        if idx % 20 == 0:
            existing["segments"].extend(added)
            existing["built_at"] = int(time.time())
            OUTPUT.write_text(json.dumps(existing))
            added = []
            print(f"    (saved progress: {len(existing['segments'])} total)")

    if added:
        existing["segments"].extend(added)
        existing["built_at"] = int(time.time())
        OUTPUT.write_text(json.dumps(existing))

    # only advance the activity-id checkpoint if we actually got through Phase 2
    # without rate limits — otherwise next run should re-process those activities
    if not rate_limited:
        cp["last_activity_id"] = highest_act_id
        cp["fully_backfilled"] = True
        _save_checkpoint(cp)
        print(f"\nCheckpoint advanced to activity id {highest_act_id}")
    else:
        print(f"\nRate limited; checkpoint NOT advanced. Re-run later to continue.")

    total = len(existing["segments"])
    enriched = sum(1 for s in existing["segments"] if s.get("from_activity"))
    print(f">>> Done. Dataset now has {total} segments ({enriched} from your activities).")
    print(f"   File: {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200, help="Max activities to scan")
    parser.add_argument("--backfill", action="store_true", help="Ignore checkpoint, walk full window")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    run(args.limit, args.dry, args.backfill)
