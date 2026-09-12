"""Weight, sleep, and hydration tracking, backed by Garmin Connect's own
records - plus a standalone sweat-rate calculator.

Weight is written through add_body_composition() and hydration through
add_hydration_data(), landing in the same places a synced scale or the
Garmin Connect app's own water-intake widget would - no separate local
database for either. Sleep is read-only (from the watch). Sweat rate has
no Garmin field to read back (its own sweatLossInML stays null on this
account), so it's computed fresh from the day's numbers each time rather
than tracked as a trend - a real gap, not an oversight.
"""

from datetime import date, datetime, timedelta

from garminconnect import Garmin

_HISTORY_DAYS = 180
_SLEEP_DAYS = 30
_SHORT_SLEEP_HOURS = 6.0
_MIN_TREND_SPAN_DAYS = 7  # below this, day-to-day water weight swings swamp any real signal
_AVG_SWEAT_SODIUM_MG_PER_L = 700  # mid-range for a "typical" (not tested) sweater


def log_weight(client: Garmin, weight_kg: float, on_date: str | None = None) -> None:
    timestamp = f"{on_date}T08:00:00" if on_date else None
    client.add_body_composition(timestamp=timestamp, weight=weight_kg)


def fetch_weight_entries(client: Garmin, days: int = _HISTORY_DAYS) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = client.get_body_composition(start.isoformat(), end.isoformat())
    entries = [
        {"date": w["calendarDate"], "weightKg": round(w["weight"] / 1000, 1)}
        for w in data.get("dateWeightList", [])
        if w.get("weight")
    ]
    entries.sort(key=lambda e: e["date"])
    # keep the last entry per calendar date if there were multiple weigh-ins
    by_date = {e["date"]: e for e in entries}
    return sorted(by_date.values(), key=lambda e: e["date"])


def compute_trend(entries: list[dict]) -> dict:
    if not entries:
        return {"latestKg": None, "latestDate": None, "perWeekKg": None, "spanDays": 0}

    latest = entries[-1]
    if len(entries) < 2:
        return {"latestKg": latest["weightKg"], "latestDate": latest["date"], "perWeekKg": None, "spanDays": 0}

    first = entries[0]
    span_days = (datetime.fromisoformat(latest["date"]) - datetime.fromisoformat(first["date"])).days
    if span_days < _MIN_TREND_SPAN_DAYS:
        # Too few days for a weekly rate to mean anything - extrapolating a
        # 1-2 day gap (all water weight, hydration, GI content, time of day)
        # up to a "per week" figure amplifies noise into something that
        # looks like a real trend when it isn't.
        return {"latestKg": latest["weightKg"], "latestDate": latest["date"], "perWeekKg": None, "spanDays": span_days}

    total_change = latest["weightKg"] - first["weightKg"]
    per_week = round(total_change / span_days * 7, 2)
    return {
        "latestKg": latest["weightKg"],
        "latestDate": latest["date"],
        "perWeekKg": per_week,
        "spanDays": span_days,
    }


def fetch_sleep_entries(client: Garmin, days: int = _SLEEP_DAYS) -> list[dict]:
    end = date.today()
    start = end - timedelta(days=days)
    data = client.get_sleep_daily(start.isoformat(), end.isoformat())
    entries = []
    for d in data:
        v = d.get("values", {})
        total_sec = v.get("totalSleepTimeInSeconds")
        if not total_sec:
            continue
        entries.append({
            "date": d["calendarDate"],
            "hours": round(total_sec / 3600, 1),
            "deepMin": round((v.get("deepTime") or 0) / 60),
            "remMin": round((v.get("remTime") or 0) / 60),
            "lightMin": round((v.get("lightTime") or 0) / 60),
            "awakeMin": round((v.get("awakeTime") or 0) / 60),
            "restingHr": v.get("restingHeartRate"),
            "sleepScore": v.get("sleepScore"),
        })
    entries.sort(key=lambda e: e["date"])
    return entries


def compute_sleep_summary(entries: list[dict]) -> dict:
    if not entries:
        return {"avgHours": None, "avgRestingHr": None, "shortNights": 0, "nightsLogged": 0}

    hours = [e["hours"] for e in entries]
    rhrs = [e["restingHr"] for e in entries if e.get("restingHr")]
    short_nights = sum(1 for h in hours if h < _SHORT_SLEEP_HOURS)
    return {
        "avgHours": round(sum(hours) / len(hours), 1),
        "avgRestingHr": round(sum(rhrs) / len(rhrs)) if rhrs else None,
        "shortNights": short_nights,
        "nightsLogged": len(entries),
        "latestHours": entries[-1]["hours"],
        "latestDate": entries[-1]["date"],
    }


def log_hydration(client: Garmin, ml: float, on_date: str | None = None) -> None:
    cdate = on_date or date.today().isoformat()
    client.add_hydration_data(value_in_ml=ml, cdate=cdate)


def fetch_hydration(client: Garmin, on_date: str | None = None) -> dict:
    cdate = on_date or date.today().isoformat()
    data = client.get_hydration_data(cdate)
    return {
        "date": data.get("calendarDate", cdate),
        "valueMl": data.get("valueInML") or 0,
        "goalMl": data.get("goalInML"),
    }


def compute_sweat_rate(pre_kg: float, post_kg: float, fluid_intake_ml: float, duration_min: float) -> dict:
    """Sweat rate from a single pre/post weigh-in around one run.

    sweat loss = weight lost during the run + any fluid drunk during it
    (drinking during the run masks weight loss, so it has to be added back
    to see the true fluid loss). 1kg of body weight lost ~= 1L of sweat.
    """
    if duration_min <= 0:
        raise ValueError("duration must be positive")

    weight_lost_kg = pre_kg - post_kg
    sweat_loss_l = weight_lost_kg + (fluid_intake_ml / 1000)
    sweat_rate_l_per_hr = sweat_loss_l / (duration_min / 60)
    sodium_loss_mg = sweat_loss_l * _AVG_SWEAT_SODIUM_MG_PER_L

    if sweat_rate_l_per_hr < 0:
        tier = "gained"
        guidance = "You gained weight during the run - likely over-drank relative to sweat loss. Ease off fluid intake slightly next time."
    elif sweat_rate_l_per_hr < 0.5:
        tier = "light"
        guidance = "Light sweater. Water is probably enough on most runs; add electrolytes mainly on hot/humid days or runs over 90 minutes."
    elif sweat_rate_l_per_hr < 1.0:
        tier = "moderate"
        guidance = "Moderate sweat rate. On runs over an hour, aim to replace roughly this much fluid, and add an electrolyte tab or drink rather than plain water."
    elif sweat_rate_l_per_hr < 1.5:
        tier = "heavy"
        guidance = "Heavy sweater. Electrolytes matter here, not just water - plain water alone on long runs risks diluting sodium further (hyponatremia risk on very long efforts). Look for a drink mix with 500-700mg sodium/L."
    else:
        tier = "very heavy"
        guidance = "Very heavy sweat rate. Worth deliberately practicing a higher fluid + sodium intake in training before race day - this rate is hard to fully replace mid-run, so pre-loading hydration matters too."

    return {
        "sweatLossL": round(sweat_loss_l, 2),
        "sweatRateLPerHr": round(sweat_rate_l_per_hr, 2),
        "sodiumLossMg": round(sodium_loss_mg),
        "tier": tier,
        "guidance": guidance,
    }
