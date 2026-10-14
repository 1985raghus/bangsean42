from weather import easy_pace_offset_sec, heat_tier, coolest_window, summarize


def test_easy_offset_scales_and_floors():
    assert easy_pace_offset_sec(24) == 0        # comfortable reference
    assert easy_pace_offset_sec(20) == 0        # cooler than reference never negative
    assert easy_pace_offset_sec(34) == 18       # (34-24)*1.8
    assert easy_pace_offset_sec(60) == 45       # capped


def test_heat_tiers():
    assert heat_tier(22)["tier"] == "comfortable"
    assert heat_tier(30)["tier"] == "warm"
    assert heat_tier(34)["tier"] == "hot"
    assert heat_tier(39)["tier"] == "extreme"


def test_coolest_window_skips_past_and_respects_horizon():
    times = ["2026-10-14T13:00", "2026-10-14T18:00", "2026-10-15T05:00", "2026-10-16T05:00"]
    app = [33.0, 30.0, 27.0, 20.0]
    w = coolest_window(times, app, "2026-10-14T13:15", hours_ahead=20)
    assert w["hour"] == "05:00" and w["apparentC"] == 27.0  # the 16th is beyond the horizon


def test_summarize_shapes_the_payload():
    raw = {
        "current": {"time": "2026-10-14T13:15", "temperature_2m": 28.5, "relative_humidity_2m": 80, "apparent_temperature": 34.5},
        "hourly": {"time": ["2026-10-14T13:00", "2026-10-15T06:00"], "apparent_temperature": [34.0, 29.0]},
    }
    out = summarize(raw, {"label": "Bangkok", "tz": "Asia/Bangkok"})
    assert out["available"] and out["tier"] == "hot"
    assert out["apparentC"] == 34.5 and out["easyPaceOffsetSec"] == 18
    assert out["coolWindow"]["apparentC"] == 29.0


def test_summarize_handles_missing_fields():
    assert summarize({"current": {}}, {"label": "X", "tz": "Asia/Bangkok"})["available"] is False
