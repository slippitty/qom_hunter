"""
Render data/scored.json as a self-contained map.html using Leaflet.

Each segment is drawn as a polyline colored by score (higher = greener),
with a popup showing record, your estimate, and the gap.

Opens in any browser, no server needed.
"""

import html
import json
from pathlib import Path

SCORED_PATH = Path(__file__).parent.parent / "data" / "scored.json"
MAP_PATH = Path(__file__).parent.parent / "data" / "map.html"


def _decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Google encoded polyline decoder. Returns list of (lat, lng)."""
    points: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lng = 0
    while index < len(encoded):
        for coord_idx in range(2):
            shift = 0
            result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if coord_idx == 0:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def _color_for_score(score: float) -> str:
    # 80 -> red, 100 -> yellow, 120+ -> green; clamped
    s = max(80.0, min(120.0, score))
    if s <= 100:
        t = (s - 80) / 20.0
        r = 255
        g = int(255 * t)
        b = 0
    else:
        t = (s - 100) / 20.0
        r = int(255 * (1 - t))
        g = 255
        b = 0
    return f"#{r:02x}{g:02x}{b:02x}"


def render():
    if not SCORED_PATH.exists():
        raise SystemExit("Run `python -m src.score` first to produce scored.json.")
    scored = json.loads(SCORED_PATH.read_text())

    features = []
    for s in scored:
        poly = s.get("map_polyline")
        if not poly:
            continue
        try:
            coords = _decode_polyline(poly)
        except Exception:
            continue
        popup_lines = [
            f"<b>{html.escape(s['name'])}</b>",
            f"{s['activity_type']} &middot; {s['distance_m']:.0f} m &middot; "
            f"{s.get('avg_grade') or 0:+.1f}% grade",
            f"{s['record_holder']}: {s['record_time_str']}",
        ]
        if s["activity_type"] == "Ride":
            popup_lines.append(
                f"Their est. {s['their_est_watts']:.0f} W &middot; "
                f"your est. {s['your_est_watts']:.0f} W"
            )
        else:
            popup_lines.append(
                f"Their GAP {s['their_gap_s_per_km']:.0f} s/km &middot; "
                f"your GAP {s['your_gap_s_per_km']:.0f} s/km"
            )
        popup_lines.append(f"Score: <b>{s['score']:.1f}</b>")
        popup_lines.append(
            f'<a href="https://www.strava.com/segments/{s["id"]}" target="_blank">Open on Strava</a>'
        )
        features.append(
            {
                "coords": coords,
                "color": _color_for_score(s["score"]),
                "popup": "<br>".join(popup_lines),
                "score": s["score"],
            }
        )

    js_features = json.dumps(features)
    html_doc = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Brooklyn QOM Hunter</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body, #map {{ height: 100%; margin: 0; }}
  .legend {{ background: white; padding: 8px; font: 12px sans-serif; }}
</style>
</head>
<body>
<div id="map"></div>
<script>
const features = {js_features};
const map = L.map('map').setView([40.6782, -73.9442], 12);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 19, attribution: '&copy; OpenStreetMap'
}}).addTo(map);
for (const f of features) {{
  const weight = 2 + Math.max(0, (f.score - 90)) / 10;
  L.polyline(f.coords, {{color: f.color, weight: weight, opacity: 0.8}})
    .bindPopup(f.popup).addTo(map);
}}
const legend = L.control({{position: 'bottomright'}});
legend.onAdd = function() {{
  const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = '<b>Score</b><br>'
    + '<span style="color:#ff0000">&#x25A0;</span> 80 (out of reach)<br>'
    + '<span style="color:#ffff00">&#x25A0;</span> 100 (line-ball)<br>'
    + '<span style="color:#00ff00">&#x25A0;</span> 120+ (beatable)';
  return d;
}};
legend.addTo(map);
</script>
</body>
</html>
"""
    MAP_PATH.write_text(html_doc)
    print(f"Wrote {MAP_PATH}. Open it in a browser.")


if __name__ == "__main__":
    render()
