"""Shared logic for comparing the plan to what actually happened.

Used by both track_progress.py (CLI) and app.py (web app) so the two never
drift - rows are plain dicts here so they serialize straight to JSON.
"""

import csv
import io
from datetime import date, datetime, timedelta

from garminconnect import Garmin

from build_workouts import session_distance_km
from history import fetch_activity_laps, fetch_race_splits, race_pacing_summary
from plan_data import ACTIVE_PACE_SET, FLOOR_TIME_SEC, MARATHON_KM, PACES, PRIMARY_TIME_SEC, STRETCH_TIME_SEC

_FADE_FORECAST_MIN_KM = 10  # only long runs long enough for pacing strategy to matter
_QUALITY_KINDS = ("tempo", "mp")
_RANGE_MIN_SESSIONS = 5  # below this, a "range" is just two points disagreeing - don't dress it up as statistics

_CSV_COLUMNS = [
    "week", "date", "actualDate", "label", "kind", "status",
    "plannedKm", "actualKm", "actualPaceLabel", "inZone", "actualHr",
]

COMPLETION_THRESHOLD = 0.85  # actual/planned distance ratio counted as "done"
DATE_TOLERANCE_DAYS = 1  # a run logged a day early/late still counts, doesn't just show "missed"


def pace_sec_per_km(distance_m: float, duration_s: float) -> float | None:
    if not distance_m or not duration_s:
        return None
    return duration_s / (distance_m / 1000.0)


def fmt_pace(sec_per_km: float | None) -> str:
    if sec_per_km is None:
        return "--:--"
    minutes, seconds = divmod(round(sec_per_km), 60)
    return f"{minutes}:{seconds:02d}"


def fmt_hms(total_sec: float) -> str:
    hours, remainder = divmod(round(total_sec), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def pace_to_sec(pace: str) -> float:
    minutes, seconds = pace.split(":")
    return int(minutes) * 60 + int(seconds)


_AEROBIC_KINDS = ("easy", "long", "recovery")


def _in_zone(kind: str, actual_pace: float | None, slow_bound: str, fast_bound: str) -> bool | None:
    """Was this run at the right effort?

    For quality work both edges matter: too slow misses the stimulus the session
    exists for. For easy, long and recovery running only the fast edge does -
    running slower than the band is how aerobic base is built, especially in
    heat, and marking it "off band" would score the right behaviour as a miss.
    """
    if actual_pace is None:
        return None
    if kind in _AEROBIC_KINDS:
        return actual_pace >= pace_to_sec(fast_bound)
    return pace_to_sec(fast_bound) <= actual_pace <= pace_to_sec(slow_bound)


def target_kind(session: dict) -> str:
    for block in session["blocks"]:
        if block["role"] in ("main", "repeat"):
            return block["kind"]
    return "easy"


def has_mp_effort(session: dict) -> bool:
    return any(block.get("kind") == "mp" for block in session["blocks"])


def fetch_activities_by_date(client: Garmin, start: str, end: str) -> dict[str, dict]:
    """Map calendar date -> that day's largest running activity."""
    activities = client.get_activities_by_date(start, end, activitytype="running")
    by_date: dict[str, dict] = {}
    for activity in activities:
        day = (activity.get("startTimeLocal") or "")[:10]
        if not day:
            continue
        if day not in by_date or activity.get("distance", 0) > by_date[day].get("distance", 0):
            by_date[day] = activity
    return by_date


def _claim_nearby_activity(
    available: dict[str, dict], session_date: date, tolerance_days: int
) -> tuple[dict | None, str | None]:
    """Pop the closest available activity within +/- tolerance_days of session_date.

    Exact date wins first; otherwise the closest offset, trying the earlier
    day before the later one at each offset. Claimed activities are removed
    from `available` so nothing gets matched to two sessions.
    """
    exact = session_date.isoformat()
    if exact in available:
        return available.pop(exact), exact

    for offset in range(1, tolerance_days + 1):
        for candidate_date in (session_date - timedelta(days=offset), session_date + timedelta(days=offset)):
            key = candidate_date.isoformat()
            if key in available:
                return available.pop(key), key

    return None, None


def build_rows(sessions: list[dict], activity_by_date: dict[str, dict], today: date) -> list[dict]:
    """One row per planned session: status, planned/actual distance, pace, zone hit.

    Matches by exact date first; if nothing landed exactly on the planned
    day, checks +/- DATE_TOLERANCE_DAYS (a run logged a day early or late
    still counts, rather than showing as both "missed" and an orphaned,
    invisible activity on the actual day it happened).
    """
    available = dict(activity_by_date)
    rows = []
    for session in sessions:
        session_date = datetime.strptime(session["date"], "%Y-%m-%d").date()
        week = session["name"].split(" ")[0]
        label = session["name"].split(" - ", 1)[1]
        planned_km = round(session_distance_km(session), 1)
        kind = target_kind(session)

        base = {
            "week": week,
            "date": session["date"],
            "actualDate": None,
            "label": label,
            "kind": kind,
            "plannedKm": planned_km,
            "actualKm": None,
            "actualPace": None,
            "actualPaceLabel": None,
            "inZone": None,
            "actualHr": None,
            "activityId": None,
        }

        if session_date > today:
            rows.append({**base, "status": "upcoming"})
            continue

        activity, matched_date = _claim_nearby_activity(available, session_date, DATE_TOLERANCE_DAYS)
        if activity is None:
            status = "today" if session_date == today else "missed"
            rows.append({**base, "status": status})
            continue

        actual_km = round(activity.get("distance", 0) / 1000.0, 1)
        actual_pace = pace_sec_per_km(activity.get("distance", 0), activity.get("duration", 0))
        lo, hi = PACES[kind]
        in_zone = _in_zone(kind, actual_pace, lo, hi)
        status = "done" if planned_km == 0 or actual_km >= planned_km * COMPLETION_THRESHOLD else "partial"
        rows.append({
            **base,
            "status": status,
            "actualDate": matched_date if matched_date != session["date"] else None,
            "actualKm": actual_km,
            "actualPace": actual_pace,
            "actualPaceLabel": fmt_pace(actual_pace),
            "inZone": in_zone,
            "actualHr": activity.get("averageHR"),
            "activityId": activity.get("activityId"),
        })
    return rows


def refine_quality_pace(client: Garmin, rows: list[dict], sessions_by_date: dict[str, dict] | None = None) -> None:
    """Replaces the whole-activity average pace/HR on tempo/MP rows with the pace/HR of just
    the work-interval laps, mutating rows in place.

    Whole-activity average dilutes a quality session with its warmup, recovery jogs, and
    cooldown - a 3x2K MP session's true effort lives only in the work laps. A first version
    of this tried to classify "work" by closeness to the target zone (+/- a small tolerance),
    but that silently dropped reps that ran meaningfully faster than target as "not work" -
    exactly the wrong bias, since a too-fast rep is more concerning than a too-slow one, not
    less. Classifying by distance from EASY pace instead is more robust: warmup/recovery/
    cooldown during a quality session are easy-paced or slower by design, so anything clearly
    faster than that is work effort, whether it undershoots, hits, or overshoots the target.
    Whether the work paced landed IN the target zone is then checked separately, on the
    aggregate work pace only.

    This also feeds compute_prediction() a much more accurate quality-session pace than the
    diluted whole-activity average did before.
    """
    _, easy_fast_bound = PACES["easy"]
    easy_fast_sec = pace_to_sec(easy_fast_bound)
    sessions_by_date = sessions_by_date or {}

    for row in rows:
        if row["status"] not in ("done", "partial") or not row.get("activityId"):
            continue
        session = sessions_by_date.get(row["date"])
        is_quality = row["kind"] in _QUALITY_KINDS
        # A long run with an MP finish carries real marathon-pace evidence too, but its row
        # kind is "long", so the old kind-only gate skipped it - and compute_prediction then
        # read its whole-run average (easy km included) as if it were MP pace.
        is_mp_finish = not is_quality and session is not None and has_mp_effort(session)
        if not (is_quality or is_mp_finish):
            continue
        try:
            laps = fetch_activity_laps(client, row["activityId"])
        except Exception:
            continue
        if not laps:
            continue

        # A work lap is one closer to this session's own target than to easy running:
        # the cutoff sits midway between the target's slow edge and easy's fast edge.
        # (Keying it to easy's fast edge alone broke when the easy band widened - a
        # 6:36/km cool-down then counted as tempo work and dragged the session pace.)
        target_kind_for_laps = "mp" if is_mp_finish else row["kind"]
        target_slow_sec = pace_to_sec(PACES[target_kind_for_laps][0])
        work_cutoff_sec = (target_slow_sec + easy_fast_sec) / 2
        work_laps = [lap for lap in laps if lap["paceSecPerKm"] < work_cutoff_sec]
        if not work_laps:
            continue

        total_distance = sum(lap["distanceM"] for lap in work_laps)
        total_duration = sum(lap["durationSec"] for lap in work_laps)
        work_pace = total_duration / (total_distance / 1000)
        work_km = total_distance / 1000
        hrs = [lap["avgHr"] for lap in work_laps if lap.get("avgHr")]

        if is_mp_finish:
            # Only the finish is MP effort; the run's own pace still describes the whole run,
            # so leave it alone and expose the finish separately for compute_prediction.
            planned_mp_km = sum(b.get("km", 0) for b in session["blocks"] if b.get("kind") == "mp")
            if planned_mp_km and not (0.5 * planned_mp_km <= work_km <= 1.75 * planned_mp_km):
                continue  # couldn't isolate the finish (e.g. one un-lapped run) - better no datapoint than a wrong one
            row["mpEffortPace"] = work_pace
            row["mpEffortPaceLabel"] = fmt_pace(work_pace)
            row["mpEffortKm"] = round(work_km, 2)
            if hrs:
                row["mpEffortHr"] = round(sum(hrs) / len(hrs), 1)
            continue

        slow_bound, fast_bound = PACES[row["kind"]]
        slow_sec, fast_sec = pace_to_sec(slow_bound), pace_to_sec(fast_bound)

        row["wholeActivityPaceLabel"] = row["actualPaceLabel"]
        row["actualPace"] = work_pace
        row["actualPaceLabel"] = fmt_pace(work_pace)
        if hrs:
            row["actualHr"] = round(sum(hrs) / len(hrs), 1)
        row["inZone"] = fast_sec <= work_pace <= slow_sec
        row["workLapCount"] = len(work_laps)
        row["workDistanceKm"] = round(work_km, 2)
        if row["kind"] == "mp":
            row["mpEffortPace"] = work_pace
            row["mpEffortPaceLabel"] = row["actualPaceLabel"]
            row["mpEffortKm"] = round(work_km, 2)


def planned_weekly_kpis(sessions: list[dict]) -> list[dict]:
    """Per-week planned volume, long-run share, and week-over-week ramp rate.

    Static from plan_data.py alone - no Garmin login needed. This is the
    same check that caught the plan's own ramp spikes during a coach review
    (e.g. a >20% week-over-week jump), kept live so a future hand-edit to
    plan_data.py gets flagged automatically instead of slipping through.
    """
    weekly: dict[str, float] = {}
    long_km: dict[str, float] = {}
    order: list[str] = []
    for session in sessions:
        parts = session["name"].split(" ")
        week, weekday = parts[0], parts[1]
        if week not in weekly:
            weekly[week] = 0.0
            order.append(week)
        km = session_distance_km(session)
        weekly[week] += km
        if weekday == "Sun":
            long_km[week] = km

    out = []
    prev_total = None
    for week in order:
        total = round(weekly[week], 1)
        long = round(long_km.get(week, 0.0), 1)
        long_pct = round(100 * long / total, 1) if total else 0.0
        ramp_pct = round(100 * (total / prev_total - 1), 1) if prev_total else None
        out.append({
            "week": week,
            "plannedKm": total,
            "longRunKm": long,
            "longRunPct": long_pct,
            "rampPct": ramp_pct,
            "rampFlag": ramp_pct is not None and ramp_pct > 20,
        })
        prev_total = total
    return out


def pace_compliance_pct(rows: list[dict]) -> float | None:
    """% of completed sessions whose actual pace landed in the target zone."""
    judged = [r for r in rows if r["inZone"] is not None]
    if not judged:
        return None
    hits = sum(1 for r in judged if r["inZone"])
    return round(100 * hits / len(judged), 1)


def avg_hr_by_kind(rows: list[dict]) -> dict[str, float]:
    """Average HR per session kind, for completed sessions with HR data.

    Flags a real coaching concern: if 'easy' HR isn't meaningfully lower
    than 'tempo'/'mp' HR, easy days are probably being run too hard.
    """
    sums: dict[str, list[float]] = {}
    for row in rows:
        if row["actualKm"] is None or not row.get("actualHr"):
            continue
        sums.setdefault(row["kind"], []).append(row["actualHr"])
    return {kind: round(sum(vals) / len(vals), 1) for kind, vals in sums.items()}


def compute_prediction(rows: list[dict], sessions_by_date: dict[str, dict]) -> dict:
    """Estimate marathon finish time from actual MP-effort session paces.

    Uses each qualifying session's whole-activity average pace (MP interval
    workouts, and the MP-finish portion's parent long run), which runs a
    little slower than true rep pace since it includes warmup/cooldown/
    recovery jogs - read the trend, not the exact number.

    The last 3 qualifying sessions are recency-weighted (1x/2x/3x, oldest to
    newest) rather than averaged flat - a session from 2 weeks ago is a
    weaker signal of current fitness than one from yesterday, so it should
    move the estimate less.
    """
    # Only MP-effort pace counts, and it has to be measured the same way every time: the MP
    # reps of an interval session, or the MP finish of a long run - never a whole-run average
    # that mixes 15km of easy running into the number. refine_quality_pace() sets mpEffortPace
    # wherever it could isolate that; a session where it couldn't is left out rather than
    # averaged in diluted, which is what made this estimate meaningless before.
    mp_rows = [row for row in rows if has_mp_effort(sessions_by_date[row["date"]])]
    refined = [(row["date"], row["mpEffortPace"]) for row in mp_rows if row.get("mpEffortPace") is not None]
    diluted = False
    if refined:
        qualifying = sorted(refined, key=lambda item: item[0])
    else:
        # No lap data available (e.g. the CLI, which doesn't fetch laps): fall back to whole-run
        # averages. Consistently diluted is still comparable; mixing the two was the bug.
        qualifying = sorted(
            ((row["date"], row["actualPace"]) for row in mp_rows if row["actualPace"] is not None),
            key=lambda item: item[0],
        )
        diluted = bool(qualifying)

    if not qualifying:
        return {
            "available": False,
            "message": "Not enough data yet - first estimate lands after your first "
            "marathon-pace session or MP-finish long run.",
        }

    # An estimate built from reps run harder than MP quietly assumes a pace only ever held for
    # 5-6km can be held for 42. Count those so the verdict can say so out loud instead of
    # rewarding the exact habit (going out too fast) that this runner is trying to break.
    mp_fast_sec = pace_to_sec(PACES["mp"][1])
    over_mp = [d for d, pace in qualifying if pace < mp_fast_sec]

    recent = qualifying[-3:]
    latest_date, latest_pace = qualifying[-1]
    weights = list(range(1, len(recent) + 1))  # oldest->newest, e.g. [1,2,3]: most recent counts 3x an average
    weighted_recent_pace = sum(w * pace for w, (_, pace) in zip(weights, recent)) / sum(weights)
    latest_predicted = latest_pace * MARATHON_KM
    avg_predicted = weighted_recent_pace * MARATHON_KM

    # A "range" needs enough sessions to mean anything. A standard deviation of 2-3 points is
    # just the gap between them wearing a statistics costume, so below _RANGE_MIN_SESSIONS
    # this stays hidden and the point estimate carries its own plain-language caveat.
    all_predicted_times = [pace * MARATHON_KM for _, pace in qualifying]
    if len(all_predicted_times) >= _RANGE_MIN_SESSIONS:
        range_low, range_high = min(all_predicted_times), max(all_predicted_times)
        predicted_range = {
            "available": True,
            "lowSec": round(range_low),
            "highSec": round(range_high),
            "lowLabel": fmt_hms(range_low),
            "highLabel": fmt_hms(range_high),
            "note": f"Fastest and slowest of your {len(all_predicted_times)} marathon-pace sessions, "
            "extended to race distance - the real spread, not a model.",
        }
    else:
        predicted_range = {
            "available": False,
            "message": f"Needs {_RANGE_MIN_SESSIONS} marathon-pace sessions before a range says anything; "
            f"you have {len(all_predicted_times)}.",
        }

    if avg_predicted <= STRETCH_TIME_SEC * 1.01:
        verdict = "ahead_of_stretch"
        message = "Comfortably inside sub-4:00 territory. That's real evidence for tightening toward the stretch goal - worth a conversation, not an automatic switch."
    elif avg_predicted <= PRIMARY_TIME_SEC * 1.01:
        verdict = "on_track_primary"
        message = "On track for the 4:35:00 primary goal. Keep training at the primary paces."
    elif avg_predicted <= FLOOR_TIME_SEC:
        verdict = "between_primary_and_floor"
        message = "Between primary and the 4:45:00 floor. Still on for a PB - watch the next 2-3 MP sessions before deciding which way to lean."
    else:
        verdict = "behind_floor"
        message = (
            'Trending slower than the 4:45:00 floor. Recommend switching ACTIVE_PACE_SET '
            'to "floor" in plan_data.py to cut injury risk instead of pushing harder.'
        )

    if over_mp:
        message += (
            f" Read it with care: {len(over_mp)} of your {len(qualifying)} marathon-pace sessions ran faster "
            "than the MP band, so this assumes you can hold for 42km a pace you've so far held for a few km."
        )

    return {
        "available": True,
        "sampleSize": len(recent),
        "qualifyingCount": len(qualifying),
        "fasterThanMpCount": len(over_mp),
        "diluted": diluted,  # True only when no lap data was available and whole-run averages were used
        "latestDate": latest_date,
        "latestPredictedSec": latest_predicted,
        "latestPredictedLabel": fmt_hms(latest_predicted),
        "avgPredictedSec": avg_predicted,
        "avgPredictedLabel": fmt_hms(avg_predicted),
        "range": predicted_range,
        "primaryTimeSec": PRIMARY_TIME_SEC,
        "floorTimeSec": FLOOR_TIME_SEC,
        "stretchTimeSec": STRETCH_TIME_SEC,
        "activePaceSet": ACTIVE_PACE_SET,
        "verdict": verdict,
        "message": message,
        "history": [
            {"date": d, "pace": p, "predictedSec": p * MARATHON_KM, "predictedLabel": fmt_hms(p * MARATHON_KM)}
            for d, p in qualifying
        ],
    }


def compute_fade_forecast(
    client: Garmin, rows: list[dict], prediction: dict, sessions_by_date: dict[str, dict] | None = None
) -> dict:
    """Projects this cycle's observed long-run fade pattern onto the marathon prediction.

    compute_prediction() assumes a flat, even pace across the full distance. This applies
    the average second-half fade actually observed on long runs instead, so the prediction
    reads as "start here, drift to here" rather than one number that assumes perfect pacing
    discipline - discipline this runner's history shows isn't a given.
    """
    if not prediction.get("available"):
        return {"available": False, "message": "Needs a race-time prediction first."}

    sessions_by_date = sessions_by_date or {}
    fades = []
    for r in rows:
        if r["kind"] != "long" or r["status"] not in ("done", "partial"):
            continue
        if not r.get("activityId") or (r["actualKm"] or 0) < _FADE_FORECAST_MIN_KM:
            continue
        session = sessions_by_date.get(r["date"])
        if session is not None and has_mp_effort(session):
            # A long run with an MP finish is *built* to negative-split. Counting it as
            # evidence of "no fade" guaranteed a reassuring answer no matter what happened -
            # only steady long runs can say anything about fading.
            continue
        try:
            splits = fetch_race_splits(client, r["activityId"])
            pacing = race_pacing_summary(splits)
        except Exception:
            continue
        if pacing.get("available"):
            fades.append(pacing["fadeSecPerKm"])

    if not fades:
        return {
            "available": False,
            "message": "No steady long runs with splits yet to read a fade pattern from "
            "(long runs with an MP finish don't count - they're built to speed up).",
        }

    avg_fade = sum(fades) / len(fades)
    fade_penalty = max(0.0, avg_fade)  # negative splits don't get rewarded with a faster prediction
    base_pace = prediction["avgPredictedSec"] / MARATHON_KM
    first_half_pace = base_pace - fade_penalty / 2
    second_half_pace = base_pace + fade_penalty / 2
    forecast_sec = first_half_pace * (MARATHON_KM / 2) + second_half_pace * (MARATHON_KM / 2)

    if fade_penalty > 0:
        message = (
            f"Based on {len(fades)} long run(s), you're averaging a {avg_fade:.0f} sec/km fade in the second half. "
            f"Projected onto race day: start ~{fmt_pace(first_half_pace)}/km, finish ~{fmt_pace(second_half_pace)}/km, "
            f"for a fade-adjusted finish of {fmt_hms(forecast_sec)} - {fmt_hms(forecast_sec - prediction['avgPredictedSec'])} "
            "slower than the flat-pace estimate."
        )
    else:
        message = f"Based on {len(fades)} long run(s), no fade pattern showing yet - no penalty applied to the flat-pace estimate."

    return {
        "available": True,
        "sampleSize": len(fades),
        "avgFadeSecPerKm": round(avg_fade, 1),
        "appliedFadePenaltySecPerKm": round(fade_penalty, 1),
        "firstHalfPaceSec": first_half_pace,
        "firstHalfPaceLabel": fmt_pace(first_half_pace),
        "secondHalfPaceSec": second_half_pace,
        "secondHalfPaceLabel": fmt_pace(second_half_pace),
        "forecastFinishSec": forecast_sec,
        "forecastFinishLabel": fmt_hms(forecast_sec),
        "message": message,
    }


def rows_to_csv(rows: list[dict]) -> str:
    """A permanent, plain-text record of the plan vs. actual, independent of the Garmin API
    or this app staying online - the API-dependency risk called out in the code review."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
