# Brooklyn QOM Hunter

A tool for finding Strava segments in Brooklyn where the current QOM/KOM looks beatable given your recent performance.

## What it does

1. Tiles Brooklyn into a grid and calls the Strava `segments/explore` endpoint for each tile, once for rides and once for runs. Caches the resulting segment list.
2. For each segment, pulls the segment detail (distance, grade, elevation, course record time) and your personal effort history.
3. Scores each segment by how close your best realistic effort is to the course record, using a power-based model for rides and a grade-adjusted pace model for runs.
4. Outputs a ranked list of targets and a Leaflet map you can open in a browser.

## What it does not do

It does not use the `segments/{id}/leaderboard` endpoint, because as of April 2026 that endpoint returns 403 for standard Strava API applications regardless of your personal subscription level. The course record time comes from the segment detail endpoint instead, which is enough for this use case.

## Setup

Create a Strava API application at https://www.strava.com/settings/api. Set the authorization callback domain to `localhost`. Copy the client ID and client secret into a `.env` file based on `.env.example`.

Then:

```
pip install -r requirements.txt
python -m src.auth
```

The auth script opens a browser, walks you through the OAuth consent, and writes tokens to `data/tokens.json`. After that:

```
python -m src.discover   # find segments in Brooklyn
python -m src.score      # rank them against your efforts
python -m src.map        # write a map.html you can open
```

Each script caches its output so you are not hammering the API on every run. Rate limits are 100 requests per 15 minutes and 1000 per day on the standard tier.

## Scoring

For rides, the script estimates the power required to match the course record using the Martin et al. cycling power model (rolling resistance, air drag, gravity, acceleration terms), given segment distance, elevation gain, and a reasonable set of physical constants. It then estimates your sustainable power at that duration using a critical power fit to your recent activities, and expresses the gap as a percentage.

For runs, it uses grade-adjusted pace. The course record pace is adjusted for the segment grade, and your recent runs are used to estimate your own grade-adjusted pace at that duration. Again the gap is expressed as a percentage.

Segments where your estimated capability is within a few percent of the record are flagged as realistic targets. Obvious sandbag segments (very short, very flat, very fast) tend to surface too and are worth a look because urban QOMs on quiet segments can be genuinely soft.

## Files

```
src/
  auth.py          OAuth flow and token refresh
  strava.py        Thin API client with rate limit handling
  discover.py      Tile Brooklyn, call explore_segments, cache results
  efforts.py       Pull your effort history and recent activities
  power.py         Cycling power model
  pace.py          Grade-adjusted pace model
  score.py         Combine the above into a ranking for you personally
  map.py           Render map.html with Leaflet
  build_dataset.py Build the shared public dataset for the web site
docs/
  index.html       Public site (served by GitHub Pages)
  app.js
  style.css
  segments.json    (created by build_dataset, committed to repo)
data/
  tokens.json      (created on first run, gitignored)
  segments.json    (created by discover, cached, gitignored)
  efforts.json     (created by efforts, cached, gitignored)
  scored.json      (created by score, gitignored)
  map.html         (created by map, gitignored)
```

## Public web site

To publish a public QOM Hunter site via GitHub Pages:

1. Run `python -m src.build_dataset`. This scans the NYC region, fetches segment detail for each, and writes `docs/segments.json`. It is resumable; hitting Ctrl-C or the daily rate limit is safe, just re-run to continue. Expect 90 minutes to several hours depending on segment density.

2. Commit and push `docs/segments.json` along with the rest of the `docs/` folder.

3. On GitHub, go to Settings, Pages, and under "Build and deployment" set Source to "Deploy from a branch", Branch to "main", and folder to "/docs". Save. GitHub will publish the site at `https://YOUR_USERNAME.github.io/qom_hunter/` within a minute.

The site loads segments.json client-side and filters entirely in the browser — no backend, no API keys exposed. To refresh the dataset, re-run `build_dataset` and push.
