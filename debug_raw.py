"""
Debug helper: dumps the raw API response for the first couple of items of a
playlist, so we can see exactly what Spotify is sending back.

Usage:
    python debug_raw.py <playlist_id>
"""

import json
import os
import sys

import spotipy
from spotipy.oauth2 import SpotifyOAuth

REDIRECT_URI = "http://127.0.0.1:8888/callback"


def get_spotify_client():
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    refresh_token = os.environ.get("SPOTIPY_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        sys.exit("Missing SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / SPOTIPY_REFRESH_TOKEN.")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
    )
    token_info = auth_manager.refresh_access_token(refresh_token)
    return spotipy.Spotify(auth=token_info["access_token"])


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python debug_raw.py <playlist_id>")

    playlist_id = sys.argv[1]
    sp = get_spotify_client()

    results = sp.playlist_items(playlist_id, limit=2, market="from_token")
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
