"""Compare planned sessions against what actually synced from the watch.

Pulls running activities from Garmin Connect for the plan's date range and
matches them to SESSIONS by calendar date, so you can see adherence
(completed/missed) and a race-time prediction. Shares its logic with
app.py (the web dashboard) via progress.py.
"""

import argparse
from datetime import date, datetime

from insights import generate_insights
from plan_data import SESSIONS
from progress import (
    avg_hr_by_kind,
    build_rows,
    compute_prediction,
    fetch_activities_by_date,
    pace_compliance_pct,
    planned_weekly_kpis,
)
from push_to_garmin import login


def main() -> None:
    parser = argparse.ArgumentParser(description="Track adherence to the marathon plan")
    parser.add_argument("--week", help="Only show one week, e.g. W7")
    args = parser.parse_args()

    today = date.today()
    plan_start = SESSIONS[0]["date"]
    plan_end_date = datetime.strptime(SESSIONS[-1]["date"], "%Y-%m-%d").date()
    fetch_end = min(today, plan_end_date)

    client = login()
    activity_by_date = {}
    if fetch_end >= datetime.strptime(plan_start, "%Y-%m-%d").date():
        activity_by_date = fetch_activities_by_date(client, plan_start, fetch_end.isoformat())

    all_rows = build_rows(SESSIONS, activity_by_date, today)
    sessions_by_date = {s["date"]: s for s in SESSIONS}
    rows = [r for r in all_rows if r["week"] == args.week] if args.week else all_rows

    due = [r for r in rows if r["status"] != "upcoming"]
    completed = sum(1 for r in due if r["status"] in ("done", "partial"))

    print(f"Bangsaen Marathon plan - progress as of {today.isoformat()}")
    if due:
        print(f"{completed}/{len(due)} due sessions completed\n")
    else:
        print("No sessions due yet.\n")

    current_week = None
    for row in rows:
        if row["week"] != current_week:
            print(f"\n{row['week']}")
            current_week = row["week"]
        if row["actualKm"] is None:
            status_label = {"upcoming": "upcoming", "today": "today, not yet synced", "missed": "MISSED"}[row["status"]]
            print(f"  {row['label']:45s} planned {row['plannedKm']:5.1f}K   [{status_label}]")
        else:
            zone_note = "" if row["inZone"] else "  (off target pace)"
            date_note = f"  (ran {row['actualDate']})" if row["actualDate"] else ""
            print(
                f"  {row['label']:45s} planned {row['plannedKm']:5.1f}K -> actual {row['actualKm']:5.1f}K "
                f"@ {row['actualPaceLabel']}/km   [{row['status']}]{zone_note}{date_note}"
            )

    prediction = compute_prediction(all_rows, sessions_by_date)
    print("\n" + "-" * 60)
    print("RACE-TIME PREDICTION")
    if not prediction["available"]:
        print(prediction["message"])
    else:
        print(f"Based on {prediction['sampleSize']} recent MP-effort session(s), latest: {prediction['latestDate']}")
        print(f"  latest session pace  -> predicted {prediction['latestPredictedLabel']}")
        print(f"  avg of last {prediction['sampleSize']}         -> predicted {prediction['avgPredictedLabel']}")
        print(f"  primary 4:35:00  |  floor 4:45:00  |  stretch 4:00:00  |  active pace set: {prediction['activePaceSet']}")
        print(f"\n-> {prediction['message']}")

    print("\n" + "-" * 60)
    print("COACH KPIs")
    current_week = next((r["week"] for r in reversed(all_rows) if r["status"] != "upcoming"), all_rows[0]["week"])
    weekly_kpis = planned_weekly_kpis(SESSIONS)
    week_kpi = next((w for w in weekly_kpis if w["week"] == current_week), None)
    if week_kpi:
        ramp = week_kpi["rampPct"]
        ramp_str = f"{ramp:+.1f}%" if ramp is not None else "n/a (first week)"
        flag = "  <-- above +20% guardrail" if week_kpi["rampFlag"] else ""
        print(f"{current_week} ramp rate: {ramp_str}{flag}")
        print(f"{current_week} long-run share: {week_kpi['longRunPct']}% of that week's volume")

    compliance = pace_compliance_pct(all_rows)
    print(f"Pace-zone compliance: {compliance}% of completed sessions" if compliance is not None else "Pace-zone compliance: no completed sessions yet")

    hr_by_kind = avg_hr_by_kind(all_rows)
    if hr_by_kind:
        print("Avg HR by session type: " + ", ".join(f"{k} {v}bpm" for k, v in hr_by_kind.items()))
        easy, hard = hr_by_kind.get("easy"), hr_by_kind.get("tempo", hr_by_kind.get("mp"))
        if easy is not None and hard is not None and hard - easy < 10:
            print(f"  -> only {round(hard - easy)}bpm between easy and hard efforts - easy days may be run too hard.")
    else:
        print("Avg HR by session type: no HR data on completed sessions yet")

    print("\n" + "-" * 60)
    print("COACH INSIGHTS")
    for insight in generate_insights(client, all_rows, weekly_kpis, current_week):
        print(f"[{insight['severity'].upper()}] {insight['title']}")
        print(f"  {insight['detail']}")
        print(f"  -> {insight['suggestion']}")


if __name__ == "__main__":
    main()
