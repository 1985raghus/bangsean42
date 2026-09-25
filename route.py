"""GPS track for one activity, shaped for drawing.

Garmin's activity details carry a decimated polyline (lat/lon, plus speed and
cumulative distance per point). That is everything needed to draw the route as
a line - no tile server, no API key, no attribution, and it renders in the same
dark palette as the rest of the app.

The points also carry speed, so each segment can be coloured by how fast it was
run: the work intervals of a tempo session light up on the map, which is the one
thing a plain route trace can't tell you.
"""

from garminconnect import Garmin

_MAX_POINTS = 400  # plenty for a smooth line on a phone; keeps the payload small


def _downsample(points: list[dict], limit: int = _MAX_POINTS) -> list[dict]:
    if len(points) <= limit:
        return points
    step = len(points) / limit
    out = [points[int(i * step)] for i in range(limit)]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


_WANT = ("directLatitude", "directLongitude", "sumDistance", "directSpeed", "directHeartRate")


def fetch_route(client: Garmin, activity_id: int) -> dict:
    """{"points": [{"lat","lon","km","paceSecPerKm","hr"}...], "bounds": {...}, "km": float}.

    Read from activityDetailMetrics rather than geoPolylineDTO: the polyline carries
    coordinates but leaves speed and distance null, while the metrics series has
    lat, lon, cumulative distance, speed and heart rate on every sample.
    """
    details = client.get_activity_details(activity_id, maxchart=1200, maxpoly=1200) or {}
    idx = {m["key"]: m["metricsIndex"] for m in details.get("metricDescriptors") or []}
    rows = details.get("activityDetailMetrics") or []
    if not rows or "directLatitude" not in idx or "directLongitude" not in idx:
        return {"available": False, "message": "No GPS track on this activity."}

    def val(metrics, key):
        i = idx.get(key)
        return metrics[i] if i is not None and i < len(metrics) else None

    samples = []
    for row in rows:
        m = row.get("metrics") or []
        lat, lon = val(m, "directLatitude"), val(m, "directLongitude")
        if lat is None or lon is None:
            continue
        speed = val(m, "directSpeed") or 0
        hr = val(m, "directHeartRate")
        samples.append({
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "km": round((val(m, "sumDistance") or 0) / 1000, 3),
            # speed is m/s; below walking pace it is a pause, not a pace
            "paceSecPerKm": round(1000 / speed, 1) if speed and speed > 0.5 else None,
            "hr": round(hr) if hr else None,
        })
    if not samples:
        return {"available": False, "message": "No GPS track on this activity."}

    points = _downsample(samples)
    lats = [p["lat"] for p in points]
    lons = [p["lon"] for p in points]
    return {
        "available": True,
        "activityId": activity_id,
        "points": points,
        "km": samples[-1]["km"],
        "bounds": {"minLat": min(lats), "maxLat": max(lats), "minLon": min(lons), "maxLon": max(lons)},
    }
