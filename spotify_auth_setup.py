"""
One-time interactive setup: authorizes this app with your Spotify account
via the browser and prints a refresh token.

Run this again any time the refresh token needs to be regenerated (lost it,
it was revoked, or the requested scopes changed) -- it doesn't matter that a
previous refresh token exists, this always mints a fresh one.

Usage:
    export SPOTIPY_CLIENT_ID="..."       # from the Spotify Developer Dashboard app
    export SPOTIPY_CLIENT_SECRET="..."
    python spotify_auth_setup.py

Then copy the printed refresh token into SPOTIPY_REFRESH_TOKEN -- both for
local runs and as the GitHub Actions repo secret (Settings > Secrets and
variables > Actions > SPOTIPY_REFRESH_TOKEN), since the scheduled job reads
it from there, not from anything printed here.

Note: the app's redirect URI (below) must be listed under "Redirect URIs" in
the app's settings on the Spotify Developer Dashboard, or the browser step
will fail with an "INVALID_CLIENT: Invalid redirect URI" error.
"""

import os
import sys

from spotipy.oauth2 import SpotifyOAuth

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative"


def main():
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

    if not client_id or not client_secret:
        sys.exit(
            "Missing SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET.\n"
            "Set them first (from the app's page on the Spotify Developer "
            "Dashboard) and re-run."
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        open_browser=True,
        cache_path=None,  # don't cache to disk -- we just want it printed once
    )

    # Opens your browser to Spotify's consent screen and starts a tiny local
    # server on 127.0.0.1:8888 to catch the redirect automatically -- log in
    # and click "Agree", then come back to this terminal.
    token_info = auth_manager.get_access_token(as_dict=True)

    print("\nSuccess. Set this as SPOTIPY_REFRESH_TOKEN -- treat it like a password:\n")
    print(token_info["refresh_token"])
    print(
        "\nRemember to update it in two places: your local terminal env var, "
        "and the SPOTIPY_REFRESH_TOKEN secret in the GitHub repo (so the "
        "nightly scheduled run keeps working too)."
    )


if __name__ == "__main__":
    main()
