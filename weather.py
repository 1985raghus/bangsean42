"""Live conditions, turned into coaching.

Bangkok training happens in real heat, and this runner is governed by heart
rate on easy days - so the useful thing a weather feed does here is set the
*pace expectation* for today's conditions (so 7:12/km at HR 161 reads as
"correct for 33C", not "slow"), point at the coolest window to run, and warn
when a quality session would be cooked by the heat before the legs give out.

Source: Open-Meteo (open-meteo.com) - free, no API key, CORS-open. Its
`apparent_temperature` already folds in humidity, wind and sun, which is
exactly the number that matters for how hard a run will feel. Every network
call is wrapped so a weather outage can never take the app down: the feature
simply reports unavailable and the rest of the screen is unaffected.
"""

import json
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Where the running happens. Training is Bangkok; the race is Bangsaen (Chonburi).
PLACES = {
    "bangkok": {"lat": 13.75, "lon": 100.50, "label": "Bangkok", "tz": "Asia/Bangkok"},
    "bangsaen": {"lat": 13.28, "lon": 100.92, "label": "Bangsaen", "tz": "Asia/Bangkok"},
}
DEFAULT_PLACE = "bangkok"

# Easy pace runs slower in heat at the same heart rate. Seconds per km added
# vs a comfortable ~24C reference, scaled by APPARENT temperature (humidity is
# already baked into that). Calibrated against this runner's own data - he held
# ~6:45/km at HR 160 on a cool dawn and ~7:12 at HR 161 in 33C afternoon sun,
# roughly +18-22 s/km - so ~1.8 s/km per degree over 24, capped so it stays a
# guide not a promise.
_COMFORT_C = 24.0
_SEC_PER_DEG = 1.8
_MAX_OFFSET = 45


def easy_pace_offset_sec(apparent_c: float) -> int:
    """Seconds/km to expect added to easy pace at the same HR, vs a cool day."""
    return int(min(_MAX_OFFSET, max(0.0, (apparent_c - _COMFORT_C) * _SEC_PER_DEG)))


def heat_tier(apparent_c: float) -> dict:
    """A band + a one-line instruction, keyed off feels-like temperature."""
    if apparent_c < 27:
        return {"tier": "comfortable", "note": "Good conditions. Run to the plan."}
    if apparent_c < 32:
        return {"tier": "warm", "note": "Warm. Easy pace drifts slower at the same effort - let it."}
    if apparent_c < 37:
        return {"tier": "hot", "note": "Hot. Govern by heart rate, not pace, and carry fluid. Move hard sessions to the cool window."}
    return {"tier": "extreme", "note": "Extreme. Easy only, or wait for the cool window. Hydration and salt are not optional."}


def coolest_window(times: list[str], apparent: list[float], from_iso: str, hours_ahead: int = 20) -> dict | None:
    """The coolest upcoming hour within the horizon, for 'when should I run'."""
    best = None
    try:
        horizon = (datetime.fromisoformat(from_iso) + timedelta(hours=hours_ahead)).isoformat()
    except Exception:
        horizon = "9999"
    for t, a in zip(times, apparent):
        if a is None or t < from_iso:
            continue
        if t > horizon:
            break
        if best is None or a < best[1]:
            best = (t, a)
    if not best:
        return None
    return {"at": best[0], "hour": best[0][11:16], "apparentC": round(best[1], 1)}


def fetch_weather(place: str = DEFAULT_PLACE, _opener=urllib.request.urlopen) -> dict:
    p = PLACES.get(place, PLACES[DEFAULT_PLACE])
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={p['lat']}&longitude={p['lon']}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code"
        "&hourly=temperature_2m,apparent_temperature"
        f"&timezone={p['tz']}&forecast_days=2"
    )
    try:
        with _opener(url, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"available": False, "message": f"weather unavailable: {exc}"}
    return summarize(raw, p)


def summarize(raw: dict, place: dict) -> dict:
    cur = raw.get("current") or {}
    hourly = raw.get("hourly") or {}
    temp = cur.get("temperature_2m")
    humidity = cur.get("relative_humidity_2m")
    apparent = cur.get("apparent_temperature")
    if apparent is None or temp is None:
        return {"available": False, "message": "weather fields missing"}

    now_iso = cur.get("time") or datetime.now(ZoneInfo(place["tz"])).isoformat()[:16]
    window = coolest_window(hourly.get("time") or [], hourly.get("apparent_temperature") or [], now_iso)
    tier = heat_tier(apparent)
    offset = easy_pace_offset_sec(apparent)

    return {
        "available": True,
        "place": place["label"],
        "tempC": round(temp, 1),
        "apparentC": round(apparent, 1),
        "humidityPct": round(humidity) if humidity is not None else None,
        "tier": tier["tier"],
        "note": tier["note"],
        "easyPaceOffsetSec": offset,
        "coolWindow": window,
        "asOf": now_iso,
    }
