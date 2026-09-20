"""Shoe mileage tracking, backed by Garmin Connect's own Gear feature.

Garmin already sums per-shoe distance from every activity logged against
it (client.get_gear / get_gear_stats) - no separate local ledger needed.
This module adds the one thing Garmin's own UI doesn't: given how many km
are already on a pair and how fast that's accumulating, project forward
to race day - but the verdict criteria differ by role. The race-day pair
gets judged against a narrow "broken in, not worn out" sweet spot; every
other shoe is a training shoe being deliberately run into the ground, so
it's judged against general retirement mileage instead. Applying the
race-day sweet-spot logic to a shoe that was never meant to survive to
race day is the same kind of category error the heat-pace model had
before it was anchored to this runner's own history - conflating two
different baselines.

Also worth flagging explicitly: Garmin's logged distance only counts
activities assigned to that gear in Garmin Connect. A shoe that feels
worn out despite a modest logged total isn't a contradiction - foam also
degrades with age/heat exposure, and unassigned or off-watch use won't
show up here at all. When the runner's own read on a shoe disagrees with
the logged km, trust the runner.
"""

from datetime import date, datetime

from garminconnect import Garmin

# Only two pairs are in this build, by the runner's own choice: Smoke (ASICS
# Superblast 3, bought 2026-09-20) is kept for Bangsaen; Aqua (ASICS Novablast 6)
# takes every training km. Garmin holds older pairs too - they're ignored here so
# the screen says one thing: which shoe today, and will the race pair be ready.
TRACKED_SHOES = {"smoke": "race", "aqua": "training"}

# Typical daily-trainer midsole lifespan before cushioning/energy-return
# degrades meaningfully. Widely-cited range is ~500-800km depending on
# foam compound, runner weight, and surface; not a precise number for any
# one shoe.
RETIREMENT_KM_LOW = 500
RETIREMENT_KM_HIGH = 800

# Narrower window for the specific pair raced in: enough km to confirm
# fit and rule out blisters/hot spots, but well short of full wear so
# peak cushioning is still there for the marathon itself.
RACE_SHOE_MIN_KM = 40
RACE_SHOE_MAX_KM = 250


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value.split("T")[0]).date()


def shoe_role(shoe: dict) -> str | None:
    return TRACKED_SHOES.get((shoe.get("name") or "").strip().lower())


def is_race_shoe(shoe: dict) -> bool:
    return shoe_role(shoe) == "race"


def fetch_shoes(client: Garmin, include_retired: bool = False) -> list[dict]:
    profile = client.get_user_profile()
    profile_id = str(profile.get("id"))
    gear_list = client.get_gear(profile_id)

    out = []
    for g in gear_list:
        if g.get("gearTypeName") != "Shoes":
            continue
        if not include_retired and g.get("gearStatusName") != "active":
            continue
        name = (g.get("displayName") or g.get("customMakeModel") or "Unnamed shoe").strip()
        if name.lower() not in TRACKED_SHOES:
            continue
        stats = client.get_gear_stats(g["uuid"])
        begin = _parse_date(g.get("dateBegin"))
        out.append({
            "uuid": g["uuid"],
            "name": name,
            "model": (g.get("customMakeModel") or "").strip(),
            "role": TRACKED_SHOES[name.lower()],
            "status": g.get("gearStatusName"),
            "beginDate": begin.isoformat() if begin else None,
            "totalKm": round((stats.get("totalDistance") or 0) / 1000, 1),
            "totalActivities": stats.get("totalActivities", 0),
        })
    out.sort(key=lambda s: s["role"] != "race")  # race pair first
    return out


def _race_shoe_verdict(shoe: dict, projected_km: float) -> tuple[str, str]:
    if projected_km > RETIREMENT_KM_HIGH:
        return "over_retirement", (
            f"Projected {projected_km}km by race day is past the typical {RETIREMENT_KM_HIGH}km "
            "retirement point - midsole cushioning will likely be noticeably degraded by then."
        )
    if projected_km > RACE_SHOE_MAX_KM:
        return "past_sweet_spot", (
            f"Projected {projected_km}km by race day is above the ~{RACE_SHOE_MAX_KM}km sweet spot for "
            "a race-day pair - not worn out, but not at peak cushioning either. Keep the rest of training "
            "off these so they stay fresh."
        )
    if shoe["totalKm"] < RACE_SHOE_MIN_KM:
        return "still_breaking_in", (
            f"Only {shoe['totalKm']}km on these so far - get in at least one long run and one MP-effort "
            f"session (past ~{RACE_SHOE_MIN_KM}km total) before trusting them on race day, to rule out "
            "blisters or hot spots under fatigue."
        )
    return "sweet_spot", f"Projected {projected_km}km by race day: broken in, nowhere near worn out. Good spot to be in."


def _training_shoe_verdict(shoe: dict, projected_km: float) -> tuple[str, str]:
    caveat = " (Garmin only counts activities assigned to this gear - if it feels more worn than that, trust how it feels.)"
    if projected_km > RETIREMENT_KM_HIGH or shoe["totalKm"] > RETIREMENT_KM_HIGH:
        return "over_retirement", (
            f"Logged distance ({shoe['totalKm']}km, projected {projected_km}km by race day) is past the "
            f"typical {RETIREMENT_KM_HIGH}km retirement point for a training shoe." + caveat
        )
    if projected_km > RETIREMENT_KM_LOW or shoe["totalKm"] > RETIREMENT_KM_LOW:
        return "nearing_retirement", (
            f"Logged distance ({shoe['totalKm']}km, projected {projected_km}km by race day) is past the "
            f"low end ({RETIREMENT_KM_LOW}km) of typical retirement mileage." + caveat
        )
    return "training_shoe_ok", (
        f"{shoe['totalKm']}km logged, projected {projected_km}km by race day - still under typical "
        "retirement mileage." + caveat
    )


def project_to_race_day(shoe: dict, race_date: date, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    begin = datetime.fromisoformat(shoe["beginDate"]).date() if shoe.get("beginDate") else None
    days_to_race = (race_date - as_of).days

    if not begin or days_to_race < 0:
        return {"available": False}

    days_used = (as_of - begin).days
    if days_used <= 0:
        # Bought today: no usage history to extrapolate, but "0km so far" is itself
        # the answer for a race pair, so say that rather than showing nothing.
        verdict, note = _race_shoe_verdict(shoe, shoe["totalKm"]) if is_race_shoe(shoe) else _training_shoe_verdict(shoe, shoe["totalKm"])
        return {
            "available": True,
            "isRaceShoe": is_race_shoe(shoe),
            "dailyRateKm": 0.0,
            "daysUsed": 0,
            "daysToRace": days_to_race,
            "projectedKmAtRace": shoe["totalKm"],
            "verdict": verdict,
            "note": note,
        }

    daily_rate_km = shoe["totalKm"] / days_used
    projected_km = round(shoe["totalKm"] + daily_rate_km * days_to_race, 1)

    race_shoe = is_race_shoe(shoe)
    verdict, note = _race_shoe_verdict(shoe, projected_km) if race_shoe else _training_shoe_verdict(shoe, projected_km)

    return {
        "available": True,
        "isRaceShoe": race_shoe,
        "dailyRateKm": round(daily_rate_km, 2),
        "daysUsed": days_used,
        "daysToRace": days_to_race,
        "projectedKmAtRace": projected_km,
        "verdict": verdict,
        "note": note,
    }
