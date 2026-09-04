"""
SpotDriver -- main entrypoint (Phase 5 orchestration + Phase 6 robustness).

For every mapping listed in config.yaml, reads that mapping's fixed source
playlists, picks a random selection, and resets its target playlist with it.
This is the script Phase 7's scheduled job will call.

Safety: DRY RUN by default -- prints/logs what each mapping would do, writes
nothing. Pass --live to actually apply the resets.

Robustness:
    - Each mapping is processed independently: a failure on one target
      logs an error and moves on to the next, instead of aborting the run.
    - If any mapping failed, the process exits with a non-zero status at
      the end -- in GitHub Actions that marks the run as failed, which
      triggers GitHub's default failure-notification email automatically.
    - spotipy retries transient/rate-limited (429) API errors internally.

Usage:
    export SPOTIPY_CLIENT_ID="..."
    export SPOTIPY_CLIENT_SECRET="..."
    export SPOTIPY_REFRESH_TOKEN="..."
    python main.py            # dry run (safe, no writes)
    python main.py --live     # actually reset every target playlist
"""

import logging
import random
import sys

from read_sources import fetch_all_tracks, get_spotify_client, load_config
from selection import select_tracks

DEFAULT_TRACK_COUNT = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("spotdriver")


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def split_source_specs(source_playlists):
    """A source_playlists entry is either:
    - a plain playlist ID string -- pooled together with any other plain
      entries, and `track_count` total is drawn at random from that pool.
    - a {id, count} mapping -- exactly `count` tracks are drawn from that
      one source alone, independent of everything else.
    Returns (fixed_specs, pooled_ids): fixed_specs is a list of
    (playlist_id, count) pairs, pooled_ids is a list of plain playlist IDs.
    """
    fixed_specs = []
    pooled_ids = []
    for entry in source_playlists:
        if isinstance(entry, dict):
            if "id" not in entry or "count" not in entry:
                raise ValueError(f"source entry needs both 'id' and 'count': {entry!r}")
            fixed_specs.append((entry["id"], entry["count"]))
        else:
            pooled_ids.append(entry)
    return fixed_specs, pooled_ids


def process_mapping(sp, mapping, live):
    """Process one target/sources mapping. Raises on failure -- the caller
    decides whether to let one failure stop the whole run."""
    target_id = mapping["target_playlist"]
    source_playlists = mapping["source_playlists"]
    default_count = mapping.get("track_count", DEFAULT_TRACK_COUNT)

    fixed_specs, pooled_ids = split_source_specs(source_playlists)

    picked = []
    total_pool_size = 0

    # Fixed-count sources: draw exactly `count` tracks from each, independently
    # of every other source.
    for playlist_id, count in fixed_specs:
        tracks, total_reported, skipped = fetch_all_tracks(sp, playlist_id)
        log.info(
            "[%s] source %s: %d usable tracks (API total %s, %d skipped) -- drawing %d",
            target_id, playlist_id, len(tracks), total_reported, skipped, count,
        )
        total_pool_size += len(tracks)
        picked.extend(select_tracks(tracks, count))

    # Pooled sources (plain ID strings, if any): combine and draw
    # `default_count` (mapping-level track_count) total from the combined pool.
    if pooled_ids:
        pool = []
        for playlist_id in pooled_ids:
            tracks, total_reported, skipped = fetch_all_tracks(sp, playlist_id)
            log.info(
                "[%s] source %s: %d usable tracks (API total %s, %d skipped)",
                target_id, playlist_id, len(tracks), total_reported, skipped,
            )
            pool.extend(tracks)
        total_pool_size += len(pool)
        picked.extend(select_tracks(pool, default_count))

    if not picked:
        raise RuntimeError(f"[{target_id}] no usable tracks found across sources {source_playlists}")

    # select_tracks() already dedupes and rng.sample() already returns a
    # random order, but shuffle explicitly here too -- once the selection is
    # complete, the final playlist order shouldn't silently depend on
    # whatever order the sources happened to be read in.
    random.shuffle(picked)

    # Defensive final dedup right before writing: if this ever fires, it
    # means a duplicate slipped past select_tracks() (e.g. a future change
    # to the selection logic), so catch it here rather than write dupes.
    uris = []
    seen_uris = set()
    for t in picked:
        if t["uri"] in seen_uris:
            log.warning("[%s] duplicate URI in final selection, dropping: %s", target_id, t["uri"])
            continue
        seen_uris.add(t["uri"])
        uris.append(t["uri"])

    log.info(
        "[%s] selected %d tracks from %d source(s) (%d fixed-count, %d pooled; %d tracks read total)",
        target_id, len(uris), len(fixed_specs) + len(pooled_ids), len(fixed_specs), len(pooled_ids), total_pool_size,
    )

    if not live:
        for t in picked:
            log.info("    - %s — %s", t["title"], t["artists"])
        return

    # "Replace Playlist Items" takes at most 100 URIs per call and clears the
    # playlist first; anything beyond the first 100 is appended after.
    chunks = list(chunked(uris, 100))
    sp.playlist_replace_items(target_id, chunks[0] if chunks else [])
    for chunk in chunks[1:]:
        sp.playlist_add_items(target_id, chunk)

    log.info("[%s] done -- now has %d tracks.", target_id, len(uris))


def main():
    live = "--live" in sys.argv

    config = load_config()
    mappings = config["playlists"]

    try:
        sp = get_spotify_client()
    except Exception:
        log.exception("Failed to authenticate with Spotify -- aborting run.")
        sys.exit(1)

    log.info("%s -- processing %d playlist mapping(s)", "LIVE" if live else "DRY RUN", len(mappings))

    failures = []
    for mapping in mappings:
        target_id = mapping.get("target_playlist", "?")
        try:
            process_mapping(sp, mapping, live)
        except Exception:
            log.exception("[%s] failed -- continuing with remaining mappings.", target_id)
            failures.append(target_id)

    if not live:
        log.info("Dry run only -- nothing written. Re-run with --live to apply.")

    if failures:
        log.error("Run finished with %d failed mapping(s): %s", len(failures), failures)
        sys.exit(1)

    log.info("Run finished successfully.")


if __name__ == "__main__":
    main()
