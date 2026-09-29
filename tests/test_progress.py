from datetime import date

from conftest import FakeGarmin, activity, laps

import progress
from plan_data import HR_CEILINGS, MARATHON_KM, PACES, STRETCH_TIME_SEC
from progress import (
    build_rows,
    compute_fade_forecast,
    compute_prediction,
    in_zone,
    pace_to_sec,
    planned_weekly_kpis,
    refine_quality_pace,
)


def _easy(date, km=8):
    return {"date": date, "name": f"W1 Tue - Easy ({km}K)", "blocks": [{"role": "main", "kind": "easy", "km": km}]}


def _long(date, km=16):
    return {"date": date, "name": f"W1 Sun - Long Run ({km}K)", "blocks": [{"role": "main", "kind": "long", "km": km}]}


def _long_mp_finish(date, long_km=15, mp_km=5):
    return {
        "date": date,
        "name": f"W3 Sun - Long Run w/ MP Finish ({long_km + mp_km}K)",
        "blocks": [{"role": "main", "kind": "long", "km": long_km}, {"role": "main", "kind": "mp", "km": mp_km}],
    }


def _reps(date, kind, reps, rep_km, week="W2"):
    return {
        "date": date,
        "name": f"{week} Fri - Intervals ({reps}x{rep_km}K @ {kind})",
        "blocks": [
            {"role": "warmup", "kind": "easy", "km": 2.0},
            {"role": "repeat", "kind": kind, "reps": reps, "rep_km": rep_km, "recovery_km": 0.4, "recovery_kind": "recovery"},
            {"role": "cooldown", "kind": "easy", "km": 2.0},
        ],
    }


# --- in_zone: effort is the instruction on aerobic days -----------------------------

def test_aerobic_run_with_hr_is_judged_by_ceiling_not_pace():
    slow, fast = PACES["easy"]
    quick_pace = pace_to_sec(fast) - 30  # well faster than the band
    assert in_zone("easy", quick_pace, slow, fast, actual_hr=HR_CEILINGS["easy"] - 3) is True
    assert in_zone("easy", quick_pace, slow, fast, actual_hr=HR_CEILINGS["easy"] + 10) is False


def test_aerobic_hr_tolerance_absorbs_a_beat_or_two():
    slow, fast = PACES["long"]
    assert in_zone("long", 420, slow, fast, actual_hr=HR_CEILINGS["long"] + progress.HR_CEILING_TOLERANCE_BPM) is True
    assert in_zone("long", 420, slow, fast, actual_hr=HR_CEILINGS["long"] + progress.HR_CEILING_TOLERANCE_BPM + 1) is False


def test_aerobic_run_without_hr_falls_back_to_fast_pace_edge_only():
    slow, fast = PACES["easy"]
    assert in_zone("easy", pace_to_sec(fast) - 5, slow, fast) is False  # too fast
    assert in_zone("easy", pace_to_sec(slow) + 60, slow, fast) is True  # slower than the band is fine
    assert in_zone("easy", None, slow, fast) is None


def test_quality_run_is_judged_on_both_pace_edges():
    slow, fast = PACES["tempo"]
    assert in_zone("tempo", pace_to_sec(fast) - 1, slow, fast, actual_hr=120) is False
    assert in_zone("tempo", pace_to_sec(slow) + 1, slow, fast, actual_hr=120) is False
    assert in_zone("tempo", (pace_to_sec(slow) + pace_to_sec(fast)) / 2, slow, fast) is True


# --- build_rows: matching sessions to what synced ------------------------------------

def test_build_rows_statuses_and_date_tolerance():
    sessions = [_easy("2026-09-08"), _easy("2026-09-10"), _easy("2026-09-12"), _easy("2026-09-15"), _easy("2026-09-20")]
    acts = {
        "2026-09-08": activity("2026-09-08", 8.0, 56, hr=150),   # exact
        "2026-09-11": activity("2026-09-11", 8.0, 56, hr=150),   # a day late for 09-10
        "2026-09-12": activity("2026-09-12", 5.0, 35, hr=150),   # short: partial
    }
    rows = build_rows(sessions, acts, today=date(2026, 9, 15))
    by_date = {r["date"]: r for r in rows}
    assert by_date["2026-09-08"]["status"] == "done" and by_date["2026-09-08"]["actualDate"] is None
    assert by_date["2026-09-10"]["status"] == "done" and by_date["2026-09-10"]["actualDate"] == "2026-09-11"
    assert by_date["2026-09-12"]["status"] == "partial"
    assert by_date["2026-09-15"]["status"] == "today"
    assert by_date["2026-09-20"]["status"] == "upcoming"


def test_build_rows_never_matches_one_activity_to_two_sessions():
    sessions = [_easy("2026-09-08"), _easy("2026-09-09")]
    acts = {"2026-09-09": activity("2026-09-09", 8.0, 56)}
    rows = build_rows(sessions, acts, today=date(2026, 9, 12))
    # exact match on 09-09 wins; 09-08 then has nothing left within tolerance
    assert [r["status"] for r in rows] == ["missed", "done"]


def test_build_rows_uses_hr_for_easy_run_verdict():
    slow, fast = PACES["easy"]
    fast_min = pace_to_sec(fast) / 60 - 0.5  # min/km, faster than the band
    acts = {"2026-09-08": activity("2026-09-08", 8.0, 8 * fast_min, hr=HR_CEILINGS["easy"] - 5)}
    rows = build_rows([_easy("2026-09-08")], acts, today=date(2026, 9, 9))
    assert rows[0]["inZone"] is True  # quick pace, but the effort was right


def test_build_rows_mp_finish_long_run_is_not_judged_by_whole_run_hr():
    # The finish lifts the average HR on purpose; the row's verdict comes from pace.
    acts = {"2026-09-20": activity("2026-09-20", 20.0, 126, hr=HR_CEILINGS["long"] + 15)}
    rows = build_rows([_long_mp_finish("2026-09-20")], acts, today=date(2026, 9, 21))
    slow, fast = PACES["long"]
    assert rows[0]["inZone"] == (rows[0]["actualPace"] >= pace_to_sec(fast))


# --- refine_quality_pace: work reps, not the diluted average -----------------------

def test_refine_quality_pace_reads_garmin_typed_reps():
    session = _reps("2026-09-11", "mp", 3, 2.0)
    acts = {"2026-09-11": activity("2026-09-11", 11.2, 60, hr=170, activity_id=11)}
    rows = build_rows([session], acts, today=date(2026, 9, 12))
    mp_mid = (pace_to_sec(PACES["mp"][0]) + pace_to_sec(PACES["mp"][1])) / 2
    client = FakeGarmin(typed_splits={11: {"splits": [
        {"type": "INTERVAL_WARMUP", "distance": 2000, "duration": 900},
        {"type": "INTERVAL_ACTIVE", "distance": 2000, "duration": mp_mid * 2, "averageHR": 172},
        {"type": "INTERVAL_RECOVERY", "distance": 400, "duration": 200},
        {"type": "INTERVAL_ACTIVE", "distance": 2000, "duration": mp_mid * 2, "averageHR": 174},
        {"type": "INTERVAL_ACTIVE", "distance": 2000, "duration": mp_mid * 2, "averageHR": 176},
        {"type": "INTERVAL_ACTIVE", "distance": 10, "duration": 4},  # the stop-the-timer tail
    ]}})
    refine_quality_pace(client, rows, {session["date"]: session})
    r = rows[0]
    assert r["workLapCount"] == 3
    assert abs(r["actualPace"] - mp_mid) < 0.01
    assert r["inZone"] is True
    assert r["mpEffortKm"] == 6.0
    assert r["actualHr"] == 174.0
    assert r["wholeActivityPaceLabel"] is not None


def test_refine_quality_pace_falls_back_to_laps_when_no_typed_reps():
    session = _reps("2026-09-18", "tempo", 3, 2.0)
    acts = {"2026-09-18": activity("2026-09-18", 11.2, 60, hr=175, activity_id=18)}
    rows = build_rows([session], acts, today=date(2026, 9, 19))
    tempo_fast = pace_to_sec(PACES["tempo"][1])
    easy_pace = pace_to_sec(PACES["easy"][0])
    client = FakeGarmin(splits={18: laps(
        (2000, easy_pace * 2, 140),          # warm-up
        (2000, (tempo_fast - 10) * 2, 182),  # work, faster than band
        (400, easy_pace * 0.4, 150),
        (2000, (tempo_fast - 10) * 2, 184),
        (2000, (tempo_fast - 10) * 2, 186),
        (2000, easy_pace * 2, 150),          # cool-down
    )})
    refine_quality_pace(client, rows, {session["date"]: session})
    r = rows[0]
    assert r["workLapCount"] == 3
    assert r["inZone"] is False  # too fast is still off band
    assert abs(r["actualPace"] - (tempo_fast - 10)) < 0.01


def test_refine_isolates_mp_finish_of_a_long_run():
    session = _long_mp_finish("2026-09-20", 15, 5)
    acts = {"2026-09-20": activity("2026-09-20", 20.0, 126, hr=168, activity_id=20)}
    rows = build_rows([session], acts, today=date(2026, 9, 21))
    mp_pace = pace_to_sec(PACES["mp"][0])
    easy_pace = pace_to_sec(PACES["long"][0])
    client = FakeGarmin(splits={20: laps(*([(1000, easy_pace, 160)] * 15 + [(1000, mp_pace, 178)] * 5))})
    refine_quality_pace(client, rows, {session["date"]: session})
    r = rows[0]
    assert r["mpEffortKm"] == 5.0
    assert abs(r["mpEffortPace"] - mp_pace) < 0.01
    assert r["actualPace"] != r["mpEffortPace"]  # whole-run pace left alone


# --- compute_prediction ----------------------------------------------------------------

def test_prediction_unavailable_without_mp_evidence():
    session = _easy("2026-09-08")
    rows = build_rows([session], {"2026-09-08": activity("2026-09-08", 8, 56)}, today=date(2026, 9, 9))
    assert compute_prediction(rows, {session["date"]: session})["available"] is False


def test_prediction_uses_isolated_mp_pace_and_flags_too_fast_reps():
    s1, s2 = _reps("2026-09-11", "mp", 3, 2.0), _reps("2026-09-18", "mp", 3, 2.0, week="W3")
    goal_pace = STRETCH_TIME_SEC / MARATHON_KM
    rows = [
        {**_row_stub(s1), "mpEffortPace": goal_pace, "actualPace": goal_pace + 60},
        {**_row_stub(s2), "mpEffortPace": pace_to_sec(PACES["mp"][1]) - 20, "actualPace": goal_pace + 60},
    ]
    p = compute_prediction(rows, {s1["date"]: s1, s2["date"]: s2})
    assert p["available"] and p["diluted"] is False
    assert p["qualifyingCount"] == 2
    assert p["fasterThanMpCount"] == 1
    assert "Read it with care" in p["message"]
    assert p["range"]["available"] is False  # 2 sessions is not a range
    assert p["verdict"] == "on_goal"


def test_prediction_recency_weights_the_last_three():
    sessions = [_reps(f"2026-09-{d:02d}", "mp", 3, 2.0) for d in (4, 11, 18)]
    paces = [400.0, 380.0, 360.0]
    rows = [{**_row_stub(s), "mpEffortPace": p, "actualPace": p} for s, p in zip(sessions, paces)]
    p = compute_prediction(rows, {s["date"]: s for s in sessions})
    weighted = (1 * 400 + 2 * 380 + 3 * 360) / 6
    assert abs(p["avgPredictedSec"] - weighted * MARATHON_KM) < 0.01
    assert abs(p["latestPredictedSec"] - 360 * MARATHON_KM) < 0.01


def _row_stub(session):
    return {
        "week": session["name"].split(" ")[0], "date": session["date"], "actualDate": None, "label": "x",
        "kind": "mp", "status": "done", "plannedKm": 10, "actualKm": 10, "actualPace": None,
        "actualPaceLabel": "", "inZone": True, "actualHr": None, "activityId": 1,
    }


# --- compute_fade_forecast --------------------------------------------------------------

def test_fade_forecast_skips_mp_finish_long_runs_and_penalises_positive_splits():
    steady, finish = _long("2026-09-13", 18), _long_mp_finish("2026-09-20")
    acts = {
        "2026-09-13": activity("2026-09-13", 18, 120, hr=165, activity_id=13),
        "2026-09-20": activity("2026-09-20", 20, 126, hr=170, activity_id=20),
    }
    rows = build_rows([steady, finish], acts, today=date(2026, 9, 21))
    client = FakeGarmin(splits={
        13: laps(*([(1000, 380, 160)] * 9 + [(1000, 410, 168)] * 9)),   # 30 s/km fade
        20: laps(*([(1000, 400, 160)] * 15 + [(1000, 340, 178)] * 5)),  # negative split, by design
    })
    prediction = {"available": True, "avgPredictedSec": 4 * 3600}
    ff = compute_fade_forecast(client, rows, prediction, {steady["date"]: steady, finish["date"]: finish})
    assert ff["available"] and ff["sampleSize"] == 1
    assert ff["avgFadeSecPerKm"] == 30.0
    # first half at the predicted pace, second half 30 s/km slower
    assert abs(ff["forecastFinishSec"] - (prediction["avgPredictedSec"] + 30 * MARATHON_KM / 2)) < 0.01
    assert ff["firstHalfPaceSec"] == prediction["avgPredictedSec"] / MARATHON_KM
    assert client.calls["splits"] == 1  # the MP-finish run's splits were never fetched


def test_fade_forecast_does_not_reward_negative_splits():
    steady = _long("2026-09-13", 18)
    rows = build_rows([steady], {"2026-09-13": activity("2026-09-13", 18, 120, hr=160, activity_id=13)}, today=date(2026, 9, 14))
    client = FakeGarmin(splits={13: laps(*([(1000, 400, 160)] * 9 + [(1000, 380, 162)] * 9))})
    prediction = {"available": True, "avgPredictedSec": 4 * 3600}
    ff = compute_fade_forecast(client, rows, prediction, {steady["date"]: steady})
    assert ff["appliedFadePenaltySecPerKm"] == 0.0
    assert ff["forecastFinishSec"] == prediction["avgPredictedSec"]


def test_build_rows_thursday_skipped_does_not_steal_fridays_run():
    thu, fri = _easy("2026-09-10"), _reps("2026-09-11", "mp", 3, 2.0)
    rows = build_rows([thu, fri], {"2026-09-11": activity("2026-09-11", 11.2, 60)}, today=date(2026, 9, 12))
    assert [r["status"] for r in rows] == ["missed", "done"]
    assert rows[1]["kind"] == "mp" and rows[1]["actualDate"] is None


# --- planned_weekly_kpis ----------------------------------------------------------------

def test_planned_weekly_kpis_flags_ramp_over_20_pct():
    sessions = [
        _easy("2026-09-08", 10), _long("2026-09-13", 10),   # W1 20 km
        {**_easy("2026-09-15", 15), "name": "W2 Tue - Easy (15K)"},
        {**_long("2026-09-20", 15), "name": "W2 Sun - Long Run (15K)"},  # W2 30 km: +50%
    ]
    kpis = planned_weekly_kpis(sessions)
    assert kpis[0]["rampPct"] is None and kpis[0]["longRunPct"] == 50.0
    assert kpis[1]["rampPct"] == 50.0 and kpis[1]["rampFlag"] is True
