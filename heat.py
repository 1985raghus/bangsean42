"""Heat-adjusted marathon goal pace, anchored to this runner's own history.

Bangsaen (Chonburi, Thailand) is coastal and tropical, but this runner
already has two prior finishes there - 26.7C in 2024, 29.8C in 2025 (read
straight from each race's lap-temperature data) - so there's no "cool
weather" baseline to fall back on the way a generic heat/marathon-
performance curve assumes. All of this runner's training and racing
happens in roughly this heat already.

Version 1 of this feature applied a literature-average penalty (e.g. Ely
et al. 2007) as if the fixed goal times (4:00/4:35/4:45) were a measured
"dry" capability, which double-counted heat the runner has already
proven they can handle - the model showed a ~9% penalty at the exact
temperature they'd already raced twice. Fixed by anchoring the penalty
curve at this runner's own demonstrated condition (the midpoint of their
two prior race temperatures) instead of a lab baseline: at that anchor
temperature the adjustment is ~0 by construction, and only a temperature
meaningfully hotter or cooler than what they've already raced produces a
nonzero adjustment.
"""

from progress import fmt_hms

# Midpoint of this runner's two actual Bangsaen race temperatures
# (26.7C in 2024, 29.8C in 2025) - the "I've already done this" anchor,
# not a lab/cool-weather baseline.
ANCHOR_TEMP_C = 28.0
DEFAULT_TEMP_C = 28.0
DEFAULT_HUMIDITY_PCT = 78.0  # typical for the race; lap data has no humidity field to confirm from history

# Temperature (°C) -> pace penalty (fraction) *relative to the anchor*,
# interpolated between points. Shaped after published heat/marathon-
# performance research on the general population, not a precise
# physiological model - used here only for the delta from a point this
# runner has already personally proven, not as an absolute penalty.
_TEMP_PENALTY_TABLE = [
    (10.0, 0.00),
    (15.0, 0.01),
    (20.0, 0.03),
    (25.0, 0.05),
    (28.0, 0.08),
    (32.0, 0.14),
    (36.0, 0.20),
]


def _interp_penalty(temp_c: float) -> float:
    table = _TEMP_PENALTY_TABLE
    if temp_c <= table[0][0]:
        return table[0][1]
    if temp_c >= table[-1][0]:
        return table[-1][1]
    for (t_lo, p_lo), (t_hi, p_hi) in zip(table, table[1:]):
        if t_lo <= temp_c <= t_hi:
            frac = (temp_c - t_lo) / (t_hi - t_lo)
            return p_lo + frac * (p_hi - p_lo)
    return table[-1][1]


def heat_penalty_pct(temp_c: float, humidity_pct: float) -> float:
    """Pace penalty relative to this runner's own proven ANCHOR_TEMP_C, not an absolute lab baseline."""
    delta = _interp_penalty(temp_c) - _interp_penalty(ANCHOR_TEMP_C)
    humidity_factor = 1 + max(-0.3, min(0.5, (humidity_pct - 50) / 100))
    return delta * humidity_factor


def _penalty_tier(penalty_pct: float) -> str:
    if abs(penalty_pct) < 1:
        return f"About the same heat as your own prior Bangsaen finishes (~{ANCHOR_TEMP_C:g}°C) - your goal times already account for this."
    if penalty_pct <= -1:
        return "Cooler than what you've raced before - if it holds, treat your goal times as slightly conservative."
    if penalty_pct < 4:
        return "A bit hotter than your prior two Bangsaen races - small extra margin worth respecting, but close to proven territory."
    if penalty_pct < 8:
        return "Meaningfully hotter than anything you've raced here before - this isn't proven ground, plan extra fluid/electrolytes and don't chase the goal splits."
    return "Well outside the heat you've already handled at Bangsaen - a real risk if the early kilometers are paced to the goal time instead of this one."


def personal_heat_history(races: list[dict]) -> list[dict]:
    """This runner's own race-day temp + finish time - the real evidence, not a formula."""
    out = []
    for race in races:
        temps = [s["temp"] for s in race.get("splits", []) if s.get("temp") is not None]
        if not temps:
            continue
        out.append({
            "date": race["date"],
            "avgTempC": round(sum(temps) / len(temps), 1),
            "finishSec": race["durationSec"],
            "finishLabel": fmt_hms(race["durationSec"]),
        })
    return out


def heat_adjusted_goals(
    goal_times_sec: dict[str, float],
    temp_c: float = DEFAULT_TEMP_C,
    humidity_pct: float = DEFAULT_HUMIDITY_PCT,
    races: list[dict] | None = None,
) -> dict:
    penalty = heat_penalty_pct(temp_c, humidity_pct)
    adjusted_sec = {name: sec * (1 + penalty) for name, sec in goal_times_sec.items()}

    return {
        "tempC": temp_c,
        "humidityPct": humidity_pct,
        "anchorTempC": ANCHOR_TEMP_C,
        "isDefault": temp_c == DEFAULT_TEMP_C and humidity_pct == DEFAULT_HUMIDITY_PCT,
        "penaltyPct": round(penalty * 100, 1),
        "tierMessage": _penalty_tier(penalty * 100),
        "goalTimes": {name: {"sec": sec, "label": fmt_hms(sec)} for name, sec in goal_times_sec.items()},
        "adjustedTimes": {name: {"sec": round(sec), "label": fmt_hms(sec)} for name, sec in adjusted_sec.items()},
        "personalHistory": personal_heat_history(races or []),
    }
