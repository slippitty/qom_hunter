"""
Mini test build. Scans a single small bounding box, fetches detail for every
segment found, and writes docs/segments.json. Verbose output shows each API
call as it happens.

Default box: a tight rectangle around Prospect Park, Brooklyn. About 2 km on
a side. Expect roughly 15-30 unique segments per sport, so ~30-60 detail
calls total. At our 0.4s pacing plus network time, that's 1-2 minutes total
and well under any rate limit.

Run: python -m src.build_mini
Optionally: python -m src.build_mini --box prospect | central | williamsburg

To preview without making any API calls (just shows the plan), use --dry.
"""

import argparse
import json
import time
from pathlib import Path

from .strava import explore_segments, get_segment

OUTPUT = Path(__file__).parent.parent / "docs" / "segments.json"

# Tight test boxes. Each ~2 km on a side, in dense segment territory.
BOXES = {
    "prospect": {
        "name": "Prospect Park, Brooklyn",
        "sw": (40.6500, -73.9800),
        "ne": (40.6720, -73.9580),
    },
    "central": {
        "name": "Central Park, Manhattan",
        "sw": (40.7680, -73.9810),
        "ne": (40.8000, -73.9490),
    },
    "williamsburg": {
        "name": "Williamsburg waterfront",
        "sw": (40.7080, -73.9700),
        "ne": (40.7280, -73.9500),
    },
    "hoboken": {
        "name": "Hoboken",
        "sw": (40.7350, -74.0400),
        "ne": (40.7560, -74.0220),
    },
}


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


def run(box_name: str, dry: bool):
    box = BOXES[box_name]
    sw, ne = box["sw"], box["ne"]
    print(f"\n=== Mini build: {box['name']} ===")
    print(f"Box: SW {sw}  NE {ne}")
    print(f"Size: {(ne[0]-sw[0])*111:.1f} km tall x {(ne[1]-sw[1])*84:.1f} km wide")
    print()

    if dry:
        print("(dry run, no API calls will be made)")
        return

    # phase 1: explore
    print(">>> Phase 1: explore (2 API calls)")
    t0 = time.time()
    seen = {}
    for sport in ("riding", "running"):
        print(f"  calling explore_segments({sw[0]}, {sw[1]}, {ne[0]}, {ne[1]}, '{sport}')")
        try:
            segs = explore_segments(sw[0], sw[1], ne[0], ne[1], activity_type=sport)
        except Exception as e:
            print(f"    ERROR: {e}")
            return
        print(f"    got {len(segs)} segments back")
        for s in segs:
            print(f"      [{s['id']}] {s['name']} ({sport})")
            seen[s["id"]] = sport
        time.sleep(0.5)
    print(f"  explore took {time.time()-t0:.1f}s, {len(seen)} unique segments")
    print()

    # phase 2: detail
    print(f">>> Phase 2: detail ({len(seen)} API calls)")
    t0 = time.time()
    out = []
    for idx, (sid, sport) in enumerate(seen.items(), 1):
        print(f"  [{idx}/{len(seen)}] get_segment({sid}) ...", end=" ", flush=True)
        try:
            d = get_segment(sid)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
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
        }
        # add derived fields the frontend uses
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

        out.append(record)
        # show what we got
        qom_disp = xoms.get("qom") or "—"
        kom_disp = xoms.get("kom") or "—"
        print(
            f"{record['name'][:35]:35s} "
            f"{dist_m:>5.0f}m  "
            f"QOM {qom_disp:>7s}  KOM {kom_disp:>7s}"
        )
        time.sleep(0.4)

    print(f"  detail took {time.time()-t0:.1f}s")
    print()

    # write output
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "segments": out,
        "built_at": int(time.time()),
        "_mini": box_name,
    }))
    print(f">>> Wrote {len(out)} segments to {OUTPUT}")
    print(f"   ({OUTPUT.stat().st_size / 1024:.1f} KB, {len([s for s in out if s['type']=='Ride'])} rides, {len([s for s in out if s['type']=='Run'])} runs)")
    print()
    print("To view: serve docs/ locally and open in a browser.")
    print("  cd docs && python -m http.server 8089")
    print("  then visit http://localhost:8089/")
    print(f"  search for: {box['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--box", choices=list(BOXES.keys()), default="prospect")
    parser.add_argument("--dry", action="store_true", help="Show plan, no API calls")
    args = parser.parse_args()
    run(args.box, args.dry)
