"""
Phase 3 preview: read the real source playlists, run the random selection,
and print what would be picked -- still read-only, nothing is written to
the target playlist yet.

Usage:
    export SPOTIPY_CLIENT_ID="..."
    export SPOTIPY_CLIENT_SECRET="..."
    export SPOTIPY_REFRESH_TOKEN="..."
    python select_preview.py
"""

from read_sources import fetch_all_tracks, get_spotify_client, load_config
from selection import select_tracks


def main():
    config = load_config()
    sp = get_spotify_client()

    all_tracks = []
    for playlist_id in config["source_playlists"]:
        tracks, total_reported, skipped = fetch_all_tracks(sp, playlist_id)
        print(f"Source {playlist_id}: {len(tracks)} usable tracks")
        all_tracks.extend(tracks)

    count = config.get("selection", {}).get("track_count", 25)
    picked = select_tracks(all_tracks, count)

    print(f"\nPicked {len(picked)} of {count} requested (pool had {len(all_tracks)} tracks, "
          f"{len({t['uri'] for t in all_tracks})} unique):\n")
    for t in picked:
        print(f"  - {t['title']} — {t['artists']}")


if __name__ == "__main__":
    main()
