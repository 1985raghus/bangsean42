import pytest
from conftest import FakeGarmin, laps

import history
from history import fetch_activity_laps, fetch_race_splits, pacing_for_activity, race_pacing_summary


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
