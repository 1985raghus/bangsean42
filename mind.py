"""The mind half of Body & Mind: stress from the watch, and how the runner says they feel.

Garmin's Forerunner 935 records all-day stress (a 0-100 score derived from
heart-rate variability) but NOT Body Battery - every Body Battery field comes
back empty on this device, so nothing here reads them.

Stress is the honest counterpart to training load: a week where life stress is
high is a week where a hard session costs more than it gives. Paired with the
daily check-in (see checkin_store) it answers "is it my legs or my life?" -
which the pace numbers alone can never say.
"""

from datetime import date, timedelta

from garminconnect import Garmin

_NO_DATA = (None, -1, -2)

# Garmin's own all-day stress bands. Their app calls 26-50 "medium"; for a
# marathon build what matters is the drift against this runner's own baseline,
# not the absolute band, so these are only used for plain-language labels.
_BANDS = ((25, "rest"), (50, "low"), (75, "medium"), (100, "high"))


def _band(avg: float) -> str:
    for ceiling, label in _BANDS:
        if avg <= ceiling:
            return label
    return "high"


def fetch_stress_days(client: Garmin, days: int = 10, end: date | None = None) -> list[dict]:
    """Daily stress averages, oldest first. One call per day - Garmin has no bulk endpoint."""
    end = end or date.today()
    out = []
    for offset in range(days - 1, -1, -1):
        day = end - timedelta(days=offset)
        try:
            summary = client.get_user_summary(day.isoformat())
        except Exception:
            continue
        avg = summary.get("averageStressLevel")
        if avg in _NO_DATA:
            continue
        out.append({
            "date": day.isoformat(),
            "avg": avg,
            "max": summary.get("maxStressLevel"),
            "band": _band(avg),
            "qualifier": (summary.get("stressQualifier") or "").replace("_", " ").lower() or None,
            "highMinutes": round((summary.get("highStressDuration") or 0) / 60),
            "restMinutes": round((summary.get("restStressDuration") or 0) / 60),
        })
    return out


def stress_summary(entries: list[dict]) -> dict:
    """Latest day against the runner's own recent baseline."""
    if not entries:
        return {"available": False, "message": "No stress data from the watch yet."}

    latest = entries[-1]
    earlier = entries[:-1]
    if len(earlier) < 3:
        return {
            "available": True,
            "latest": latest,
            "baselineAvg": None,
            "deltaVsBaseline": None,
            "verdict": "baseline_building",
            "message": f"Stress averaged {latest['avg']} yesterday. A few more days and this compares against your own normal.",
        }

    baseline = round(sum(e["avg"] for e in earlier) / len(earlier), 1)
    delta = round(latest["avg"] - baseline, 1)
    if delta >= 8:
        verdict = "elevated"
        message = (
            f"Stress averaged {latest['avg']}, {delta} above your {baseline} baseline. "
            "A hard session on top of a stressful week costs more than it gives - if today is quality, judge it by effort, not pace."
        )
    elif delta <= -8:
        verdict = "low"
        message = f"Stress averaged {latest['avg']}, {abs(delta)} below your {baseline} baseline. Good day to do the hard one properly."
    else:
        verdict = "normal"
        message = f"Stress averaged {latest['avg']}, about your normal ({baseline})."

    return {
        "available": True,
        "latest": latest,
        "baselineAvg": baseline,
        "deltaVsBaseline": delta,
        "verdict": verdict,
        "message": message,
    }
