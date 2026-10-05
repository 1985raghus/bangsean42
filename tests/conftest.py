"""Shared fixtures: a fake Garmin client and a web app that never touches Garmin.

app.py tries a cached Garmin login the moment it is imported (so gunicorn
restores the session). The tests stub that out before the import, then drive
the session state directly.
"""

import collections
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import garmin_session  # noqa: E402

garmin_session.session.try_cached_login = lambda: False  # never hit the network from a test

import app as app_module  # noqa: E402
import history  # noqa: E402


class FakeGarmin:
    """Just enough of garminconnect.Garmin for the modules under test.

    Every method counts its calls so a test can assert how many times Garmin
    was actually asked for something.
    """

    def __init__(self, activities=None, splits=None, typed_splits=None, sleep=None, hydration=None, details=None):
        self.activities = activities or []
        self.splits = splits or {}
        self.typed_splits = typed_splits or {}
        self.details = details or {}
        self.sleep = sleep or []
        self.hydration = hydration or {}
        self.calls = collections.Counter()
        self.logged = []

    def get_activities_by_date(self, start, end, activitytype=None):
        self.calls["activities"] += 1
        return [a for a in self.activities if start <= a["startTimeLocal"][:10] <= end]

    def get_activity_splits(self, activity_id):
        self.calls["splits"] += 1
        if activity_id not in self.splits:
            raise RuntimeError(f"no splits for {activity_id}")
        return self.splits[activity_id]

    def get_activity_details(self, activity_id, maxchart=None, maxpoly=None):
        self.calls["details"] += 1
        if activity_id not in self.details:
            raise RuntimeError(f"no details for {activity_id}")
        return self.details[activity_id]

    def get_activity_typed_splits(self, activity_id):
        self.calls["typed"] += 1
        return self.typed_splits.get(activity_id, {})

    def get_sleep_daily(self, start, end):
        self.calls["sleep"] += 1
        return self.sleep

    def get_hydration_data(self, cdate):
        self.calls["hydration"] += 1
        return self.hydration

    def add_body_composition(self, timestamp=None, weight=None):
        self.logged.append(("weight", timestamp, weight))

    def add_hydration_data(self, value_in_ml=None, cdate=None):
        self.logged.append(("hydration", cdate, value_in_ml))


def activity(date, km, minutes, hr=None, activity_id=None):
    """A Garmin running activity as the API returns it, from the numbers that matter."""
    return {
        "activityId": activity_id or int(date.replace("-", "")),
        "startTimeLocal": f"{date} 06:00:00",
        "distance": km * 1000.0,
        "duration": minutes * 60.0,
        "averageHR": hr,
    }


def typed(*steps):
    """Typed splits from (type, distance_m, elapsed_s, moving_s, hr) tuples."""
    return {"splits": [
        {"type": t, "distance": d, "duration": el, "elapsedDuration": el, "movingDuration": mv, "averageHR": hr}
        for t, d, el, mv, hr in steps
    ]}


def laps(*legs):
    """lapDTOs from (distance_m, duration_s, hr) tuples."""
    return {"lapDTOs": [{"distance": d, "duration": t, "averageHR": hr} for d, t, hr in legs]}


def stream(*km_seconds, hr=None):
    """An activityDetailMetrics payload: one sample per kilometre boundary.

    Each argument is that kilometre's duration in seconds, so stream(360, 390)
    is a 2 km run whose second kilometre was 30 s slower. The GPS coordinates
    are filler - the splits are computed from distance and elapsed time.
    """
    keys = ["directLatitude", "directLongitude", "sumDistance", "sumElapsedDuration", "directHeartRate"]
    rows, dist, elapsed = [], 0.0, 0.0
    for i, secs in enumerate(km_seconds):
        dist += 1000.0
        elapsed += secs
        beats = hr[i] if hr else None
        rows.append({"metrics": [13.7, 100.5, dist, elapsed, beats]})
    return {
        "metricDescriptors": [{"key": k, "metricsIndex": i} for i, k in enumerate(keys)],
        "activityDetailMetrics": rows,
    }


@pytest.fixture(autouse=True)
def _clean_caches():
    history.clear_activity_cache()
    app_module._cache.clear()
    app_module._gate_throttle.reset()
    yield
    history.clear_activity_cache()
    app_module._cache.clear()


@pytest.fixture
def fake_garmin():
    return FakeGarmin()


@pytest.fixture
def logged_out():
    s = garmin_session.session
    prev = (s.status, s.client)
    s.status, s.client = "logged_out", None
    yield s
    s.status, s.client = prev


@pytest.fixture
def logged_in(fake_garmin):
    s = garmin_session.session
    prev = (s.status, s.client)
    s.status, s.client = "logged_in", fake_garmin
    yield fake_garmin
    s.status, s.client = prev


@pytest.fixture
def web():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()
