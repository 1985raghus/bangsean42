"""Builds JOURNAL.md from everything already logged: Garmin, the app's run
ratings and notes, sleep, stress and the daily check-ins.

Written as a DERIVED file, never a second place to type. The app is the one
inbox - a rating and a note after each run, a check-in each day - and this
turns that into a chronological story with the numbers attached: material for
a weekly Instagram post, and for the race report afterwards.

    python journal.py              # whole block so far -> JOURNAL.md
    python journal.py --week W3    # just that week, printed to the screen

Regenerate it any time; it is overwritten from source data on every run, so
nothing typed into JOURNAL.md by hand survives. Type in the app instead.
"""

import argparse
from datetime import date, datetime

from checkin_store import MOOD_LABELS, MOTIVATION_LABELS, all_checkins
from feel_store import all_feels
from garmin_session import session
from health import compute_sleep_summary, fetch_sleep_entries
from mind import fetch_stress_days
from plan_data import RACE, SESSIONS
from progress import (
    build_rows,
    compute_prediction,
    fetch_activities_by_date,
    fmt_hms,
    refine_quality_pace,
)

KIND_WORD = {"easy": "easy", "long": "long run", "tempo": "tempo", "mp": "marathon pace", "recovery": "recovery"}
PHASE = {
    "W1": "Base", "W2": "Build", "W3": "Build", "W4": "Cutback", "W5": "Build", "W6": "Build",
    "W7": "Peak", "W8": "Peak", "W9": "Taper", "W10": "Taper", "W11": "Race week",
}
MOMENTS_FILE = "MOMENTS.md"


def read_moments() -> dict[str, list[str]]:
    """MOMENTS.md is the runner's own voice, typed in chat and kept by hand.

    Format is just `## YYYY-MM-DD` followed by free text. This file is never
    rewritten by the script - it is the one thing here that isn't derived.
    """
    try:
        with open(MOMENTS_FILE, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return {}
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()[:10]
            out.setdefault(current, [])
        elif current and line.strip():
            out[current].append(line.strip())
    return {k: v for k, v in out.items() if v}


def _gather() -> dict:
    if not session.try_cached_login():
        raise SystemExit(f"Garmin sign-in needed: {session.error}")
    today = date.today()
    plan_start = SESSIONS[0]["date"]
    end = min(today, datetime.strptime(SESSIONS[-1]["date"], "%Y-%m-%d").date())
    activities = fetch_activities_by_date(session.client, plan_start, end.isoformat())
    rows = build_rows(SESSIONS, activities, today)
    sessions_by_date = {s["date"]: s for s in SESSIONS}
    refine_quality_pace(session.client, rows, sessions_by_date)

    def _safe(fn, fallback):
        try:
            return fn()
        except Exception:
            return fallback

    sleep_entries = _safe(lambda: fetch_sleep_entries(session.client), [])
    return {
        "today": today,
        "rows": rows,
        "prediction": compute_prediction(rows, sessions_by_date),
        "feels": _safe(all_feels, {}),
        "checkins": _safe(all_checkins, {}),
        "stress": {d["date"]: d for d in _safe(lambda: fetch_stress_days(session.client, days=30), [])},
        "sleep": {e["date"]: e for e in sleep_entries},
        "sleepSummary": compute_sleep_summary(sleep_entries) if sleep_entries else {},
        "moments": read_moments(),
    }


def _run_lines(row: dict, data: dict) -> list[str]:
    run_date = row.get("actualDate") or row["date"]
    feel = data["feels"].get(str(row.get("activityId")), {})
    bits = [f"{row['actualKm']:.1f} km", f"{row['actualPaceLabel']}/km"]
    if row.get("actualHr"):
        bits.append(f"HR {round(row['actualHr'])}")
    if row.get("mpEffortPaceLabel"):
        bits.append(f"MP portion {row['mpEffortPaceLabel']}/km over {row['mpEffortKm']:.1f} km")
    if feel.get("rpe"):
        bits.append(f"felt {feel['rpe']}/10")
    out = [f"- **{run_date} · {row['label']}** — {' · '.join(bits)}"]
    for said in data["moments"].get(run_date, []):
        out.append(f"  > {said}")
    if feel.get("note"):
        out.append(f"  > {feel['note']}")
    sleep = data["sleep"].get(run_date)
    stress = data["stress"].get(run_date)
    context = []
    if sleep:
        context.append(f"slept {sleep['hours']} h")
    if stress:
        context.append(f"stress {stress['avg']}")
    checkin = data["checkins"].get(run_date)
    if checkin:
        context.append(
            f"mood {MOOD_LABELS.get(checkin['mood'], checkin['mood'])}, "
            f"motivation {MOTIVATION_LABELS.get(checkin['motivation'], checkin['motivation'])}"
        )
    if context:
        out.append(f"  <sub>{' · '.join(context)}</sub>")
    return out


def build(data: dict, only_week: str | None = None) -> str:
    rows = data["rows"]
    days_to_race = (datetime.strptime(RACE["date"], "%Y-%m-%d").date() - data["today"]).days
    done = [r for r in rows if r["status"] in ("done", "partial")]
    km_done = sum(r["actualKm"] or 0 for r in done)
    pred = data["prediction"]

    lines = ["# Road to Bangsaen", ""]
    lines.append(f"Race day **{RACE['date']}** · {days_to_race} days to go · goal **sub-4:00** ({RACE['stretch_pace']})")
    lines.append("")
    lines.append(f"So far: **{len(done)} runs**, **{km_done:.1f} km**"
                 + (f", predicted finish **{pred['avgPredictedLabel']}**" if pred.get("available") else ""))
    lines.append("")
    lines.append(f"<sub>Generated from Garmin and the app's own logs on {data['today']}. Don't edit by hand - it is rebuilt each time.</sub>")
    lines.append("")

    weeks: dict[str, list[dict]] = {}
    for r in rows:
        weeks.setdefault(r["week"], []).append(r)

    for week, week_rows in weeks.items():
        if only_week and week != only_week:
            continue
        week_done = [r for r in week_rows if r["status"] in ("done", "partial")]
        if not week_done:
            continue
        planned = sum(r["plannedKm"] for r in week_rows)
        actual = sum(r["actualKm"] or 0 for r in week_rows)
        missed = [r for r in week_rows if r["status"] == "missed"]
        lines.append(f"## {week} · {PHASE.get(week, '')}")
        lines.append("")
        lines.append(f"{actual:.1f} of {planned:.1f} km · {len(week_done)} of {len(week_rows)} runs"
                     + (f" · missed: {', '.join(r['label'] for r in missed)}" if missed else ""))
        lines.append("")
        for row in week_done:
            lines.extend(_run_lines(row, data))
        # Anything said on a rest day belongs to the week too, not to a run.
        run_dates = {r.get("actualDate") or r["date"] for r in week_done}
        week_dates = {r["date"] for r in week_rows}
        spare = sorted(d for d, said in data["moments"].items() if d in week_dates - run_dates and said)
        for d in spare:
            lines.append(f"- **{d}**")
            for said in data["moments"][d]:
                lines.append(f"  > {said}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build JOURNAL.md from Garmin and the app's logs")
    parser.add_argument("--week", help="only this week (e.g. W3), printed instead of written")
    args = parser.parse_args()

    data = _gather()
    text = build(data, only_week=args.week)
    if args.week:
        print(text)
        return
    with open("JOURNAL.md", "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"JOURNAL.md written - {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
