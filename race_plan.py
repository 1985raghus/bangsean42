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

# The first 2 km are crowded at Bangsaen, so they are planned slow rather than
# fought: +19 s/km costs 38 seconds over the whole race and buys the restraint
# that both previous attempts lacked. The middle banks a few seconds so the last
# 10 km can be the SLOWEST segment - which is what actually happens in the heat -
# instead of requiring a negative split nobody runs at km 35.
_CROWD_KM = 2.0
_CROWD_EASE_SEC = 19
# (from_km, to_km, seconds/km relative to the flat average). The final segment's
# pace is solved so the whole plan lands exactly on the goal time.
_PROFILE = ((0.0, _CROWD_KM, _CROWD_EASE_SEC), (_CROWD_KM, 10.0, -1), (10.0, 21.1, -2), (21.1, 32.0, -2))
_CUES = (
    "Crowded, and planned that way. Let it hold you back - these 38 seconds are the "
    "cheapest insurance in the race. Do not weave.",
    "Settle in. This should feel too easy. Heart rate 165-172, no higher.",
    "Goal pace. Halfway should read about 2:00 - if it reads 1:57 you have already made the 2025 mistake.",
    "Hold. Being passed is fine here; pushing because you feel good is not.",
    "Now it counts. This is where 2024 and 2025 went to 8:00/km. Whatever is left, spend it here.",
)
_GEL_CARBS_G = 25
_GEL_EVERY_MIN = 30
_LAST_GEL_BEFORE_FINISH_MIN = 25
_MAX_FLUID_ML_PER_HOUR = 800


def _goal_pace(goal_sec: float) -> float:
    """The flat average the goal time implies; every segment is set against it."""
    return goal_sec / MARATHON_KM


def _paced_segments(goal_sec: float) -> list[tuple[float, float, float]]:
    """(from_km, to_km, pace_sec_per_km), the last one solved to hit the goal exactly."""
    p = _goal_pace(goal_sec)
    out = [(start, end, p + offset) for start, end, offset in _PROFILE]
    used = sum((end - start) * pace for start, end, pace in out)
    last_start = _PROFILE[-1][1]
    remaining_km = MARATHON_KM - last_start
    out.append((last_start, MARATHON_KM, (goal_sec - used) / remaining_km))
    return out


def _elapsed_at(km: float, goal_sec: float) -> float:
    total = 0.0
    for start, end, pace in _paced_segments(goal_sec):
        if km <= start:
            break
        total += (min(km, end) - start) * pace
    return total


def pacing_plan(goal_sec: float) -> dict:
    segs = _paced_segments(goal_sec)
    out, elapsed = [], 0.0
    for (start, end, pace), cue in zip(segs, _CUES):
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
    half_sec = _elapsed_at(MARATHON_KM / 2, goal_sec)
    return {
        "goalSec": goal_sec,
        "goalLabel": fmt_hms(goal_sec),
        "goalPaceLabel": fmt_pace(_goal_pace(goal_sec)),
        "halfwaySec": half_sec,
        "halfwayLabel": fmt_hms(half_sec),
        "segments": out,
    }


def _km_at(minutes: float, goal_sec: float) -> float:
    """Distance reached at a given minute, walking the same segment profile."""
    t = minutes * 60
    km = 0.0
    for start, end, pace in _paced_segments(goal_sec):
        block = (end - start) * pace
        if t <= block:
            return start + t / pace
        t -= block
        km = end
    return km


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
