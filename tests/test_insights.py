from conftest import FakeGarmin, laps

from insights import (
    _rule_easy_too_hard,
    _rule_quality_pace_miss,
    aerobic_effort_verdict,
    generate_insights,
)
from plan_data import HR_CEILINGS, PACES
from progress import pace_to_sec


def _row(kind, pace_sec, hr=None, date="2026-09-08", **extra):
    return {
        "week": "W2", "date": date, "actualDate": None, "label": f"{kind} run", "kind": kind, "status": "done",
        "plannedKm": 8, "actualKm": 8, "actualPace": pace_sec, "actualPaceLabel": "6:00", "inZone": None,
        "actualHr": hr, "activityId": int(date.replace("-", "")), **extra,
    }


# --- the rule the app is built around: effort on aerobic days -------------------------

def test_easy_run_over_hr_ceiling_is_flagged_even_at_a_slow_pace():
    slow_pace = pace_to_sec(PACES["easy"][0]) + 30
    out = _rule_easy_too_hard([_row("easy", slow_pace, hr=HR_CEILINGS["easy"] + 12)])
    assert len(out) == 1
    assert "HR ceiling" in out[0]["title"]
    assert "12 over" in out[0]["detail"]


def test_easy_run_under_hr_ceiling_is_not_flagged_for_a_quick_pace():
    quick_pace = pace_to_sec(PACES["easy"][1]) - 40
    assert _rule_easy_too_hard([_row("easy", quick_pace, hr=HR_CEILINGS["easy"] - 4)]) == []


def test_hr_within_tolerance_is_not_over():
    v = aerobic_effort_verdict(_row("long", 420, hr=HR_ceiling_plus(1, "long")))
    assert v["basis"] == "hr" and v["over"] is False


def HR_ceiling_plus(n, kind):
    return HR_CEILINGS[kind] + n


def test_easy_run_without_hr_falls_back_to_pace_with_tolerance():
    fast = pace_to_sec(PACES["easy"][1])
    assert _rule_easy_too_hard([_row("easy", fast - 5)]) == []        # within the 10 s tolerance
    out = _rule_easy_too_hard([_row("easy", fast - 25)])
    assert len(out) == 1 and out[0]["detail"].endswith("no heart rate on this run to judge effort directly.")


def test_mp_finish_long_run_is_skipped_by_the_aerobic_rule():
    row = _row("long", 380, hr=HR_CEILINGS["long"] + 20, mpEffortPace=340.0)
    assert aerobic_effort_verdict(row) is None
    assert _rule_easy_too_hard([row]) == []


def test_quality_rows_are_not_aerobic():
    assert aerobic_effort_verdict(_row("tempo", 300, hr=190)) is None


# --- quality sessions: both edges matter -----------------------------------------------

def test_quality_pace_miss_flags_fast_as_warning_and_slow_as_info():
    slow, fast = (pace_to_sec(p) for p in PACES["tempo"])
    out = _rule_quality_pace_miss([
        _row("tempo", fast - 8, date="2026-09-11", wholeActivityPaceLabel="6:00", workDistanceKm=6.0),
        _row("tempo", slow + 8, date="2026-09-18"),
        _row("tempo", (slow + fast) / 2, date="2026-09-25"),
    ])
    assert [(i["severity"], i["id"][:12]) for i in out] == [("warning", "quality-fast"), ("info", "quality-slow")]
    assert "work-interval laps" in out[0]["detail"]


# --- generate_insights: ordering, all-clear, and one Garmin call per long run ---------

def test_generate_insights_all_clear_when_nothing_fires():
    client = FakeGarmin()
    rows = [_row("easy", pace_to_sec(PACES["easy"][0]), hr=HR_CEILINGS["easy"] - 5)]
    kpis = [{"week": "W2", "rampFlag": False}]
    out = generate_insights(client, rows, kpis, "W2")
    assert [i["id"] for i in out] == ["all-clear"]


def test_long_run_splits_fetched_once_for_fade_and_decoupling():
    # A steady long run with a fade and HR drift trips both long-run rules; they must
    # share one Garmin call, not make one each.
    row = _row("long", 395, hr=165, date="2026-09-13", actualKm=18)
    client = FakeGarmin(splits={row["activityId"]: laps(*([(1000, 380, 158)] * 9 + [(1000, 410, 172)] * 9))})
    out = generate_insights(client, [row], [{"week": "W2", "rampFlag": False}], "W2")
    ids = {i["id"].split("-20")[0] for i in out}
    assert {"long-fade", "decoupling"} <= ids
    assert client.calls["splits"] == 1
    assert out[0]["severity"] in ("critical", "warning")  # most severe first
