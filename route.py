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


def _km_splits(samples: list[tuple[float, float, float | None]]) -> list[dict]:
    """Per-kilometre pace and heart rate, computed from the distance/time stream.

    Garmin only records LAPS - what the watch was told to cut, by a structured
    workout's steps or by auto-lap. With auto-lap off, an 8 km easy run arrives as
    one 8 km lap and the kilometre detail looks missing. It isn't: the 1-second
    stream is all there, so the splits are computed here the way Strava does it.
    """
    splits = []
    boundary = 1000.0
    prev_dist, prev_time = 0.0, 0.0
    hrs: list[float] = []
    for dist, t, hr in samples:
        if hr:
            hrs.append(hr)
        while dist >= boundary:
            # interpolate the crossing so a sample straddling the line doesn't skew the split
            span = dist - prev_dist
            frac = (boundary - prev_dist) / span if span > 0 else 0
            cross = prev_time + (t - prev_time) * frac
            start = splits[-1]["atSec"] if splits else 0.0
            splits.append({
                "km": int(boundary / 1000),
                "paceSecPerKm": round(cross - start, 1),
                "hr": round(sum(hrs) / len(hrs)) if hrs else None,
                "atSec": cross,
            })
            hrs = []
            boundary += 1000.0
        prev_dist, prev_time = dist, t
    # the tail: only worth showing if it is a real chunk of a kilometre
    if samples and prev_dist > (boundary - 1000.0) + 200:
        part = (prev_dist - (boundary - 1000.0)) / 1000
        start = splits[-1]["atSec"] if splits else 0.0
        splits.append({
            "km": round(prev_dist / 1000, 2),
            "paceSecPerKm": round((prev_time - start) / part, 1),
            "hr": round(sum(hrs) / len(hrs)) if hrs else None,
            "atSec": prev_time,
            "partial": True,
        })
    for s in splits:
        s.pop("atSec", None)
    return splits



def _continuity(stream: list[tuple]) -> dict | None:
    """How unbroken the running was: walk share, longest continuous stretch, breaks.

    The number this block's training now turns on - the owner's easy runs were
    27% walking before 6 October and nobody could see it in the app. Walking is
    forward motion under 2.0 m/s (slower than 8:20/km), same threshold the
    coaching analysis used.
    """
    walk = run = cur = best = 0.0
    breaks, in_break = 0, False
    for i in range(1, len(stream)):
        dt = stream[i][1] - stream[i - 1][1]
        if dt <= 0 or dt > 60:
            continue
        dd = stream[i][0] - stream[i - 1][0]
        if dd / dt < 2.0:
            walk += dt
            cur = 0.0
            if not in_break:
                breaks += 1
                in_break = True
        else:
            run += dt
            cur += dt
            best = max(best, cur)
            in_break = False
    total = walk + run
    if total < 60:
        return None
    return {"walkPct": round(100 * walk / total), "longestRunMin": round(best / 60), "breaks": breaks}


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

    samples, stream = [], []
    for row in rows:
        m = row.get("metrics") or []
        dist = val(m, "sumDistance")
        # elapsed, not moving time - Strava's splits are elapsed, and a pause at a
        # crossing otherwise makes one kilometre look 15-20 s quicker than it was
        elapsed = val(m, "sumElapsedDuration")
        if elapsed is None:
            elapsed = val(m, "sumDuration")
        if elapsed is None:
            ts = val(m, "directTimestamp")
            elapsed = (ts - (val(rows[0].get("metrics") or [], "directTimestamp") or ts)) / 1000 if ts else None
        if dist is not None and elapsed is not None:
            stream.append((dist, elapsed, val(m, "directHeartRate")))
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
        "splits": _km_splits(stream),
        "continuity": _continuity(stream),
        "bounds": {"minLat": min(lats), "maxLat": max(lats), "minLon": min(lons), "maxLon": max(lons)},
    }
