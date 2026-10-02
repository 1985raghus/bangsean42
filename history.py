"""Per-activity detail (splits, laps, interval reps) and the pre-plan history.

The history half feeds /api/history and heat.py: prior Bangsaen races with
their km splits, training-block comparisons and VO2max - the analysis that
shaped the plan's goal tiers, kept live rather than baked into a document.

The per-activity half is shared by progress.py, insights.py and
weekly_summary.py. A finished activity's splits never change, so they are
memoised here for the life of the process: before this, one long run's splits
were fetched three times per cache miss (the fade rule, the decoupling rule
and the fade forecast), against an unofficial API that rate-limits.
"""

import statistics
import threading
from collections import OrderedDict
from datetime import date, timedelta

from garminconnect import Garmin

from plan_data import MARATHON_KM
from route import fetch_route

_LOOKBACK_DAYS = 3 * 365
_RIEGEL_EXPONENT = 1.06
_MIN_REAL_RUN_KM = 2.0  # excludes warmup/cooldown fragments logged as separate activities

_ACTIVITY_CACHE_MAX = 256  # (kind, activityId) -> result; a full plan is ~44 runs x 3 kinds
_activity_cache: OrderedDict[tuple[str, int], object] = OrderedDict()
_activity_cache_lock = threading.Lock()


def _memo_activity(kind: str, activity_id: int, fetch):
    """Return the cached result for (kind, activity_id), fetching once if needed.

    Only successful fetches are cached - a failed call is retried next time, so a
    transient Garmin error can't pin an empty result for the rest of the process.
    """
    key = (kind, int(activity_id))
    with _activity_cache_lock:
        if key in _activity_cache:
            _activity_cache.move_to_end(key)
            return _activity_cache[key]
    value = fetch()
    with _activity_cache_lock:
        _activity_cache[key] = value
        _activity_cache.move_to_end(key)
        while len(_activity_cache) > _ACTIVITY_CACHE_MAX:
            _activity_cache.popitem(last=False)
    return value


def clear_activity_cache() -> None:
    """Forget every memoised activity detail - used on a forced refresh and on logout."""
    with _activity_cache_lock:
        _activity_cache.clear()


def fetch_history(client: Garmin, plan_start: date, end: date | None = None) -> list[dict]:
    """Fetches 3 years back from plan_start through `end` (default: today).

    Everything before plan_start is the pre-plan baseline these cycle
    comparisons are built on; everything from plan_start onward is the
    in-progress training block itself, needed so a "how's this block going
    so far" figure can grow with actual training instead of staying frozen
    at the day the plan started.
    """
    start = plan_start - timedelta(days=_LOOKBACK_DAYS)
    end = end or date.today()
    activities = client.get_activities_by_date(start.isoformat(), end.isoformat(), activitytype="running")
    return sorted(activities, key=lambda a: a.get("startTimeLocal", ""))


def _raw_splits(client: Garmin, activity_id: int) -> dict:
    """One Garmin call behind both fetch_race_splits and fetch_activity_laps."""
    return _memo_activity("splits", activity_id, lambda: client.get_activity_splits(activity_id) or {})


_MIN_SPLITS = 4  # below this, "first half vs second half" is two laps arguing


def _laps_as_km_splits(laps: list[dict]) -> list[dict]:
    result = []
    for i, lap in enumerate(laps, 1):
        dist_km = lap["distance"] / 1000
        result.append({
            "km": i,
            "paceSecPerKm": (lap["duration"] / dist_km) if dist_km else None,
            "avgHr": lap.get("averageHR"),
            "temp": lap.get("averageTemperature"),
        })
    return result


def _laps_look_like_kilometres(laps: list[dict]) -> bool:
    """True when the watch was auto-lapping at 1 km, so the laps already ARE km splits."""
    if len(laps) < _MIN_SPLITS:
        return False
    return all(900 <= (lap.get("distance") or 0) <= 1100 for lap in laps[:-1])


def _computed_km_splits(client: Garmin, activity_id: int) -> list[dict]:
    """Km splits computed from the activity's own distance/time stream."""

    def fetch():
        try:
            route = fetch_route(client, activity_id)
        except Exception:
            # a details call that fails must not take the fade rule down with it -
            # fetch_race_splits falls back to the laps, which is better than nothing
            return []
        if not route.get("available"):
            return []
        return [
            {"km": s["km"], "paceSecPerKm": s["paceSecPerKm"], "avgHr": s.get("hr"), "temp": None}
            for s in route.get("splits") or []
        ]

    return _memo_activity("computed_splits", activity_id, fetch)


def fetch_race_splits(client: Garmin, activity_id: int) -> list[dict]:
    """Per-km pace/HR for one activity, for a pacing-strategy/fade chart.

    Garmin stores LAPS, not kilometres. With auto-lap off, a 21 km long run arrives
    as a single 21 km lap, and every rule built on these splits - fade, decoupling,
    the fade forecast - silently had nothing to read: four long runs in this block
    produced one fade number between them. So laps are used only when they really
    are kilometre splits (auto-lap on); otherwise the splits are computed from the
    1-second stream, the way Strava does it. Laps remain the last resort if the
    stream can't be read, which keeps a structured workout's steps better than nothing.
    """
    splits = _raw_splits(client, activity_id)
    laps = [lap for lap in splits.get("lapDTOs", []) if lap.get("distance", 0) >= 900]
    if _laps_look_like_kilometres(laps):
        return _laps_as_km_splits(laps)
    computed = _computed_km_splits(client, activity_id)
    if len(computed) >= _MIN_SPLITS:
        return computed
    return _laps_as_km_splits(laps)


def pacing_for_activity(client: Garmin, activity_id: int) -> dict:
    """race_pacing_summary() of an activity's km splits - the one call the fade rule,
    the decoupling rule, the fade forecast and the weekly positives all need."""
    return race_pacing_summary(fetch_race_splits(client, activity_id))


def fetch_interval_reps(client: Garmin, activity_id: int, min_metres: float = 100) -> list[dict]:
    """The work reps of a structured session, from Garmin's own typed splits.

    A pushed workout's steps are labelled by Garmin (INTERVAL_WARMUP / ACTIVE /
    RECOVERY / COOLDOWN), so the reps can be read directly instead of inferred
    from lap paces - which matters when the watch's auto-lap cuts across the
    steps (a 1-mile auto-lap once split every 2 km rep into 1.61 + 0.39).
    Returns [] when the activity has no interval structure.
    """
    data = _memo_activity("typed", activity_id, lambda: client.get_activity_typed_splits(activity_id) or {})
    reps = []
    for split in data.get("splits") or []:
        if (split.get("type") or split.get("splitType")) != "INTERVAL_ACTIVE":
            continue
        distance_m = split.get("distance") or 0
        duration_s = split.get("duration") or 0
        if distance_m < min_metres or duration_s <= 0:
            continue  # the 10 m tail Garmin adds when you stop the timer
        km = distance_m / 1000
        reps.append({
            "km": round(km, 2),
            "paceSecPerKm": round(duration_s / km, 1),
            "hr": round(split["averageHR"]) if split.get("averageHR") else None,
        })
    return reps


def fetch_activity_laps(client: Garmin, activity_id: int) -> list[dict]:
    """Raw per-lap data for a structured interval/tempo workout.

    Unlike fetch_race_splits (built for races/long runs with uniform ~1km auto-laps and a
    >=900m filter), this keeps every lap regardless of distance - interval workout laps vary
    a lot by design (warmup/work/recovery/cooldown), and the device's own auto-lap setting
    (e.g. 1 mile) often doesn't line up with the workout's own step boundaries anyway, so a
    single planned "2km rep" can arrive as two Garmin laps (e.g. 1609m + 391m).
    """
    data = _raw_splits(client, activity_id)
    laps = []
    for lap in data.get("lapDTOs", []):
        distance_m = lap.get("distance") or 0
        duration_s = lap.get("duration") or 0
        if distance_m <= 0 or duration_s <= 0:
            continue
        laps.append({
            "distanceM": distance_m,
            "durationSec": duration_s,
            "paceSecPerKm": duration_s / (distance_m / 1000),
            "avgHr": lap.get("averageHR"),
        })
    return laps


def race_pacing_summary(splits: list[dict]) -> dict:
    """First-half vs second-half pace (and, where HR is available, aerobic decoupling).

    Fade catches the obvious failure: slowing down. Decoupling catches the subtler one:
    holding pace steady while heart rate climbs to do it - the aerobic system drifting even
    though the pace chart looks fine. Standard Pa:HR decoupling formula: efficiency factor
    (speed/HR) in each half, decoupling% = drop in EF from first half to second.
    """
    if not splits:
        return {"available": False}
    half = len(splits) // 2
    first = [s["paceSecPerKm"] for s in splits[:half] if s["paceSecPerKm"]]
    second = [s["paceSecPerKm"] for s in splits[half:] if s["paceSecPerKm"]]
    if not first or not second:
        return {"available": False}
    first_avg = sum(first) / len(first)
    second_avg = sum(second) / len(second)

    first_hr = [s["avgHr"] for s in splits[:half] if s.get("avgHr")]
    second_hr = [s["avgHr"] for s in splits[half:] if s.get("avgHr")]
    decoupling_pct = None
    first_hr_avg = second_hr_avg = None
    if first_hr and second_hr:
        first_hr_avg = sum(first_hr) / len(first_hr)
        second_hr_avg = sum(second_hr) / len(second_hr)
        ef_first = (1 / first_avg) / first_hr_avg
        ef_second = (1 / second_avg) / second_hr_avg
        decoupling_pct = round((ef_first - ef_second) / ef_first * 100, 1) if ef_first else None

    return {
        "available": True,
        "firstHalfAvgPaceSec": first_avg,
        "secondHalfAvgPaceSec": second_avg,
        "fadeSecPerKm": second_avg - first_avg,
        "firstHalfAvgHr": first_hr_avg,
        "secondHalfAvgHr": second_hr_avg,
        "decouplingPct": decoupling_pct,
    }


def _riegel_predict(time_sec: float, from_km: float, to_km: float = MARATHON_KM) -> float:
    return time_sec * (to_km / from_km) ** _RIEGEL_EXPONENT


def _profile_for_window(activities: list[dict], start: date, end_date: date, label: str, weeks: float) -> dict:
    """Training profile for the exact [start, end_date) window.

    The headline numbers: volume, longest run, and - the real diagnostic -
    how much pace/HR varied run to run. A tight spread means every run was
    at roughly the same effort, whatever it was supposed to be.
    """
    runs = [
        a for a in activities
        if start.isoformat() <= a.get("startTimeLocal", "")[:10] < end_date.isoformat()
        and a.get("distance", 0) >= _MIN_REAL_RUN_KM * 1000
    ]
    total_km = sum(a.get("distance", 0) for a in runs) / 1000
    longest_km = max((a.get("distance", 0) / 1000 for a in runs), default=0.0)
    paces = [
        (a["duration"] / 60) / (a["distance"] / 1000)
        for a in runs
        if a.get("distance") and a.get("duration")
    ]
    hrs = [a["averageHR"] for a in runs if a.get("averageHR")]

    return {
        "label": label,
        "windowStart": start.isoformat(),
        "windowEnd": end_date.isoformat(),
        "weeksBack": round(weeks, 1),
        "runCount": len(runs),
        "totalKm": round(total_km, 1),
        "avgKmPerWeek": round(total_km / weeks, 1) if weeks > 0 else 0.0,
        "longestKm": round(longest_km, 1),
        "avgPaceSecPerKm": round(statistics.mean(p * 60 for p in paces)) if paces else None,
        "paceStdevMin": round(statistics.pstdev(paces), 2) if len(paces) >= 2 else None,
        "avgHr": round(statistics.mean(hrs)) if hrs else None,
        "hrStdev": round(statistics.pstdev(hrs), 1) if len(hrs) >= 2 else None,
    }


def cycle_profile(activities: list[dict], end_date: date, label: str, weeks_back: int = 16) -> dict:
    """Training profile for the fixed weeks_back window before end_date - a base-fitness
    snapshot, not a live figure. Used for the pre-race/pre-plan baselines, which by design
    never move once that window has passed."""
    start = end_date - timedelta(weeks=weeks_back)
    return _profile_for_window(activities, start, end_date, label, weeks_back)


def current_block_profile(activities: list[dict], plan_start: date, today: date, label: str = "This block so far") -> dict:
    """Training profile for [plan_start, today) - grows day by day as the plan progresses,
    unlike cycle_profile's fixed pre-plan/pre-race windows."""
    weeks = max((today - plan_start).days / 7, 1 / 7)
    return _profile_for_window(activities, plan_start, today, label, weeks)


def summarize(activities: list[dict], plan_start: date, today: date | None = None) -> dict:
    today = today or date.today()
    races = [
        {
            "activityId": a.get("activityId"),
            "date": a.get("startTimeLocal", "")[:10],
            "name": a.get("activityName", ""),
            "km": round(a.get("distance", 0) / 1000, 2),
            "durationSec": a.get("duration", 0),
        }
        for a in activities
        if a.get("distance", 0) >= 40000  # marathon-distance-or-near effort
    ]

    vo2_history = [
        {"date": a.get("startTimeLocal", "")[:10], "vo2": a.get("vO2MaxValue")}
        for a in activities
        if a.get("vO2MaxValue")
    ]

    f5k_candidates = [
        (a.get("startTimeLocal", "")[:10], a.get("fastestSplit_5000"))
        for a in activities
        if a.get("fastestSplit_5000")
    ]
    best_5k = min(f5k_candidates, key=lambda x: x[1]) if f5k_candidates else None

    pre_plan_weeks = []
    for i in range(16, 0, -1):
        week_end = plan_start - timedelta(days=(i - 1) * 7)
        week_start = week_end - timedelta(days=7)
        km = sum(
            a.get("distance", 0)
            for a in activities
            if week_start.isoformat() <= a.get("startTimeLocal", "")[:10] < week_end.isoformat()
        ) / 1000
        pre_plan_weeks.append({"label": f"P-{i}", "start": week_start.isoformat(), "end": week_end.isoformat(), "km": round(km, 1)})

    base_km_12wk = sum(w["km"] for w in pre_plan_weeks[-12:])

    # One comparable profile per marathon cycle: each prior race's 16-week
    # buildup, plus this cycle's 16 weeks going into the plan. Same method,
    # same window size, so they're honestly comparable across 3 years. This
    # is a base-fitness snapshot, not a live number - it stops moving once
    # the plan starts, which is the point (apples-to-apples starting point).
    cycle_profiles = [
        cycle_profile(activities, date.fromisoformat(r["date"]), label=f"{r['date'][:4]} Bangsaen")
        for r in races
    ]
    cycle_profiles.append(cycle_profile(activities, plan_start, label="Base going in"))

    # This one DOES move: it's the plan-to-date window, so it grows as
    # training happens rather than freezing at the day the plan started.
    if today > plan_start:
        cycle_profiles.append(current_block_profile(activities, plan_start, today))

    result = {
        "races": races,
        "vo2History": vo2_history,
        "vo2Current": vo2_history[-1]["vo2"] if vo2_history else None,
        "vo2Peak": max((h["vo2"] for h in vo2_history), default=None),
        "preplanWeekly": pre_plan_weeks,
        "baseKmPerWeek12wk": round(base_km_12wk / 12, 1),
        "bestFiveK": None,
        "riegelPredictionSec": None,
        "cycleProfiles": cycle_profiles,
    }

    if best_5k:
        date_str, sec = best_5k
        predicted = _riegel_predict(sec, 5.0)
        result["bestFiveK"] = {"date": date_str, "sec": sec}
        result["riegelPredictionSec"] = predicted

    return result
