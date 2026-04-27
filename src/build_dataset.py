"""
Build the static dataset that powers the GitHub Pages site.

Scans a region with `segments/explore`, pulls detail for each segment, and
writes `docs/segments.json` in a compact shape the browser can load and filter
client-side.

Region covers NYC plus Jersey City, Hoboken, and Yonkers.

This script is incremental and resumable: it writes a checkpoint after each
segment detail call, so if you hit the daily rate limit or stop it with
Ctrl-C, re-running picks up where it left off. The explore phase also caches.

Expect 90 minutes to several hours depending on segment density. The
bottleneck is Strava's 200-req-per-15-min and 2000-req-per-day limits.
"""

import argparse
import json
import time
from pathlib import Path

from .strava import explore_segments, get_segment

ROOT = Path(__file__).parent.parent
CHECKPOINT = ROOT / "docs" / ".dataset_checkpoint.json"
OUTPUT = ROOT / "docs" / "segments.json"

# Region bounding box: southwest and northeast corners.
# Default: Brooklyn + Manhattan, which fits comfortably in a single GitHub
# Actions run. Expand later by adjusting these or by setting the
# QOM_REGION_SW / QOM_REGION_NE env vars.
import os as _os

def _env_box(name, default):
    v = _os.environ.get(name)
    if not v:
        return default
    parts = [float(x) for x in v.split(",")]
    return (parts[0], parts[1])

REGION_SW = _env_box("QOM_REGION_SW", (40.5707, -74.0420))
REGION_NE = _env_box("QOM_REGION_NE", (40.8200, -73.9100))
GRID = int(_os.environ.get("QOM_GRID", "6"))
ACTIVITY_TYPES = ("riding", "running")


def _tiles():
    sw_lat, sw_lng = REGION_SW
    ne_lat, ne_lng = REGION_NE
    d_lat = (ne_lat - sw_lat) / GRID
    d_lng = (ne_lng - sw_lng) / GRID
    for i in range(GRID):
        for j in range(GRID):
            yield (
                sw_lat + i * d_lat,
                sw_lng + j * d_lng,
                sw_lat + (i + 1) * d_lat,
                sw_lng + (j + 1) * d_lng,
            )


def _load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"explored": False, "segment_ids": [], "details": {}}


def _save_checkpoint(state: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(state))


def _parse_record(s: str) -> int | None:
    """Parse 'M:SS' / 'MM:SS' / 'H:MM:SS' into seconds."""
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


def explore_phase(state: dict):
    """Populate state['segment_ids'] by walking all tiles x activity types."""
    if state.get("explored"):
        print(f"Explore phase already done: {len(state['segment_ids'])} segments.")
        return

    seen: set[int] = set(state.get("segment_ids", []))
    tiles = list(_tiles())
    total = len(tiles) * len(ACTIVITY_TYPES)
    i = 0
    for tile in tiles:
        for activity in ACTIVITY_TYPES:
            i += 1
            try:
                segs = explore_segments(*tile, activity_type=activity)
            except Exception as e:
                print(f"  explore {i}/{total} {activity}: error {e}")
                continue
            before = len(seen)
            for s in segs:
                seen.add(s["id"])
            print(f"  explore {i}/{total} {activity}: +{len(seen) - before} new (total {len(seen)})")
            time.sleep(0.5)
    state["segment_ids"] = sorted(seen)
    state["explored"] = True
    _save_checkpoint(state)
    print(f"Explore phase complete: {len(seen)} unique segments.")


def detail_phase(state: dict):
    """Fetch segment detail for every id, saving after each one."""
    remaining = [sid for sid in state["segment_ids"] if str(sid) not in state["details"]]
    print(f"Detail phase: {len(remaining)} segments to fetch, {len(state['details'])} cached.")
    for idx, sid in enumerate(remaining, 1):
        try:
            d = get_segment(sid)
        except Exception as e:
            print(f"  detail {idx}/{len(remaining)} seg {sid}: error {e}")
            # save progress even on errors
            _save_checkpoint(state)
            time.sleep(2)
            continue
        # keep only the fields the frontend needs, to keep the JSON small
        xoms = d.get("xoms") or {}
        state["details"][str(sid)] = {
            "id": d["id"],
            "name": d.get("name"),
            "type": d.get("activity_type"),  # "Ride" or "Run"
            "dist_m": d.get("distance"),
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
            "qom_s": _parse_record(xoms.get("qom")),
            "kom_s": _parse_record(xoms.get("kom")),
            "qom_str": xoms.get("qom"),
            "kom_str": xoms.get("kom"),
        }
        if idx % 10 == 0:
            _save_checkpoint(state)
            print(f"  detail {idx}/{len(remaining)} seg {sid}: ok (checkpoint saved)")
        else:
            print(f"  detail {idx}/{len(remaining)} seg {sid}: ok")
        time.sleep(0.4)
    _save_checkpoint(state)


def write_output(state: dict):
    """Write the compact JSON the browser will load.

    Preserves any `from_activity: true` segments already in the existing
    output file, so the scheduled rebuild doesn't clobber segments that came
    from enrich_from_activities.
    """
    records = []
    for d in state["details"].values():
        if not d.get("start") or not d.get("dist_m"):
            continue
        # compute qom speed in km/h for filtering
        qom_s = d.get("qom_s")
        kom_s = d.get("kom_s")
        dist_km = d["dist_m"] / 1000.0
        d["qom_kph"] = (dist_km / (qom_s / 3600)) if qom_s else None
        d["kom_kph"] = (dist_km / (kom_s / 3600)) if kom_s else None
        # qom pace for runs, min/km
        d["qom_min_per_km"] = (qom_s / 60 / dist_km) if qom_s else None
        d["kom_min_per_km"] = (kom_s / 60 / dist_km) if kom_s else None
        records.append(d)

    # preserve personal segments from the existing file, if any
    preserved = 0
    if OUTPUT.exists():
        try:
            existing = json.loads(OUTPUT.read_text())
            existing_personal = [
                s for s in existing.get("segments", [])
                if s.get("from_activity")
            ]
            # avoid duplicating an id that's now in the explore set
            new_ids = {r["id"] for r in records}
            for s in existing_personal:
                if s["id"] not in new_ids:
                    records.append(s)
                    preserved += 1
        except Exception:
            pass

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"segments": records, "built_at": int(time.time())}))
    print(f"\nWrote {len(records)} segments to {OUTPUT} ({OUTPUT.stat().st_size / 1024:.0f} KB).")
    if preserved:
        print(f"  ({preserved} personal segments preserved from prior enrichment)")


def run():
    state = _load_checkpoint()
    explore_phase(state)
    detail_phase(state)
    write_output(state)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="Delete checkpoint and start over")
    args = parser.parse_args()
    if args.reset and CHECKPOINT.exists():
        CHECKPOINT.unlink()
        print("Checkpoint cleared.")
    run()
