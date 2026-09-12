"""Daily fueling guidance, scaled to this runner's weight and today's session.

Replaces a static meal-plan text block with the numbers a sports dietitian
would actually give: a carbohydrate and protein range for the day based on
its training load, plus what to eat before, during and after the run.

Ranges follow the ACSM / ISSN position stands: carbs ~3-5 g/kg on light days
rising to 6-8 g/kg on long-run days; 30-60 g/h during runs of 1-2.5 h and
60+ g/h beyond that; ~1 g/kg carbs plus 20-30 g protein soon after hard or
long sessions. Food ideas stick to what this runner eats - chicken and
vegetarian (eggs and dairy included) - with Thai staples where they fit.
"""

import math

DEFAULT_WEIGHT_KG = 65.0

# day type -> (carbs g/kg low, high, plate guidance)
_DAILY = {
    "rest": (3, 4, "Normal plate: half vegetables, a quarter rice or noodles, a quarter chicken, eggs, tofu or paneer."),
    "easy": (4, 5, "Normal plate with a little more rice than a rest day."),
    "quality": (5, 7, "Carb-forward: rice or noodles at every main meal, not just breakfast."),
    "long": (6, 8, "Big carb day - most of it in the meals after the run, to refill what the run burned."),
}
_PROTEIN_G_PER_KG = (1.4, 1.8)

_KIND_TO_DAY = {"easy": "easy", "recovery": "easy", "tempo": "quality", "mp": "quality", "long": "long"}


def _round10(x: float) -> int:
    return int(round(x / 10.0) * 10)


def day_type(kind: str | None) -> str:
    return _KIND_TO_DAY.get(kind, "rest") if kind else "rest"


def fluid_ml_per_hour(temp_c: float) -> tuple[int, int]:
    if temp_c >= 26:
        return (500, 750)
    if temp_c >= 20:
        return (400, 600)
    return (300, 500)


def sodium_mg_per_hour(temp_c: float) -> tuple[int, int]:
    return (500, 700) if temp_c >= 26 else (300, 500)


def _before(day: str, weight_kg: float) -> str | None:
    if day in ("quality", "long"):
        grams = _round10(weight_kg)  # ~1 g/kg
        return (
            f"1-2 h before: about {grams} g of easy carbs - two slices of white toast with honey and a banana, "
            "or a small bowl of jok (rice porridge). Keep fibre and fat low so it sits well."
        )
    if day == "easy":
        return "Nothing special - run fed or fasted, whichever sits better. A banana helps if it's over an hour."
    return None


def _during(day: str, duration_min: int | None, temp_c: float) -> dict | None:
    if day == "rest" or not duration_min:
        return None
    fluid = fluid_ml_per_hour(temp_c)
    sodium = sodium_mg_per_hour(temp_c)
    if duration_min < 60:
        return {"carbsPerHour": None, "gels": 0, "fluidMlPerHour": fluid, "sodiumMgPerHour": None,
                "text": "Under an hour - water if it's hot, no fuel needed."}
    if duration_min < 90:
        return {"carbsPerHour": [0, 30], "gels": 0, "fluidMlPerHour": fluid, "sodiumMgPerHour": sodium,
                "text": "Fuel is optional at this length. Carry electrolyte drink rather than plain water in the heat."}
    if duration_min <= 150:
        gels = max(1, math.ceil((duration_min - 35) / 40))
        return {"carbsPerHour": [30, 60], "gels": gels, "fluidMlPerHour": fluid, "sodiumMgPerHour": sodium,
                "text": f"{gels} gel{'s' if gels > 1 else ''}: first at ~35 min, then every ~40 min. Use the gels you'll race with."}
    gels = max(1, math.ceil((duration_min - 30) / 30))
    return {"carbsPerHour": [60, 75], "gels": gels, "fluidMlPerHour": fluid, "sodiumMgPerHour": sodium,
            "text": f"{gels} gels: every 30 min from 30 min in - this is race-fueling practice, not optional."}


def _after(day: str, weight_kg: float) -> str | None:
    if day in ("quality", "long"):
        grams = _round10(weight_kg)
        return (
            f"Within an hour: ~{grams} g carbs plus 20-30 g protein - khao man gai (chicken rice), "
            "chocolate milk with a banana, or a tofu / paneer rice bowl."
        )
    if day == "easy":
        return "Your next normal meal is enough - make sure it has 20-30 g protein."
    return None


def day_guidance(weight_kg: float, kind: str | None, duration_min: int | None, temp_c: float = 28.0) -> dict:
    day = day_type(kind)
    lo, hi, plate = _DAILY[day]
    plo, phi = _PROTEIN_G_PER_KG
    return {
        "dayType": day,
        "weightKg": round(weight_kg, 1),
        "carbsG": [_round10(lo * weight_kg), _round10(hi * weight_kg)],
        "proteinG": [_round10(plo * weight_kg), _round10(phi * weight_kg)],
        "plate": plate,
        "before": _before(day, weight_kg),
        "during": _during(day, duration_min, temp_c),
        "after": _after(day, weight_kg),
    }


def all_day_types(weight_kg: float) -> list[dict]:
    """The carb/protein range for every kind of day, for the Body tab's reference table."""
    labels = {"rest": "Rest day", "easy": "Easy run", "quality": "Tempo / MP day", "long": "Long-run day"}
    out = []
    for day, (lo, hi, plate) in _DAILY.items():
        out.append({
            "dayType": day,
            "label": labels[day],
            "carbsG": [_round10(lo * weight_kg), _round10(hi * weight_kg)],
            "plate": plate,
        })
    return out
