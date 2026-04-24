"""
Pull the authenticated athlete's recent activities for pace/power calibration.

We fetch the last ~90 days, which is a reasonable window: long enough to have
samples at multiple durations, short enough that it reflects current fitness.
Cached in data/efforts.json. Re-run with --force to refresh.
"""

import argparse
import json
import time
from pathlib import Path

from .strava import recent_activities

DATA_PATH = Path(__file__).parent.parent / "data" / "efforts.json"
LOOKBACK_DAYS = 90


def fetch(force: bool = False) -> list[dict]:
    if DATA_PATH.exists() and not force:
        print(f"Using cached {DATA_PATH}. Pass --force to refresh.")
        return json.loads(DATA_PATH.read_text())

    after = int(time.time() - LOOKBACK_DAYS * 86400)
    print(f"Fetching activities since {time.ctime(after)}...")
    acts = recent_activities(after_epoch=after)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(acts, indent=2))
    print(f"Saved {len(acts)} activities to {DATA_PATH}.")
    return acts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    fetch(force=args.force)
