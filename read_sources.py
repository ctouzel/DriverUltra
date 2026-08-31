"""
Phase 2: read every track from the fixed source playlists (config.yaml) and
print a summary. This does not touch the target playlist yet -- read-only.

Usage:
    pip install -r requirements.txt
    export SPOTIPY_CLIENT_ID="..."
    export SPOTIPY_CLIENT_SECRET="..."
    export SPOTIPY_REFRESH_TOKEN="..."   # printed by spotify_auth_setup.py
    python read_sources.py
"""

import os
import sys

import spotipy
import yaml
from spotipy.oauth2 import SpotifyOAuth

CONFIG_PATH = "config.yaml"
REDIRECT_URI = "http://127.0.0.1:8888/callback"


def get_spotify_client():
    """Authenticate using a stored refresh token -- no browser needed."""
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    refresh_token = os.environ.get("SPOTIPY_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        sys.exit(
            "Missing one of SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET / "
            "SPOTIPY_REFRESH_TOKEN.\nSet them (the refresh token comes from "
            "spotify_auth_setup.py) and re-run."
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
    )
    token_info = auth_manager.refresh_access_token(refresh_token)
    return spotipy.Spotify(auth=token_info["access_token"])


def load_config(path=CONFIG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch_all_tracks(sp, playlist_id):
    """Return (tracks, total_reported) for a playlist: tracks is a flat list of
    {uri, title, artists}, following pagination. Skips local files and any item
    whose track is null (removed track / market-restricted). total_reported is
    the item count the API itself claims the playlist has, for diagnostics --
    if it's >0 but tracks ends up empty, every item came back null."""
    tracks = []
    # No `fields` filter here -- keep the full response so a bad field-filter
    # string can't silently zero out the results. market="from_token" works
    # around a known Spotify API issue where newer apps get track:null for
    # every item unless a market is explicitly resolved.
    results = sp.playlist_items(
        playlist_id, additional_types=["track"], market="from_token"
    )
    total_reported = results.get("total")
    skipped_null = 0
    while results:
        for item in results.get("items", []):
            # This Spotify API surface nests the track/episode payload under
            # "item" (newer response shape); older docs/examples show "track".
            # Support both so we don't silently break again if it flips back.
            media = item.get("item") or item.get("track")
            is_local = item.get("is_local") or (media and media.get("is_local"))
            if not media or is_local or media.get("type") != "track":
                skipped_null += 1
                continue
            tracks.append(
                {
                    "uri": media["uri"],
                    "title": media["name"],
                    "artists": ", ".join(a["name"] for a in media.get("artists", [])),
                }
            )
        results = sp.next(results) if results.get("next") else None
    return tracks, total_reported, skipped_null


def main():
    config = load_config()
    sp = get_spotify_client()

    all_tracks = []
    for playlist_id in config["source_playlists"]:
        tracks, total_reported, skipped_null = fetch_all_tracks(sp, playlist_id)
        print(
            f"Source {playlist_id}: {len(tracks)} usable tracks "
            f"(API reports {total_reported} items total, {skipped_null} skipped as null/local)"
        )
        all_tracks.extend(tracks)

    unique_uris = {t["uri"] for t in all_tracks}
    print(f"\nTotal tracks pooled from all sources: {len(all_tracks)}")
    print(f"Unique tracks (by URI): {len(unique_uris)}")

    print("\nSample:")
    for t in all_tracks[:5]:
        print(f"  - {t['title']} — {t['artists']}")


if __name__ == "__main__":
    main()
