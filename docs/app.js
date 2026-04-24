// QOM Hunter frontend. Loads segments.json, handles filters, renders map.

const state = {
  segments: [],
  sport: "Ride",
  record: "qom",
  center: null,        // [lat, lng], set after geocode
  centerMarker: null,
  radiusCircle: null,
  segLayers: [],
};

const map = L.map("map").setView([40.7128, -74.0060], 11);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "&copy; OpenStreetMap",
}).addTo(map);

// --- load dataset ---
fetch("segments.json")
  .then(r => r.json())
  .then(data => {
    state.segments = data.segments;
    const built = new Date(data.built_at * 1000).toLocaleDateString();
    document.getElementById("results-count").textContent =
      `Loaded ${data.segments.length} segments (built ${built}).`;
  })
  .catch(err => {
    document.getElementById("results-count").textContent =
      "Failed to load dataset. If this is a fresh deploy, run the build step first.";
    console.error(err);
  });

// --- range displays ---
const $ = id => document.getElementById(id);
function bindRange(inputId, valId, fmt = v => v) {
  const input = $(inputId), val = $(valId);
  const update = () => { val.textContent = fmt(input.value); rerender(); };
  input.addEventListener("input", update);
  update();
}
bindRange("radius", "radius-val");
bindRange("dist-min", "dist-min-val");
bindRange("dist-max", "dist-max-val");
bindRange("max-speed", "max-speed-val");
bindRange("min-pace", "min-pace-val", v => (+v).toFixed(1));

// --- sport toggle ---
document.querySelectorAll(".sport-btn").forEach(b => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".sport-btn").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    state.sport = b.dataset.sport;
    // runs show pace filter, rides show speed filter
    $("speed-field").style.display = state.sport === "Ride" ? "" : "none";
    $("pace-field").style.display = state.sport === "Run" ? "" : "none";
    rerender();
  });
});

// --- record toggle ---
document.querySelectorAll(".record-btn").forEach(b => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".record-btn").forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    state.record = b.dataset.record;
    rerender();
  });
});

// --- geocode using Nominatim (OSM). Free, no key, but rate-limited; for a
// shared app we'd want a proper provider. For casual use it's fine. ---
$("geocode-btn").addEventListener("click", doGeocode);
$("location").addEventListener("keydown", e => { if (e.key === "Enter") doGeocode(); });

async function doGeocode() {
  const q = $("location").value.trim();
  if (!q) return;
  $("geocode-status").textContent = "Searching...";
  try {
    // bias search to the NYC area with a viewbox
    const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&viewbox=-74.27,40.95,-73.70,40.50&bounded=1&q=${encodeURIComponent(q)}`;
    const r = await fetch(url, { headers: { "Accept-Language": "en" } });
    const hits = await r.json();
    if (!hits.length) {
      $("geocode-status").textContent = "No match. Try a more specific place name.";
      return;
    }
    const hit = hits[0];
    state.center = [parseFloat(hit.lat), parseFloat(hit.lon)];
    $("geocode-status").textContent = hit.display_name;
    map.setView(state.center, 14);
    rerender();
  } catch (e) {
    $("geocode-status").textContent = "Geocoding failed. Try again.";
  }
}

// --- haversine distance in km between two [lat,lng] points ---
function haversineKm(a, b) {
  const R = 6371;
  const [lat1, lon1] = a, [lat2, lon2] = b;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const s = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// --- polyline decoder (Google algorithm) ---
function decodePolyline(str) {
  let index = 0, lat = 0, lng = 0, coords = [];
  while (index < str.length) {
    for (let k = 0; k < 2; k++) {
      let shift = 0, result = 0, b;
      do {
        b = str.charCodeAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
      if (k === 0) lat += delta; else lng += delta;
    }
    coords.push([lat / 1e5, lng / 1e5]);
  }
  return coords;
}

// --- main filter + render ---
function rerender() {
  // clear old layers
  state.segLayers.forEach(l => map.removeLayer(l));
  state.segLayers = [];
  if (state.radiusCircle) map.removeLayer(state.radiusCircle);
  if (state.centerMarker) map.removeLayer(state.centerMarker);

  if (!state.center || !state.segments.length) {
    $("results").innerHTML = "";
    return;
  }

  const radius = parseFloat($("radius").value);
  const distMin = parseFloat($("dist-min").value);
  const distMax = parseFloat($("dist-max").value);
  const maxKph = parseFloat($("max-speed").value);
  const minPacePerKm = parseFloat($("min-pace").value);

  // draw center + radius
  state.centerMarker = L.marker(state.center).addTo(map);
  state.radiusCircle = L.circle(state.center, {
    radius: radius * 1000,
    color: "#fc4c02", weight: 1, fillOpacity: 0.05
  }).addTo(map);

  const recordKey = state.record + "_s";
  const speedKey = state.record + "_kph";
  const paceKey = state.record + "_min_per_km";

  const matches = state.segments.filter(s => {
    if (s.type !== state.sport) return false;
    if (!s[recordKey]) return false;  // segment has no record time
    if (!s.start) return false;
    const d = haversineKm(state.center, s.start);
    if (d > radius) return false;
    const distKm = s.dist_m / 1000;
    if (distKm < distMin || distKm > distMax) return false;
    if (state.sport === "Ride") {
      if (s[speedKey] > maxKph) return false;
    } else {
      if (s[paceKey] < minPacePerKm) return false;
    }
    return true;
  });

  // sort: for rides, slowest-first (softest speed); for runs, slowest pace
  matches.sort((a, b) => {
    if (state.sport === "Ride") return a[speedKey] - b[speedKey];
    return b[paceKey] - a[paceKey];
  });

  $("results-count").textContent = `${matches.length} segments match.`;

  // render map layers
  for (const s of matches) {
    if (!s.poly) continue;
    let coords;
    try { coords = decodePolyline(s.poly); } catch { continue; }
    const line = L.polyline(coords, { color: "#fc4c02", weight: 3, opacity: 0.8 });
    const popup = renderPopup(s);
    line.bindPopup(popup);
    line.addTo(map);
    state.segLayers.push(line);
  }

  // render result list (top 50)
  const list = $("results");
  list.innerHTML = "";
  for (const s of matches.slice(0, 50)) {
    const div = document.createElement("div");
    div.className = "result";
    const recStr = state.record === "qom" ? s.qom_str : s.kom_str;
    const distKm = (s.dist_m / 1000).toFixed(2);
    let rate;
    if (state.sport === "Ride") {
      rate = `${s[speedKey].toFixed(1)} km/h`;
    } else {
      const mins = Math.floor(s[paceKey]);
      const secs = Math.round((s[paceKey] - mins) * 60);
      rate = `${mins}:${secs.toString().padStart(2, "0")}/km`;
    }
    div.innerHTML = `
      <div class="name">${s.name || "(unnamed)"}</div>
      <div class="meta">${distKm} km &middot; ${(s.grade || 0).toFixed(1)}% &middot; ${state.record.toUpperCase()} ${recStr} &middot; ${rate}</div>
    `;
    div.addEventListener("click", () => {
      if (s.start) map.setView(s.start, 16);
    });
    list.appendChild(div);
  }
}

function renderPopup(s) {
  const recStr = state.record === "qom" ? s.qom_str : s.kom_str;
  const distKm = (s.dist_m / 1000).toFixed(2);
  let rate;
  if (state.sport === "Ride") {
    rate = `${s[state.record + "_kph"].toFixed(1)} km/h`;
  } else {
    const p = s[state.record + "_min_per_km"];
    const mins = Math.floor(p);
    const secs = Math.round((p - mins) * 60);
    rate = `${mins}:${secs.toString().padStart(2, "0")} min/km`;
  }
  return `
    <b>${s.name || "(unnamed)"}</b><br>
    ${s.type} &middot; ${distKm} km &middot; ${(s.grade || 0).toFixed(1)}% grade<br>
    ${s.type === "Ride" ? "QOM" : "QOM"}: ${recStr} (${rate})<br>
    ${s.effort_count || 0} efforts by ${s.athlete_count || 0} athletes<br>
    <a href="https://www.strava.com/segments/${s.id}" target="_blank">Open on Strava</a>
  `;
}
