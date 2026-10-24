"""
SpotDriver -- main entrypoint (Phase 5 orchestration + Phase 6 robustness +
Radio-type mappings).

For every mapping listed in config.yaml, reads that mapping's sources, picks
a random selection according to its type, and resets its target playlist
with it. This is the script Phase 7's scheduled job calls.

Two mapping types:
    - simple (default if "type" is omitted): one target, sources pooled
      and/or fixed-count -- see split_source_specs()/process_mapping().
    - radio: one target assembled from named roles (Top Songs in full, plus
      samples of Top Albums / Classics / Great Classics, plus any active
      seasonal "holiday" sources), with an aging Top-Songs-to-Classics
      promotion step -- see process_radio_mapping() and radio.py.

Update days (any mapping type): an optional `update_days` list restricts a
mapping to certain weekdays, e.g. `update_days: [mon, thu]`. On other days
the target playlist is left untouched. Omit it to update every day. The day
is judged on the UTC date, same as the holiday windows: the nightly run is
scheduled at 04:17 UTC, so the UTC date is always the morning the playlist
is meant for (in Toronto local time it can still be the previous evening in
winter). Pass --all-days to ignore update_days (handy for testing).

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
    python main.py --all-days # ignore update_days restrictions
"""

import logging
import random
import sys
from datetime import datetime, timezone

from radio import active_holidays, find_promotable, promote_aged_top_songs
from read_sources import fetch_all_tracks, get_spotify_client, load_config
from selection import select_tracks

DEFAULT_TRACK_COUNT = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("spotdriver")


WEEKDAY_NAMES = {
    # English and French, full or short -- all map to date.weekday() (Mon=0).
    "mon": 0, "monday": 0, "lun": 0, "lundi": 0,
    "tue": 1, "tuesday": 1, "mar": 1, "mardi": 1,
    "wed": 2, "wednesday": 2, "mer": 2, "mercredi": 2,
    "thu": 3, "thursday": 3, "jeu": 3, "jeudi": 3,
    "fri": 4, "friday": 4, "ven": 4, "vendredi": 4,
    "sat": 5, "saturday": 5, "sam": 5, "samedi": 5,
    "sun": 6, "sunday": 6, "dim": 6, "dimanche": 6,
}


def parse_update_days(raw):
    """Turn a mapping's `update_days` value into a set of weekday numbers
    (Mon=0 .. Sun=6), or None if the mapping updates every day."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    days = set()
    for name in raw:
        key = str(name).strip().lower()
        if key not in WEEKDAY_NAMES:
            raise ValueError(f"unknown day in update_days: {name!r}")
        days.add(WEEKDAY_NAMES[key])
    if not days:
        raise ValueError("update_days is empty -- remove it to update every day")
    return days


def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def finalize_and_write(sp, target_id, picked, live):
    """Shared tail end for every mapping type: shuffle, defensively dedup,
    then either log the dry-run preview or write it to the target playlist.
    `picked` is a list of track dicts (each with at least "uri", and
    "title"/"artists" for the dry-run preview)."""
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

    if not live:
        for t in picked:
            log.info("    - %s — %s", t.get("title", "?"), t.get("artists", "?"))
        log.info("[%s] would write %d tracks (dry run).", target_id, len(uris))
        return

    # "Replace Playlist Items" takes at most 100 URIs per call and clears the
    # playlist first; anything beyond the first 100 is appended after.
    chunks = list(chunked(uris, 100))
    sp.playlist_replace_items(target_id, chunks[0] if chunks else [])
    for chunk in chunks[1:]:
        sp.playlist_add_items(target_id, chunk)

    log.info("[%s] done -- now has %d tracks.", target_id, len(uris))


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
    """Process one target/sources mapping (SIMPLE type). Raises on failure --
    the caller decides whether to let one failure stop the whole run."""
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

    log.info(
        "[%s] selected %d track(s) from %d source(s) (%d fixed-count, %d pooled; %d tracks read total)",
        target_id, len(picked), len(fixed_specs) + len(pooled_ids), len(fixed_specs), len(pooled_ids), total_pool_size,
    )

    finalize_and_write(sp, target_id, picked, live)


def process_radio_mapping(sp, mapping, live):
    """Process one target/sources mapping (RADIO type). Raises on failure --
    the caller decides whether to let one failure stop the whole run.

    A radio mapping has named source roles rather than a flat source list:
    Top Songs is included in full; Top Albums / Classics / Great Classics
    each contribute a random sample; any "holiday" source whose date window
    covers today also contributes a sample. Before assembling the playlist,
    Top Songs tracks older than classics_promotion.after_weeks are copied
    into Classics (never removed from Top Songs, never duplicated)."""
    target_id = mapping["target_playlist"]
    sources = mapping["sources"]
    samples = mapping.get("samples", {})
    promotion_cfg = mapping.get("classics_promotion") or {}
    holidays_cfg = mapping.get("holidays") or []

    top_songs_id = sources["top_songs"]
    top_albums_id = sources["top_albums"]
    classics_id = sources["classics"]
    great_classics_id = sources["great_classics"]

    top_songs, ts_total, ts_skipped = fetch_all_tracks(sp, top_songs_id)
    log.info(
        "[%s] Top Songs %s: %d usable tracks (API total %s, %d skipped) -- all included",
        target_id, top_songs_id, len(top_songs), ts_total, ts_skipped,
    )

    classics, cl_total, cl_skipped = fetch_all_tracks(sp, classics_id)
    log.info(
        "[%s] Classics %s: %d usable tracks (API total %s, %d skipped) before promotion",
        target_id, classics_id, len(classics), cl_total, cl_skipped,
    )

    # Classics promotion: Top Songs tracks added more than `after_weeks` ago
    # get copied into Classics. Top Songs is never touched. In dry runs this
    # only previews the promotion (and simulates its effect on the Classics
    # sample pool below) -- nothing is written.
    after_weeks = promotion_cfg.get("after_weeks")
    if after_weeks is not None:
        if live:
            promoted = promote_aged_top_songs(
                sp, log, target_id, top_songs, classics_id, classics, after_weeks
            )
            if promoted:
                # Re-read so the freshly promoted tracks are eligible for
                # this run's Classics sample too.
                classics, _, _ = fetch_all_tracks(sp, classics_id)
        else:
            to_promote = find_promotable(top_songs, classics, after_weeks)
            if to_promote:
                log.info(
                    "[%s] would promote %d track(s) from Top Songs to Classics (>%d week(s) old)",
                    target_id, len(to_promote), after_weeks,
                )
                classics = classics + [
                    {"uri": uri, "title": "(promoted from Top Songs)", "artists": ""}
                    for uri in to_promote
                ]
            else:
                log.info("[%s] no Top Songs tracks old enough to promote to Classics", target_id)

    # Top Songs contributes in full (deduped, but not sampled/truncated).
    picked = list(select_tracks(top_songs, len(top_songs)))

    sampled_roles = (
        ("Top Albums", top_albums_id, samples.get("top_albums", 0), None),
        ("Classics", classics_id, samples.get("classics", 0), classics),
        ("Great Classics", great_classics_id, samples.get("great_classics", 0), None),
    )
    for label, playlist_id, count, cached_tracks in sampled_roles:
        if cached_tracks is not None:
            tracks = cached_tracks
        else:
            tracks, total_reported, skipped = fetch_all_tracks(sp, playlist_id)
            log.info(
                "[%s] %s %s: %d usable tracks (API total %s, %d skipped) -- drawing %d",
                target_id, label, playlist_id, len(tracks), total_reported, skipped, count,
            )
        picked.extend(select_tracks(tracks, count))

    # Holiday sources only contribute when today falls inside their window.
    today = datetime.now(timezone.utc).date()
    for holiday in active_holidays(holidays_cfg, today=today):
        tracks, total_reported, skipped = fetch_all_tracks(sp, holiday["source"])
        log.info(
            "[%s] holiday '%s' active: source %s: %d usable tracks (API total %s, %d skipped) -- drawing %d",
            target_id, holiday["name"], holiday["source"], len(tracks), total_reported, skipped, holiday["count"],
        )
        picked.extend(select_tracks(tracks, holiday["count"]))

    if not picked:
        raise RuntimeError(f"[{target_id}] no usable tracks found across any radio source")

    log.info("[%s] selected %d track(s) for radio playlist", target_id, len(picked))

    finalize_and_write(sp, target_id, picked, live)


def main():
    live = "--live" in sys.argv
    all_days = "--all-days" in sys.argv
    today = datetime.now(timezone.utc).date()

    config = load_config()
    mappings = config["playlists"]

    try:
        sp = get_spotify_client()
    except Exception:
        log.exception("Failed to authenticate with Spotify -- aborting run.")
        sys.exit(1)

    log.info("%s -- processing %d playlist mapping(s)", "LIVE" if live else "DRY RUN", len(mappings))

    failures = []
    skipped = []
    for mapping in mappings:
        target_id = mapping.get("target_playlist", "?")
        mapping_type = mapping.get("type", "simple")
        try:
            update_days = parse_update_days(mapping.get("update_days"))
            if update_days is not None and today.weekday() not in update_days:
                if all_days:
                    log.info("[%s] not an update day (%s) -- updating anyway (--all-days)", target_id, today.strftime("%A"))
                else:
                    log.info("[%s] not an update day (%s) -- skipped, playlist left as is", target_id, today.strftime("%A"))
                    skipped.append(target_id)
                    continue
            if mapping_type == "simple":
                process_mapping(sp, mapping, live)
            elif mapping_type == "radio":
                process_radio_mapping(sp, mapping, live)
            else:
                raise ValueError(f"unknown mapping type: {mapping_type!r}")
        except Exception:
            log.exception("[%s] failed -- continuing with remaining mappings.", target_id)
            failures.append(target_id)

    if skipped:
        log.info("Skipped %d mapping(s) (not an update day): %s", len(skipped), skipped)

    if not live:
        log.info("Dry run only -- nothing written. Re-run with --live to apply.")

    if failures:
        log.error("Run finished with %d failed mapping(s): %s", len(failures), failures)
        sys.exit(1)

    log.info("Run finished successfully.")


if __name__ == "__main__":
    main()
