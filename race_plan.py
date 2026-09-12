"""Race-day plan: pacing by segment, plus a fuel and fluid timeline.

Pacing: both prior Bangsaen finishes were positive splits from starting too
fast, so the plan builds restraint into the start - the first 5 km run 10 s/km
slower than goal pace, and the remaining distance runs at a goal pace slightly
quicker than the flat average so the finish time still holds.

Fuel: 60-75 g carbs per hour from 30 min in (a gel every 30 min, topped up
with sports drink at aid stations), fluid and sodium scaled to race-morning
heat - and to the runner's own sweat rate once they've measured it, capped
at 800 ml/h because over-drinking is its own risk on a 4-hour run.
"""

from fuel import DEFAULT_WEIGHT_KG, fluid_ml_per_hour, sodium_mg_per_hour
from plan_data import MARATHON_KM
from progress import fmt_hms, fmt_pace

_START_KM = 5.0
_START_EASE_SEC = 10
_GEL_CARBS_G = 25
_GEL_EVERY_MIN = 30
_LAST_GEL_BEFORE_FINISH_MIN = 25
_MAX_FLUID_ML_PER_HOUR = 800


def _goal_pace(goal_sec: float) -> float:
    # 5 km at (p + ease) + the rest at p must add up to the goal time.
    return (goal_sec - _START_KM * _START_EASE_SEC) / MARATHON_KM


def pacing_plan(goal_sec: float) -> dict:
    p = _goal_pace(goal_sec)
    segments = [
        (0.0, _START_KM, p + _START_EASE_SEC,
         "Deliberately slow. The crowd and fresh legs will say go faster - this is exactly where 2024 and 2025 were lost."),
        (_START_KM, 30.0, p,
         "Settle at goal pace. If it feels hard before 20 km, drop 5-10 s/km early rather than hang on."),
        (30.0, MARATHON_KM, p,
         "Hold goal pace. Only press after 35 km, and only if it's genuinely there."),
    ]
    out = []
    elapsed = 0.0
    for start, end, pace, cue in segments:
        elapsed += (end - start) * pace
        out.append({
            "fromKm": start,
            "toKm": round(end, 1),
            "paceSec": pace,
            "paceLabel": fmt_pace(pace),
            "elapsedAtEndSec": elapsed,
            "elapsedAtEndLabel": fmt_hms(elapsed),
            "cue": cue,
        })
    half_sec = _START_KM * (p + _START_EASE_SEC) + (MARATHON_KM / 2 - _START_KM) * p
    return {
        "goalSec": goal_sec,
        "goalLabel": fmt_hms(goal_sec),
        "goalPaceLabel": fmt_pace(p),
        "halfwaySec": half_sec,
        "halfwayLabel": fmt_hms(half_sec),
        "segments": out,
    }


def _km_at(minutes: float, goal_sec: float) -> float:
    p = _goal_pace(goal_sec)
    t = minutes * 60
    start_block = _START_KM * (p + _START_EASE_SEC)
    if t <= start_block:
        return t / (p + _START_EASE_SEC)
    return _START_KM + (t - start_block) / p


def fuel_plan(goal_sec: float, temp_c: float, sweat_rate_l_per_hr: float | None = None) -> dict:
    goal_min = goal_sec / 60
    gels = []
    minute = _GEL_EVERY_MIN
    while minute <= goal_min - _LAST_GEL_BEFORE_FINISH_MIN:
        gels.append({"minute": minute, "km": round(_km_at(minute, goal_sec), 1)})
        minute += _GEL_EVERY_MIN

    if sweat_rate_l_per_hr:
        target = min(_MAX_FLUID_ML_PER_HOUR, max(400, round(sweat_rate_l_per_hr * 1000 * 0.7, -1)))
        fluid = [int(target - 50), int(target + 50)]
        fluid_note = f"From your {sweat_rate_l_per_hr:.2f} L/h sweat test: replace ~70%, not all of it."
    else:
        lo, hi = fluid_ml_per_hour(temp_c)
        fluid = [lo, hi]
        fluid_note = "Estimated for the heat - do a sweat test on a long run to replace this with your own number."

    sodium = list(sodium_mg_per_hour(temp_c))
    if sweat_rate_l_per_hr and sweat_rate_l_per_hr >= 1.2:
        sodium = [700, 1000]

    gel_carbs_per_hour = _GEL_CARBS_G * 60 / _GEL_EVERY_MIN
    return {
        "carbsPerHour": [60, 75],
        "gelCarbsPerHour": round(gel_carbs_per_hour),
        "gels": gels,
        "gelCount": len(gels),
        "fluidMlPerHour": fluid,
        "fluidNote": fluid_note,
        "sodiumMgPerHour": sodium,
        "notes": [
            "Take a gel 10-15 min before the start, with a few sips of water.",
            f"Gels give ~{round(gel_carbs_per_hour)} g/h - top up the rest of the 60-75 g/h with sports drink at aid stations.",
            "Only race with gels you've used on at least two long runs.",
        ],
    }


def race_morning(weight_kg: float = DEFAULT_WEIGHT_KG) -> dict:
    grams = int(round(weight_kg * 2 / 10) * 10)  # ~2 g/kg, 3 h out
    return {
        "carbsG": grams,
        "text": (
            f"About 3 h before the start: ~{grams} g of familiar, low-fibre carbs - jok with a little chicken, "
            "or white toast with honey and a banana. Nothing new on race morning."
        ),
    }


def carb_load(weight_kg: float = DEFAULT_WEIGHT_KG) -> dict:
    lo, hi = int(round(weight_kg * 8 / 10) * 10), int(round(weight_kg * 10 / 10) * 10)
    return {
        "carbsG": [lo, hi],
        "text": (
            f"The two days before the race: {lo}-{hi} g carbs a day. Rice, noodles, bread, bananas and juice at "
            "every meal; ease off vegetables, beans and lentils so your gut is calm on race morning."
        ),
    }
