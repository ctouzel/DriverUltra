"""
Radio-type playlist support.

A "radio" mapping assembles its target playlist from several roles rather
than a flat list of sources:
    - top_songs: included IN FULL, never sampled.
    - top_albums / classics / great_classics: a random sample of each
      (sizes come from the mapping's "samples" config).
    - optional seasonal "holidays" sources: only contribute when today's
      date falls inside their configured window.

It also runs a maintenance step before assembling the playlist: any track
in top_songs older than `classics_promotion.after_weeks` (by the date it
was added to top_songs, which Spotify already tracks per-playlist) is
copied into classics -- never removed from top_songs, and never duplicated
in classics.
"""

from datetime import datetime, timedelta, timezone


def parse_added_at(added_at):
    """Parse Spotify's added_at ("2020-11-14T12:13:08Z") into an aware UTC
    datetime. Returns None if missing or unparseable -- such a track is
    simply never eligible for promotion, rather than erroring the run."""
    if not added_at:
        return None
    try:
        return datetime.strptime(added_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def find_promotable(top_songs, classics_tracks, after_weeks, now=None):
    """URIs (deduped, first-seen order) of top_songs tracks added more than
    `after_weeks` weeks ago that aren't already in classics_tracks."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(weeks=after_weeks)
    existing = {t["uri"] for t in classics_tracks}

    promotable = []
    seen = set()
    for t in top_songs:
        added_at = parse_added_at(t.get("added_at"))
        if added_at is None or added_at > cutoff:
            continue
        if t["uri"] in existing or t["uri"] in seen:
            continue
        promotable.append(t["uri"])
        seen.add(t["uri"])
    return promotable


def promote_aged_top_songs(sp, log, target_label, top_songs, classics_id, classics_tracks, after_weeks, now=None):
    """Add every promotable track (see find_promotable) to the classics
    playlist. Returns how many were added. Actually writes -- callers in a
    dry run should use find_promotable directly instead."""
    to_add = find_promotable(top_songs, classics_tracks, after_weeks, now)
    if not to_add:
        log.info("[%s] no Top Songs tracks old enough to promote to Classics", target_label)
        return 0

    log.info(
        "[%s] promoting %d track(s) from Top Songs to Classics (>%d week(s) old)",
        target_label, len(to_add), after_weeks,
    )
    for i in range(0, len(to_add), 100):
        sp.playlist_add_items(classics_id, to_add[i : i + 100])
    return len(to_add)


def _parse_mmdd(s):
    month, day = s.split("-")
    return (int(month), int(day))


def date_in_window(today, start_mmdd, end_mmdd):
    """True if today's (month, day) falls within [start, end] inclusive.
    Compared purely on (month, day) -- the year is ignored, so the window
    recurs every year. Handles windows that cross the year boundary (e.g.
    "12-26" to "01-02")."""
    t = (today.month, today.day)
    start, end = _parse_mmdd(start_mmdd), _parse_mmdd(end_mmdd)
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end


def active_holidays(holidays, today=None):
    """The holiday configs (dicts with at least start/end) whose window
    includes today."""
    today = today or datetime.now(timezone.utc).date()
    return [h for h in (holidays or []) if date_in_window(today, h["start"], h["end"])]


if __name__ == "__main__":
    # Self-test with fake data -- no network needed.
    from datetime import date

    now = datetime(2026, 9, 8, tzinfo=timezone.utc)

    old = (now - timedelta(weeks=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = (now - timedelta(weeks=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    top_songs = [
        {"uri": "spotify:track:old1", "added_at": old},
        {"uri": "spotify:track:old2", "added_at": old},
        {"uri": "spotify:track:recent1", "added_at": recent},
        {"uri": "spotify:track:already-in-classics", "added_at": old},
        {"uri": "spotify:track:no-date", "added_at": None},
    ]
    classics = [{"uri": "spotify:track:already-in-classics"}]

    promotable = find_promotable(top_songs, classics, after_weeks=3, now=now)
    assert promotable == ["spotify:track:old1", "spotify:track:old2"], promotable

    # Non-wrapping window
    assert date_in_window(date(2026, 12, 10), "12-01", "12-25") is True
    assert date_in_window(date(2026, 11, 30), "12-01", "12-25") is False
    assert date_in_window(date(2026, 12, 26), "12-01", "12-25") is False

    # Wrapping window (New Year's)
    assert date_in_window(date(2026, 12, 30), "12-26", "01-02") is True
    assert date_in_window(date(2027, 1, 1), "12-26", "01-02") is True
    assert date_in_window(date(2026, 6, 1), "12-26", "01-02") is False

    holidays = [{"name": "Christmas", "start": "12-01", "end": "12-25"}]
    assert [h["name"] for h in active_holidays(holidays, today=date(2026, 12, 10))] == ["Christmas"]
    assert active_holidays(holidays, today=date(2026, 6, 1)) == []

    print("radio.py self-test passed.")
