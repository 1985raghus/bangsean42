import pytest
from conftest import FakeGarmin, laps, stream, typed

import history
from history import (
    fetch_activity_laps,
    fetch_interval_reps,
    fetch_race_splits,
    pacing_for_activity,
    race_pacing_summary,
)


def test_splits_and_laps_share_one_garmin_call_per_activity():
    client = FakeGarmin(splits={7: laps((1000, 400, 150), (1000, 410, 155), (300, 120, 150))})
    fetch_race_splits(client, 7)
    fetch_activity_laps(client, 7)
    pacing_for_activity(client, 7)
    pacing_for_activity(client, 7)
    assert client.calls["splits"] == 1
    # and the two views still differ where they should: race splits drop the short tail lap
    assert len(fetch_race_splits(client, 7)) == 2
    assert len(fetch_activity_laps(client, 7)) == 3


def test_failed_fetch_is_not_cached():
    client = FakeGarmin()  # no splits at all -> every fetch raises
    with pytest.raises(RuntimeError):
        fetch_race_splits(client, 99)
    client.splits[99] = laps((1000, 400, 150))
    assert len(fetch_race_splits(client, 99)) == 1
    assert client.calls["splits"] == 2


def test_clear_activity_cache_forces_a_refetch():
    client = FakeGarmin(splits={7: laps((1000, 400, 150))})
    fetch_race_splits(client, 7)
    history.clear_activity_cache()
    fetch_race_splits(client, 7)
    assert client.calls["splits"] == 2


def test_cache_is_bounded():
    client = FakeGarmin(splits={i: laps((1000, 400, 150)) for i in range(history._ACTIVITY_CACHE_MAX + 5)})
    for i in range(history._ACTIVITY_CACHE_MAX + 5):
        fetch_race_splits(client, i)
    assert len(history._activity_cache) == history._ACTIVITY_CACHE_MAX


def test_race_pacing_summary_fade_and_decoupling():
    splits = fetch_race_splits(FakeGarmin(splits={1: laps(*([(1000, 360, 150)] * 5 + [(1000, 390, 165)] * 5))}), 1)
    pacing = race_pacing_summary(splits)
    assert pacing["available"]
    assert pacing["fadeSecPerKm"] == 30.0
    assert pacing["firstHalfAvgHr"] == 150 and pacing["secondHalfAvgHr"] == 165
    # EF drops from (1/360)/150 to (1/390)/165: decoupling well above the 5 % threshold
    assert pacing["decouplingPct"] > 5


def test_race_pacing_summary_needs_two_halves():
    assert race_pacing_summary([]) == {"available": False}
    assert race_pacing_summary([{"paceSecPerKm": 400, "avgHr": None}])["available"] is False


def test_a_single_long_lap_falls_back_to_computed_kilometres():
    """A long run with auto-lap off arrives as one lap - the km splits come from the stream.

    This is the bug that made the fade forecast read four long runs and find one
    usable fade: the kilometre detail was never missing, just never computed.
    """
    client = FakeGarmin(
        splits={5: laps((15000, 6000, 160))},
        details={5: stream(380, 385, 390, 395, 400, 405, hr=[150, 152, 155, 158, 160, 162])},
    )
    splits = fetch_race_splits(client, 5)
    assert [s["paceSecPerKm"] for s in splits] == [380, 385, 390, 395, 400, 405]
    assert [s["avgHr"] for s in splits] == [150, 152, 155, 158, 160, 162]
    pacing = pacing_for_activity(client, 5)
    assert pacing["available"] and pacing["fadeSecPerKm"] == pytest.approx(15.0)  # 385 avg -> 400 avg


def test_auto_lapped_kilometres_are_used_as_is():
    """Auto-lap at 1 km already gives true km splits - don't spend a details call."""
    client = FakeGarmin(splits={6: laps(*([(1000, 360, 150)] * 4 + [(420, 160, 150)]))})
    splits = fetch_race_splits(client, 6)
    assert len(splits) == 4
    assert client.calls["details"] == 0


def test_unreadable_stream_keeps_the_laps():
    """A details call that fails leaves the laps in place rather than losing the activity."""
    client = FakeGarmin(splits={8: laps((2000, 700, 170), (2000, 710, 172))})  # workout steps, no details
    splits = fetch_race_splits(client, 8)
    assert [round(s["paceSecPerKm"]) for s in splits] == [350, 355]


def test_reps_report_moving_pace_not_elapsed():
    """A stop inside a rep is not the runner being slow.

    Rep 3 of 2 Oct ran 2 km in 9:50 of running with an 89 s stand-still in the
    middle of it (the national anthem, 80 s after the rep started). Charged as
    elapsed it reads 5:39/km - the slowest rep of the day. It was the fastest.
    """
    client = FakeGarmin(typed_splits={11: typed(
        ("INTERVAL_WARMUP", 2000, 828, 828, 155),
        ("INTERVAL_ACTIVE", 2000, 638, 638, 176),
        ("INTERVAL_RECOVERY", 400, 182, 182, 167),
        ("INTERVAL_ACTIVE", 2000, 679, 590, 178),
        ("INTERVAL_ACTIVE", 20, 7, 7, 180),  # the tail Garmin adds on stopping the timer
    )})
    reps = fetch_interval_reps(client, 11)
    assert len(reps) == 2  # warmup, recovery and the 20 m tail all excluded
    assert reps[0]["paceSecPerKm"] == 319.0 and reps[0]["stoppedSec"] == 0
    assert reps[1]["paceSecPerKm"] == 295.0  # 4:55, not 5:39
    assert reps[1]["stoppedSec"] == 89
