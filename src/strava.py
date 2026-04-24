"""
Minimal Strava API client.

We care about four endpoints:
  GET /segments/explore           -> discover segments in a bounding box
  GET /segments/{id}              -> detail including course record (KOM/QOM)
  GET /segments/{id}/all_efforts  -> the authenticated athlete's efforts on that segment
  GET /athlete/activities         -> recent activities for pace/power calibration

The leaderboard endpoint is deliberately not here; standard app-level access
to it was revoked and returns 403 as of April 2026.

Rate limits: 100 requests per 15 minutes, 1000 per day on the standard tier.
Strava returns these in response headers, and we back off on 429.
"""

import time

import requests

from .auth import get_access_token

BASE = "https://www.strava.com/api/v3"


class RateLimited(Exception):
    pass


def _request(method: str, path: str, **kwargs) -> dict | list:
    url = f"{BASE}{path}"
    token = get_access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"

    for attempt in range(3):
        r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if r.status_code == 429:
            # short-term limit hit; wait for the 15-minute window to reset
            # in practice we sleep sixty seconds and retry, up to three attempts
            time.sleep(60)
            continue
        if r.status_code == 401:
            # token might have just expired between get_access_token and the call
            headers["Authorization"] = f"Bearer {get_access_token()}"
            continue
        r.raise_for_status()
        return r.json()
    raise RateLimited(f"Gave up after repeated 429s on {path}")


def explore_segments(
    sw_lat: float, sw_lng: float, ne_lat: float, ne_lng: float, activity_type: str
) -> list[dict]:
    """
    activity_type is 'riding' or 'running'. Returns up to ten segments in the box.
    Each segment has id, name, climb_category, distance, avg_grade, elev_difference,
    start_latlng, end_latlng, points (polyline).
    """
    params = {
        "bounds": f"{sw_lat},{sw_lng},{ne_lat},{ne_lng}",
        "activity_type": activity_type,
    }
    data = _request("GET", "/segments/explore", params=params)
    return data.get("segments", [])


def get_segment(segment_id: int) -> dict:
    """
    Detailed segment. Includes xoms (course record times), total_elevation_gain,
    average_grade, distance, activity_type, effort_count, athlete_count, map.
    The course record time lives at data['xoms']['qom'] / ['kom'] as a string like
    "4:32" or "1:02:45"; we parse it elsewhere.
    """
    return _request("GET", f"/segments/{segment_id}")


def get_my_segment_efforts(segment_id: int, per_page: int = 50) -> list[dict]:
    """
    All of the authenticated athlete's efforts on this segment.
    Each effort has elapsed_time, moving_time, start_date, average_watts (if available),
    average_heartrate, distance.
    """
    efforts: list[dict] = []
    page = 1
    while True:
        batch = _request(
            "GET",
            f"/segments/{segment_id}/all_efforts",
            params={"per_page": per_page, "page": page},
        )
        if not batch:
            break
        efforts.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return efforts


def recent_activities(after_epoch: int | None = None, per_page: int = 100) -> list[dict]:
    """
    Recent activities for the authenticated athlete, for power/pace calibration.
    Pass after_epoch to limit to the last N days.
    """
    activities: list[dict] = []
    page = 1
    params = {"per_page": per_page}
    if after_epoch:
        params["after"] = after_epoch
    while True:
        batch = _request(
            "GET", "/athlete/activities", params={**params, "page": page}
        )
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return activities
