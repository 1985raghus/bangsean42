from datetime import date

from conftest import FakeGarmin, activity

from readiness import compute_acwr, compute_readiness, pivot_suggestion


def _sleep(days, hours, rhr):
    """Sleep entries as Garmin returns them, oldest first, ending yesterday-relative to `days`."""
    out = []
    for i, (h, r) in enumerate(zip(hours, rhr)):
        out.append({
            "calendarDate": f"2026-09-{i + 1:02d}",
            "values": {"totalSleepTimeInSeconds": h * 3600, "restingHeartRate": r},
        })
    return out


def test_acwr_ratio_and_baseline_readiness():
    as_of = date(2026, 9, 28)
    daily = {f"2026-09-{d:02d}": 10.0 for d in range(1, 29)}  # 10 km every day for 4 weeks
    acwr = compute_acwr(daily, as_of)
    assert acwr["acuteKm"] == 70.0
    assert acwr["chronicWeeklyAvgKm"] == 70.0
    assert acwr["ratio"] == 1.0
    assert acwr["baselineReady"] is True

    thin = compute_acwr({"2026-09-27": 10.0, "2026-09-28": 12.0}, as_of)
    assert thin["baselineReady"] is False


def test_readiness_green_when_everything_is_normal():
    client = FakeGarmin(sleep=_sleep(14, [7.5] * 14, [50] * 14))
    r = compute_readiness(client, as_of=date(2026, 9, 15))
    assert r["available"] and r["verdict"] == "green" and r["score"] == 100
    assert r["reasons"] == ["Sleep, resting HR, and training load all look normal against your own baseline."]


def test_readiness_red_on_short_sleep_and_elevated_rhr():
    hours = [7.5] * 13 + [5.0]
    rhr = [50] * 13 + [57]
    client = FakeGarmin(sleep=_sleep(14, hours, rhr))
    r = compute_readiness(client, as_of=date(2026, 9, 15))
    assert r["score"] == 50 and r["verdict"] == "red"
    assert any("Slept 5.0h" in s for s in r["reasons"])
    assert any("+7.0 above" in s for s in r["reasons"])


def test_readiness_unavailable_without_any_data():
    r = compute_readiness(FakeGarmin(), as_of=date(2026, 9, 15))
    assert r["available"] is False


def test_pivot_suggestion_only_for_quality_days():
    red = {"available": True, "verdict": "red", "score": 45}
    assert pivot_suggestion(red, "easy") is None
    assert pivot_suggestion(red, "mp")["severity"] == "critical"
    amber = {"available": True, "verdict": "amber", "score": 70}
    assert pivot_suggestion(amber, "tempo")["severity"] == "warning"
    assert pivot_suggestion({"available": True, "verdict": "green", "score": 100}, "tempo") is None


def test_load_component_spike_from_acwr_when_no_garmin_snapshot():
    # 4 weeks of light running then a heavy last week -> ratio well over 1.5
    acts = [activity(f"2026-09-{d:02d}", 3.0, 20) for d in range(1, 22)]
    acts += [activity(f"2026-09-{d:02d}", 15.0, 100) for d in range(22, 29)]
    client = FakeGarmin(activities=acts, sleep=_sleep(14, [7.5] * 14, [50] * 14))
    r = compute_readiness(client, as_of=date(2026, 9, 28))
    assert r["trainingLoad"]["flag"] == "spike"
    assert r["score"] == 70 and r["verdict"] == "amber"
