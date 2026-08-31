"""
Phase 4: read sources -> select -> reset the target playlist.

Safety: by default this is a DRY RUN -- it prints what it would do and
writes nothing. Pass --live to actually replace the target playlist's
contents.

Usage:
    export SPOTIPY_CLIENT_ID="..."
    export SPOTIPY_CLIENT_SECRET="..."
    export SPOTIPY_REFRESH_TOKEN="..."
    python reset_target.py            # dry run (safe, no writes)
    python reset_target.py --live     # actually resets the target playlist
"""

import sys

from read_sources import fetch_all_tracks, get_spotify_client, load_config
from selection import select_tracks


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def main():
    live = "--live" in sys.argv

    config = load_config()
    sp = get_spotify_client()

    all_tracks = []
    for playlist_id in config["source_playlists"]:
        tracks, _, _ = fetch_all_tracks(sp, playlist_id)
        all_tracks.extend(tracks)

    count = config.get("selection", {}).get("track_count", 25)
    picked = select_tracks(all_tracks, count)
    uris = [t["uri"] for t in picked]
    target_id = config["target_playlist"]

    print(f"Selected {len(uris)} tracks for target playlist {target_id}:")
    for t in picked:
        print(f"  - {t['title']} — {t['artists']}")

    if not live:
        print("\nDry run only -- nothing written. Re-run with --live to apply.")
        return

    # "Replace Playlist Items" takes at most 100 URIs per call and clears the
    # playlist first. Any tracks beyond the first 100 are appended after.
    chunks = list(chunked(uris, 100))
    sp.playlist_replace_items(target_id, chunks[0] if chunks else [])
    for chunk in chunks[1:]:
        sp.playlist_add_items(target_id, chunk)

    print(f"\nDone -- target playlist now has {len(uris)} tracks.")


if __name__ == "__main__":
    main()
