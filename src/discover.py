"""
Tile Brooklyn into a grid of bounding boxes and call explore_segments on each.

Brooklyn's approximate bounding box:
  southwest ~ (40.5707, -74.0420)   (around Coney Island / Gravesend Bay)
  northeast ~ (40.7395, -73.8331)   (around Greenpoint / Bushwick border)

explore_segments returns up to ten segments per call, so in a dense area like
Brooklyn you want reasonably small tiles to avoid missing segments. A 6x6 grid
gives tiles roughly 3 km on a side, which is a decent balance for a free tier
with a 1000/day limit: 36 tiles x 2 activity types = 72 calls for discovery.

Run this script to (re)build data/segments.json. It is idempotent and caches,
so re-running without --force just reads from disk.
"""

import argparse
import json
import time
from pathlib import Path

from .strava import explore_segments

DATA_PATH = Path(__file__).parent.parent / "data" / "segments.json"

BROOKLYN_SW = (40.5707, -74.0420)
BROOKLYN_NE = (40.7395, -73.8331)
GRID = 6  # 6x6 tiles

ACTIVITY_TYPES = ("riding", "running")


def build_tiles(grid: int = GRID) -> list[tuple[float, float, float, float]]:
    sw_lat, sw_lng = BROOKLYN_SW
    ne_lat, ne_lng = BROOKLYN_NE
    d_lat = (ne_lat - sw_lat) / grid
    d_lng = (ne_lng - sw_lng) / grid
    tiles = []
    for i in range(grid):
        for j in range(grid):
            tile_sw_lat = sw_lat + i * d_lat
            tile_sw_lng = sw_lng + j * d_lng
            tile_ne_lat = tile_sw_lat + d_lat
            tile_ne_lng = tile_sw_lng + d_lng
            tiles.append((tile_sw_lat, tile_sw_lng, tile_ne_lat, tile_ne_lng))
    return tiles


def discover(force: bool = False) -> dict[str, list[dict]]:
    """
    Returns {'riding': [...], 'running': [...]}, deduped by segment id.
    Writes data/segments.json.
    """
    if DATA_PATH.exists() and not force:
        print(f"Using cached {DATA_PATH}. Pass --force to rebuild.")
        return json.loads(DATA_PATH.read_text())

    tiles = build_tiles()
    print(f"Scanning {len(tiles)} tiles x {len(ACTIVITY_TYPES)} activity types...")

    by_type: dict[str, dict[int, dict]] = {a: {} for a in ACTIVITY_TYPES}
    for idx, tile in enumerate(tiles, 1):
        for activity in ACTIVITY_TYPES:
            try:
                segs = explore_segments(*tile, activity_type=activity)
            except Exception as e:
                print(f"  tile {idx} {activity}: error {e}, skipping")
                continue
            for s in segs:
                by_type[activity][s["id"]] = s
            print(f"  tile {idx}/{len(tiles)} {activity}: +{len(segs)} segments")
            # gentle pacing so we do not hammer the 100/15min limit
            time.sleep(0.5)

    out = {a: list(by_type[a].values()) for a in ACTIVITY_TYPES}
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(out, indent=2))
    total = sum(len(v) for v in out.values())
    print(f"Saved {total} unique segments to {DATA_PATH}.")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Rebuild cache")
    args = parser.parse_args()
    discover(force=args.force)
