"""Garmin's own computed fitness signals: Training Status, VO2max/Fitness
Age, and the personalized acute training-load range (the "load tunnel").

Body Battery, HRV Status, Training Readiness, and Endurance Score are also
native Garmin metrics, but every one of them returns empty on this account
- they require the Elevate 4th-gen HR sensor found on newer devices
(Fenix 7+, Forerunner 255/955+), which this Forerunner 935 (2017) doesn't
have. Rather than build UI around fields that will always be null on this
hardware, this module surfaces only what's actually populated here.
readiness.py's own RHR/sleep/training-load score stays the primary daily
signal - it's the right substitute for what HRV-based recovery can't do
on this watch, not a redundant reinvention of it.

The load tunnel is the one piece worth feeding back into readiness.py:
it's Garmin's own personalized acute-load range, calibrated from years of
this account's history - already populated, vs. this app's own ACWR
component which needs ~10 days of in-cycle data before it'll say
anything. Prefer Garmin's number when it's there.
"""

from datetime import date

from garminconnect import Garmin

# Garmin does not publish this numeric-code-to-label mapping anywhere (not in
# their API docs, not in the garminconnect library, which just passes the raw
# int through). This table was reconstructed from community sources and
# confirmed WRONG for code 7 - this account's Garmin Connect app showed
# "Productive" for a day this code labeled "Unproductive". Fixed below, but
# treat every other entry as an unverified best guess until confirmed the
# same way: cross-check against the Garmin Connect app and report any other
# mismatch.
_TRAINING_STATUS_LABELS = {
    0: "No status",
    1: "Detraining",
    2: "Recovery",
    3: "Maintaining",
    4: "Peaking",
    5: "Overreaching",
    6: "Strained",
    7: "Productive",  # confirmed against this account's Garmin Connect app
}


def fetch_fitness_snapshot(client: Garmin, on_date: str | None = None) -> dict:
    on_date = on_date or date.today().isoformat()
    data = client.get_training_status(on_date)

    vo2 = (data.get("mostRecentVO2Max") or {}).get("generic") or {}
    status_map = (data.get("mostRecentTrainingStatus") or {}).get("latestTrainingStatusData") or {}
    status_entry = next(iter(status_map.values()), {}) if status_map else {}

    status_code = status_entry.get("trainingStatus")
    weekly_load = status_entry.get("weeklyTrainingLoad")
    load_min = status_entry.get("loadTunnelMin")
    load_max = status_entry.get("loadTunnelMax")

    load_verdict = None
    if weekly_load is not None and load_min is not None and load_max is not None:
        if weekly_load < load_min:
            load_verdict = "below_range"
        elif weekly_load > load_max:
            load_verdict = "above_range"
        else:
            load_verdict = "in_range"

    return {
        "available": bool(vo2 or status_entry),
        "vo2Max": vo2.get("vo2MaxValue"),
        "fitnessAge": vo2.get("fitnessAge"),
        "vo2MaxDate": vo2.get("calendarDate"),
        "trainingStatusCode": status_code,
        "trainingStatusLabel": _TRAINING_STATUS_LABELS.get(status_code) if status_code is not None else None,
        "weeklyTrainingLoad": weekly_load,
        "loadTunnelMin": load_min,
        "loadTunnelMax": load_max,
        "loadVerdict": load_verdict,
    }
