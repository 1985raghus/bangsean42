"""End-of-week coach review: what went well, what to work on, what's next.

Reuses the same rows/insights machinery as the daily view, but scoped to
one week and balanced on purpose - insights.py is a mistake-detector by
design, so this module adds the positive side explicitly rather than
leaving the week's review one-sided.
"""

from datetime import datetime

from garminconnect import Garmin

from history import fetch_race_splits, race_pacing_summary
from progress import avg_hr_by_kind, pace_compliance_pct

_FLAT_SPLIT_THRESHOLD_SEC = 5  # fade at/below this counts as a genuinely even long run
_LONG_RUN_MIN_KM = 10


def _week_rows(all_rows: list[dict], week: str) -> list[dict]:
    return [r for r in all_rows if r["week"] == week]


def _completed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] in ("done", "partial")]


def _fmt_pace_sec(sec: float) -> str:
    m, s = divmod(round(sec), 60)
    return f"{m}:{s:02d}"


def generate_positives(client: Garmin, week_rows: list[dict], prev_week_rows: list[dict] | None) -> list[dict]:
    positives = []
    completed = _completed(week_rows)
    due = [r for r in week_rows if r["status"] != "upcoming"]

    if due and len(completed) == len(due):
        positives.append({
            "id": "perfect-adherence",
            "title": "Every session this week done",
            "detail": f"{len(completed)}/{len(due)} planned sessions completed, nothing missed.",
        })

    this_compliance = pace_compliance_pct(week_rows)
    prev_compliance = pace_compliance_pct(prev_week_rows) if prev_week_rows else None
    if this_compliance is not None:
        if prev_compliance is not None and this_compliance > prev_compliance:
            positives.append({
                "id": "compliance-improved",
                "title": f"Pace-zone compliance up to {this_compliance}%",
                "detail": f"Up from {prev_compliance}% last week - real, measurable improvement in hitting target paces.",
            })
        elif this_compliance >= 50 and prev_compliance is None:
            positives.append({
                "id": "compliance-solid",
                "title": f"Pace-zone compliance at {this_compliance}%",
                "detail": "Half or more of completed sessions landed in their target zone.",
            })

    for r in completed:
        if r["kind"] != "long" or not r.get("activityId") or (r["actualKm"] or 0) < _LONG_RUN_MIN_KM:
            continue
        try:
            splits = fetch_race_splits(client, r["activityId"])
            pacing = race_pacing_summary(splits)
        except Exception:
            continue
        if not pacing.get("available"):
            continue
        fade = pacing["fadeSecPerKm"]
        if fade <= _FLAT_SPLIT_THRESHOLD_SEC:
            direction = "a negative split" if fade < 0 else "a dead-even pace"
            positives.append({
                "id": f"long-run-pacing-{r['date']}",
                "title": f"{r['label']} held {direction}",
                "detail": (
                    f"{_fmt_pace_sec(pacing['firstHalfAvgPaceSec'])}/km first half -> "
                    f"{_fmt_pace_sec(pacing['secondHalfAvgPaceSec'])}/km second half. "
                    "This is the opposite of both prior marathons' pacing - real evidence you can hold an even effort."
                ),
            })

    if not positives:
        positives.append({
            "id": "no-positives-yet",
            "title": "Nothing to highlight yet this week",
            "detail": "Once a few more sessions sync, this fills in - it isn't scored down for a slow start.",
        })

    return positives


def generate_lookahead(next_week_rows: list[dict], hr_by_kind: dict[str, float]) -> dict:
    if not next_week_rows:
        return {"week": None, "sessions": [], "note": "This is the last week of the plan."}

    week = next_week_rows[0]["week"]
    sessions = [
        {
            "date": r["date"],
            "day": datetime.strptime(r["date"], "%Y-%m-%d").strftime("%a"),
            "label": r["label"],
            "kind": r["kind"],
            "plannedKm": r["plannedKm"],
        }
        for r in next_week_rows
    ]

    has_quality = any(s["kind"] in ("tempo", "mp") for s in sessions)
    easy_hr, hard_hr = hr_by_kind.get("easy"), hr_by_kind.get("tempo", hr_by_kind.get("mp"))
    if has_quality and easy_hr is not None and hard_hr is not None and hard_hr - easy_hr < 10:
        note = (
            "Same easy-vs-hard HR gap as this week - the priority for "
            f"{week} is keeping the easy days genuinely easy so the quality session actually gets fresher legs."
        )
    elif has_quality:
        note = f"{week} has a real quality session again - carry this week's pacing discipline straight into it."
    else:
        note = f"{week} is lighter on quality work - a good week to bank the easy-day discipline before the next hard block."

    return {"week": week, "sessions": sessions, "note": note}


def generate_week_review(
    client: Garmin,
    all_rows: list[dict],
    week: str,
    week_order: list[str],
) -> dict:
    week_rows = _week_rows(all_rows, week)
    idx = week_order.index(week) if week in week_order else -1
    prev_week_rows = _week_rows(all_rows, week_order[idx - 1]) if idx > 0 else None
    next_week_rows = _week_rows(all_rows, week_order[idx + 1]) if 0 <= idx < len(week_order) - 1 else []

    completed = _completed(week_rows)
    due = [r for r in week_rows if r["status"] != "upcoming"]
    total_planned = round(sum(r["plannedKm"] for r in week_rows), 1)
    total_actual = round(sum(r["actualKm"] or 0 for r in completed), 1)

    return {
        "week": week,
        "dateRange": {"start": week_rows[0]["date"], "end": week_rows[-1]["date"]} if week_rows else None,
        "sessionsDue": len(due),
        "sessionsCompleted": len(completed),
        "totalPlannedKm": total_planned,
        "totalActualKm": total_actual,
        "paceCompliancePct": pace_compliance_pct(week_rows),
        "avgHrByKind": avg_hr_by_kind(week_rows),
        "positives": generate_positives(client, week_rows, prev_week_rows),
        "lookAhead": generate_lookahead(next_week_rows, avg_hr_by_kind(week_rows)),
    }
