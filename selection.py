"""
Phase 3: pure random-selection logic. No Spotify API calls in here -- easy
to test in isolation (see the self-test at the bottom).
"""

import random


def select_tracks(tracks, count, rng=None):
    """Pick `count` tracks at random from `tracks`.

    - `tracks`: list of dicts, each with at least a "uri" key.
    - Deduplicated by URI first (a track that appears in two source
      playlists should only be eligible once).
    - Returns at most `count` tracks -- fewer if the deduplicated pool is
      smaller than `count`.
    - Pass `rng` (a random.Random instance) for deterministic tests;
      defaults to a fresh Random() seeded from the system.
    """
    rng = rng or random.Random()

    seen = set()
    unique_tracks = []
    for t in tracks:
        if t["uri"] not in seen:
            seen.add(t["uri"])
            unique_tracks.append(t)

    count = min(count, len(unique_tracks))
    return rng.sample(unique_tracks, count)


if __name__ == "__main__":
    # Quick self-test with fake data -- no network needed.
    fake_pool = (
        [{"uri": f"spotify:track:{i}", "title": f"Track {i}", "artists": "A"} for i in range(10)]
        # duplicate a few URIs, as would happen across overlapping sources
        + [{"uri": "spotify:track:0", "title": "Track 0", "artists": "A"}]
        + [{"uri": "spotify:track:1", "title": "Track 1", "artists": "A"}]
    )

    rng = random.Random(42)  # deterministic for the test
    picked = select_tracks(fake_pool, count=5, rng=rng)
    assert len(picked) == 5, f"expected 5 tracks, got {len(picked)}"
    assert len({t['uri'] for t in picked}) == 5, "duplicates leaked through"

    # Asking for more than the pool has should just return the whole pool.
    picked_all = select_tracks(fake_pool, count=999, rng=rng)
    assert len(picked_all) == 10, f"expected 10 unique tracks, got {len(picked_all)}"

    print("selection.py self-test passed.")
