"""
OAuth flow for Strava. Run once interactively to get initial tokens.
Subsequent runs use the refresh token automatically.

Strava OAuth notes: the authorize URL opens in a browser, user consents, and
Strava redirects to our local callback with a short-lived code. We exchange
that code for an access token plus a refresh token. Access tokens last six
hours, refresh tokens are long-lived. The refresh token is what we persist.

Required scopes: read, activity:read_all, profile:read_all. The activity
scope is needed to read your own segment efforts and recent activities.
"""

import http.server
import json
import os
import socketserver
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")
TOKEN_PATH = Path(__file__).parent.parent / "data" / "tokens.json"
REDIRECT_URI = "http://localhost:8721/callback"
SCOPES = "read,activity:read_all,profile:read_all"

_captured_code = {"code": None}


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            _captured_code["code"] = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>Strava authorization received.</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args, **kwargs):
        pass  # quiet


def _run_local_server():
    """Serve one request on localhost:8721 to catch the OAuth callback."""
    with socketserver.TCPServer(("localhost", 8721), _CallbackHandler) as httpd:
        # handle a single request then return
        httpd.handle_request()


def initial_auth():
    """Interactive one-time flow: open browser, catch code, exchange for tokens."""
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env first."
        )

    server_thread = threading.Thread(target=_run_local_server, daemon=True)
    server_thread.start()

    authorize_url = (
        "https://www.strava.com/oauth/authorize?"
        + urllib.parse.urlencode(
            {
                "client_id": CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "approval_prompt": "auto",
                "scope": SCOPES,
            }
        )
    )
    print("Opening browser for Strava authorization...")
    print(f"If it does not open, paste this URL into your browser:\n{authorize_url}")
    webbrowser.open(authorize_url)

    # wait up to two minutes for the user to complete consent
    deadline = time.time() + 120
    while _captured_code["code"] is None and time.time() < deadline:
        time.sleep(0.5)

    code = _captured_code["code"]
    if not code:
        raise RuntimeError("Did not receive an authorization code in time.")

    r = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _save_tokens(data)
    print(f"Saved tokens for athlete {data.get('athlete', {}).get('id')}.")
    return data


def _save_tokens(data: dict):
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
        "athlete_id": data.get("athlete", {}).get("id")
        or _load_tokens_if_exists().get("athlete_id"),
    }
    TOKEN_PATH.write_text(json.dumps(payload, indent=2))


def _load_tokens_if_exists() -> dict:
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text())
    return {}


def get_access_token() -> str:
    """
    Return a live access token, refreshing if the current one is near expiry.
    Callers elsewhere in the app should only use this, never the raw file.
    """
    tokens = _load_tokens_if_exists()
    if not tokens:
        raise RuntimeError(
            "No tokens on disk. Run `python -m src.auth` once interactively first."
        )

    # refresh if expired or within sixty seconds of expiry
    if tokens["expires_at"] - 60 <= time.time():
        r = requests.post(
            "https://www.strava.com/oauth/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
            },
            timeout=30,
        )
        r.raise_for_status()
        refreshed = r.json()
        # preserve athlete_id across refreshes (not returned by the refresh call)
        refreshed.setdefault("athlete", {})["id"] = tokens.get("athlete_id")
        _save_tokens(refreshed)
        tokens = _load_tokens_if_exists()

    return tokens["access_token"]


def get_athlete_id() -> int:
    return _load_tokens_if_exists().get("athlete_id")


if __name__ == "__main__":
    initial_auth()
