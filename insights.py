"""Mistake-detection and improvement-suggestion engine.

Each rule below looks at the current cycle's actual data and, where it
spots the same failure patterns found in the account's two prior Bangsaen
marathons (effort never differentiated, positive-split long efforts), says
so immediately - instead of waiting until race day to find out again.

Returns a flat list of insight dicts, most-severe first:
    {id, severity, title, detail, suggestion, date}
severity is one of "critical" | "warning" | "info" | "good".
"""

import statistics

from garminconnect import Garmin

from health import fetch_sleep_entries
from history import fetch_race_splits, race_pacing_summary
from plan_data import PACES
from progress import fmt_pace, pace_to_sec

_SHORT_SLEEP_HOURS = 6.0

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2, "good": 3}
_EFFORT_WINDOW = 6  # how many recent completed sessions count toward the "same effort every day" check
_TIGHT_PACE_STDEV_MIN = 0.35  # min/km - below this, sessions aren't being differentiated
_LONG_RUN_FADE_THRESHOLD_SEC = 20  # sec/km slower in 2nd half before flagging a fade
_LONG_RUN_MIN_KM = 10  # only check splits on runs long enough for pacing strategy to matter
_DECOUPLING_THRESHOLD_PCT = 5.0  # standard Pa:HR decoupling threshold for "insufficient aerobic durability"


def _completed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] in ("done", "partial") and r["actualPace"] is not None]


def _rule_easy_too_hard(rows: list[dict]) -> list[dict]:
    out = []
    for r in _completed(rows):
        if r["kind"] not in ("easy", "recovery"):
            continue
        _, fast_bound = PACES[r["kind"]]
        fast_sec = pace_to_sec(fast_bound)
        if r["actualPace"] < fast_sec:
            over_by = fast_sec - r["actualPace"]
            out.append({
                "id": f"easy-hard-{r['date']}",
                "severity": "warning",
                "title": f"{r['label']} run faster than its easy zone",
                "detail": (
                    f"{r['actualPaceLabel']}/km actual vs. {fast_bound}/km fastest allowed "
                    f"({over_by:.0f} sec/km over)"
                    + (f", HR {round(r['actualHr'])}bpm" if r.get("actualHr") else "")
                    + "."
                ),
                "suggestion": "Slow down until it feels conversational, even if the pace looks slower than the target. This is the exact pattern behind both prior marathon results.",
                "date": r["actualDate"] or r["date"],
            })
    return out


def _rule_quality_pace_miss(rows: list[dict]) -> list[dict]:
    """Flags quality sessions whose work-interval pace (see progress.refine_quality_pace -
    already isolated from warmup/recovery/cooldown) landed outside the target zone, in
    either direction. Too fast matters as much as too slow: running quality reps hot is how
    the historical "started too fast, faded" pattern shows up in training, not just race day.
    """
    out = []
    for r in _completed(rows):
        if r["kind"] not in ("tempo", "mp"):
            continue
        slow_bound, fast_bound = PACES[r["kind"]]
        slow_sec, fast_sec = pace_to_sec(slow_bound), pace_to_sec(fast_bound)
        work_note = (
            f" ({r.get('workDistanceKm', '?')}km of work-interval laps; whole-activity average was "
            f"{r['wholeActivityPaceLabel']}/km, diluted by warmup/recovery/cooldown)"
            if r.get("wholeActivityPaceLabel") else ""
        )
        if r["actualPace"] > slow_sec:
            under_by = r["actualPace"] - slow_sec
            out.append({
                "id": f"quality-slow-{r['date']}",
                "severity": "info",
                "title": f"{r['label']} came in slower than target",
                "detail": f"{r['actualPaceLabel']}/km vs. {slow_bound}/km slowest allowed ({under_by:.0f} sec/km under){work_note}.",
                "suggestion": "One slow quality session isn't a trend. If it keeps happening, that's the signal to check ACTIVE_PACE_SET against the floor tier.",
                "date": r["actualDate"] or r["date"],
            })
        elif r["actualPace"] < fast_sec:
            over_by = fast_sec - r["actualPace"]
            out.append({
                "id": f"quality-fast-{r['date']}",
                "severity": "warning",
                "title": f"{r['label']} came in faster than target",
                "detail": f"{r['actualPaceLabel']}/km vs. {fast_bound}/km fastest allowed ({over_by:.0f} sec/km over){work_note}.",
                "suggestion": "Running quality reps too hot is the same failure mode as starting a race too fast. Hold the target zone even when it feels easy - that's what it's calibrated for.",
                "date": r["actualDate"] or r["date"],
            })
    return out


def _rule_tight_effort_variation(rows: list[dict]) -> list[dict]:
    recent = [r for r in _completed(rows)][-_EFFORT_WINDOW:]
    if len(recent) < 3:
        return []
    kinds = {r["kind"] for r in recent}
    if len(kinds) < 2:
        return []
    paces_min = [r["actualPace"] / 60 for r in recent]
    spread = statistics.pstdev(paces_min)
    if spread >= _TIGHT_PACE_STDEV_MIN:
        return []
    return [{
        "id": "tight-effort-variation",
        "severity": "critical",
        "title": "Every recent run is at nearly the same effort",
        "detail": (
            f"Last {len(recent)} completed sessions ({', '.join(sorted(kinds))}) span only "
            f"{spread:.2f} min/km of pace variation. Three years of history shows this exact "
            f"signature - it's never gotten better on its own."
        ),
        "suggestion": "Deliberately exaggerate the contrast: make easy days noticeably slower and quality days noticeably faster, even if it feels artificial at first.",
        "date": None,
    }]


def _rule_missed_session(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if r["status"] != "missed":
            continue
        out.append({
            "id": f"missed-{r['date']}",
            "severity": "warning",
            "title": f"{r['label']} missed",
            "detail": f"No matching activity found within a day of {r['date']}.",
            "suggestion": "Don't try to cram it in later or double up - just resume the plan on schedule. One missed session isn't worth compounding with a second mistake.",
            "date": r["date"],
        })
    return out


def _rule_ramp_breach(planned_kpis: list[dict], current_week: str) -> list[dict]:
    try:
        idx = next(i for i, w in enumerate(planned_kpis) if w["week"] == current_week)
    except StopIteration:
        return []
    upcoming = planned_kpis[idx + 1] if idx + 1 < len(planned_kpis) else None
    if not upcoming or not upcoming.get("rampFlag"):
        return []
    return [{
        "id": f"ramp-breach-{upcoming['week']}",
        "severity": "warning",
        "title": f"{upcoming['week']} volume jumps {upcoming['rampPct']:+.0f}%",
        "detail": f"Planned volume goes from this week to {upcoming['plannedKm']}km, a bigger jump than the +20% guardrail.",
        "suggestion": "That's a deliberate choice already made for this plan, not a bug - but it's worth entering that week fresh, not already fatigued.",
        "date": None,
    }]


def _rule_long_run_fade(client: Garmin, rows: list[dict]) -> list[dict]:
    out = []
    for r in _completed(rows):
        if r["kind"] != "long" or not r.get("activityId") or r["actualKm"] < _LONG_RUN_MIN_KM:
            continue
        try:
            splits = fetch_race_splits(client, r["activityId"])
            pacing = race_pacing_summary(splits)
        except Exception:
            continue
        if not pacing.get("available"):
            continue
        fade = pacing["fadeSecPerKm"]
        if fade < _LONG_RUN_FADE_THRESHOLD_SEC:
            continue
        out.append({
            "id": f"long-fade-{r['date']}",
            "severity": "critical" if fade > 40 else "warning",
            "title": f"{r['label']} faded {fade:.0f} sec/km in the second half",
            "detail": (
                f"{fmt_pace(pacing['firstHalfAvgPaceSec'])}/km first half -> "
                f"{fmt_pace(pacing['secondHalfAvgPaceSec'])}/km second half. "
                "This is the exact shape of both prior race-day blowups, showing up in training."
            ),
            "suggestion": "Start this run's early kilometers slower than feels necessary. If it's happening in training too, it will happen on race day.",
            "date": r["actualDate"] or r["date"],
        })
    return out


def _rule_aerobic_decoupling(client: Garmin, rows: list[dict]) -> list[dict]:
    """Catches the subtler failure fade-detection misses: pace holds steady, but heart rate
    climbs to sustain it. The pace chart looks fine; the aerobic system is still drifting.
    """
    out = []
    for r in _completed(rows):
        if r["kind"] != "long" or not r.get("activityId") or r["actualKm"] < _LONG_RUN_MIN_KM:
            continue
        try:
            splits = fetch_race_splits(client, r["activityId"])
            pacing = race_pacing_summary(splits)
        except Exception:
            continue
        if not pacing.get("available") or pacing.get("decouplingPct") is None:
            continue
        decoupling = pacing["decouplingPct"]
        if decoupling < _DECOUPLING_THRESHOLD_PCT:
            continue
        out.append({
            "id": f"decoupling-{r['date']}",
            "severity": "critical" if decoupling > 10 else "warning",
            "title": f"{r['label']} shows {decoupling:.1f}% aerobic decoupling",
            "detail": (
                f"Pace held near {fmt_pace(pacing['firstHalfAvgPaceSec'])}/km both halves, but HR climbed "
                f"{pacing['firstHalfAvgHr']:.0f} -> {pacing['secondHalfAvgHr']:.0f}bpm to hold it. The pace chart "
                "looks fine; the heart rate says the aerobic system hadn't fully adapted to that pace/duration yet."
            ),
            "suggestion": "This is a durability gap, not a pacing mistake - more easy-effort aerobic volume closes it faster than running this pace harder.",
            "date": r["actualDate"] or r["date"],
        })
    return out


def _rule_poor_sleep(client: Garmin, rows: list[dict]) -> list[dict]:
    try:
        sleep_entries = fetch_sleep_entries(client)
    except Exception:
        return []
    sleep_by_date = {e["date"]: e for e in sleep_entries}

    out = []
    for r in _completed(rows):
        night = sleep_by_date.get(r["actualDate"] or r["date"])
        if not night or night["hours"] >= _SHORT_SLEEP_HOURS:
            continue
        context = ""
        if r["kind"] in ("easy", "recovery") and r["actualPace"] is not None:
            fast_bound = pace_to_sec(PACES[r["kind"]][1])
            if r["actualPace"] < fast_bound:
                context = " Likely no coincidence this is also one of the easy-run-too-hard flags - short sleep makes effort control worse."
        out.append({
            "id": f"poor-sleep-{r['date']}",
            "severity": "warning",
            "title": f"Only {night['hours']}h sleep before {r['label']}",
            "detail": f"Resting HR that morning: {night.get('restingHr', '—')}bpm.{context}",
            "suggestion": "Short sleep degrades pace judgment and recovery both - if it's a pattern, protecting sleep will do more for this plan than any single workout will.",
            "date": r["actualDate"] or r["date"],
        })
    return out


def generate_insights(
    client: Garmin,
    rows: list[dict],
    planned_kpis: list[dict],
    current_week: str,
) -> list[dict]:
    insights: list[dict] = []
    insights += _rule_long_run_fade(client, rows)
    insights += _rule_aerobic_decoupling(client, rows)
    insights += _rule_tight_effort_variation(rows)
    insights += _rule_easy_too_hard(rows)
    insights += _rule_poor_sleep(client, rows)
    insights += _rule_missed_session(rows)
    insights += _rule_ramp_breach(planned_kpis, current_week)
    insights += _rule_quality_pace_miss(rows)

    if not insights:
        insights.append({
            "id": "all-clear",
            "severity": "good",
            "title": "Nothing flagged right now",
            "detail": "No effort-mismatch, fade, or ramp issues detected in the current data.",
            "suggestion": "Keep going - this updates as new sessions sync.",
            "date": None,
        })

    insights.sort(key=lambda i: (_SEVERITY_ORDER[i["severity"]], i["date"] or ""))
    return insights
