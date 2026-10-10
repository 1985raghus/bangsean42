import app as app_module
from app import GateThrottle
from plan_data import HR_CEILINGS, PHASES, PLANNED_RPE


# --- what works without Garmin ------------------------------------------------------------

def test_healthz_is_open(web, logged_out):
    r = web.get("/healthz")
    assert r.status_code == 200 and r.get_json()["ok"] is True


def test_plan_serves_the_coaching_constants(web, logged_out):
    plan = web.get("/api/plan").get_json()
    assert plan["hrCeilings"] == HR_CEILINGS
    assert plan["hrCeilingToleranceBpm"] == 2
    assert plan["phases"] == PHASES
    assert plan["plannedRpe"] == {k: list(v) for k, v in PLANNED_RPE.items()}
    assert set(plan["aerobicKinds"]) == {"easy", "long", "recovery"}
    assert plan["sessions"][0]["week"] == "W1"
    assert all(s["steps"] for s in plan["sessions"])


def test_garmin_endpoints_are_401_until_signed_in(web, logged_out):
    for path in ("/api/progress", "/api/insights", "/api/weight", "/api/mind", "/api/route?activityId=1"):
        assert web.get(path).status_code == 401, path
    assert web.post("/api/weight", json={"weight": 70}).status_code == 401


# --- the shared contract for Garmin-backed endpoints -------------------------------------

def test_upstream_failure_is_a_502_with_the_message(web, logged_in, monkeypatch):
    def boom(client):
        raise RuntimeError("Garmin said no")
    monkeypatch.setattr(app_module, "fetch_weight_entries", boom)
    r = web.get("/api/weight")
    assert r.status_code == 502 and r.get_json()["error"] == "Garmin said no"


def test_progress_renders_with_no_activities_and_reports_cache_age(web, logged_in):
    r = web.get("/api/progress")
    assert r.status_code == 200
    body = r.get_json()
    assert body["prediction"]["available"] is False
    assert body["raceActivity"] is None
    assert body["cacheTtlSec"] == app_module._CACHE_TTL_SECONDS
    assert body["cacheAgeSec"] is not None
    web.get("/api/progress")
    assert logged_in.calls["activities"] == 1  # second call served from cache
    web.get("/api/progress?refresh=1")
    assert logged_in.calls["activities"] == 2


def test_route_requires_an_activity_id(web, logged_in):
    assert web.get("/api/route").status_code == 400
    assert web.get("/api/route?activityId=abc").status_code == 400


# --- input validation: bad input is a 400, never a 500 --------------------------------------

def test_weight_post_rejects_bad_input(web, logged_in):
    for body in ({}, {"weight": "abc"}, {"weight": -1}, {"weight": 0}, {"weight": None}):
        r = web.post("/api/weight", json=body)
        assert r.status_code == 400, body
    assert web.post("/api/weight", json={"weight": 70, "date": "yesterday"}).status_code == 400
    assert logged_in.logged == []


def test_weight_post_logs_to_garmin(web, logged_in):
    r = web.post("/api/weight", json={"weight": "70.4", "date": "2026-09-28"})
    assert r.status_code == 200
    assert logged_in.logged == [("weight", "2026-09-28T08:00:00", 70.4)]


def test_hydration_post_validation_and_logging(web, logged_in):
    assert web.post("/api/hydration", json={"ml": "lots"}).status_code == 400
    assert web.post("/api/hydration", json={"ml": 0}).status_code == 400
    assert web.post("/api/hydration", json={"ml": 250}).status_code == 200
    assert logged_in.logged[0][0] == "hydration" and logged_in.logged[0][2] == 250.0


def test_feel_post_validation(web, logged_in):
    assert web.post("/api/feel", json={"activityId": 1, "rpe": 11, "date": "2026-09-08"}).status_code == 400
    assert web.post("/api/feel", json={"activityId": 1, "rpe": 5, "date": "8 Sep"}).status_code == 400
    assert web.post("/api/feel", json={"rpe": 5}).status_code == 400


def test_sweat_rate_validation(web, logged_in):
    assert web.post("/api/sweat-rate", json={"preKg": 70}).status_code == 400
    assert web.post("/api/sweat-rate", json={"preKg": 70, "postKg": 69, "durationMin": 0}).status_code == 400
    r = web.post("/api/sweat-rate", json={"preKg": 70, "postKg": 69, "durationMin": 60, "fluidMl": 500})
    assert r.status_code == 200 and r.get_json()["sweatRateLPerHr"] == 1.5


# --- the password gate -----------------------------------------------------------------------

def test_gate_disabled_locally(web, logged_out, monkeypatch):
    monkeypatch.setattr(app_module, "_APP_PASSWORD", None)
    assert web.get("/").status_code == 200


def test_gate_locks_everything_but_healthz(web, logged_out, monkeypatch):
    monkeypatch.setattr(app_module, "_APP_PASSWORD", "open-sesame")
    r = web.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/gate")
    assert web.get("/api/session").status_code == 401
    assert web.get("/api/session").get_json() == {"error": "locked"}
    assert web.get("/healthz").status_code == 200
    assert web.get("/gate").status_code == 200


def test_gate_accepts_the_password_and_sets_a_session(web, logged_out, monkeypatch):
    monkeypatch.setattr(app_module, "_APP_PASSWORD", "open-sesame")
    assert web.post("/gate", data={"password": "wrong"}).status_code == 200  # re-rendered with the error
    r = web.post("/gate", data={"password": "open-sesame"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert web.get("/api/session").status_code == 200
    web.post("/gate/logout")
    assert web.get("/api/session").status_code == 401


def test_gate_throttles_after_repeated_failures(web, logged_out, monkeypatch):
    monkeypatch.setattr(app_module, "_APP_PASSWORD", "open-sesame")
    for _ in range(app_module._gate_throttle.free_attempts):
        assert web.post("/gate", data={"password": "nope"}).status_code == 200
    r = web.post("/gate", data={"password": "open-sesame"})  # even the right password waits now
    assert r.status_code == 429 and b"Too many attempts" in r.data
    assert web.get("/api/session").status_code == 401


def test_gate_ignores_surrounding_whitespace(web, logged_out, monkeypatch):
    """A trailing newline on the value in the host's env panel is invisible there.

    Without stripping, the owner types the right password, compare_digest fails
    on the hidden character, and the gate says "Wrong password" forever.
    """
    monkeypatch.setattr(app_module, "_APP_PASSWORD", "open-sesame" + chr(10))
    r = web.post("/gate", data={"password": "  open-sesame "})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert web.get("/api/session").status_code == 200


def test_gate_throttle_backoff_and_reset():
    now = [1000.0]
    t = GateThrottle(free_attempts=2, base_lock_sec=10, max_lock_sec=25, clock=lambda: now[0])
    t.record_failure()
    assert t.seconds_locked() == 0
    t.record_failure()
    assert t.seconds_locked() == 10          # 3rd wrong guess and later lock the gate
    t.record_failure()
    assert t.seconds_locked() == 20          # doubling
    t.record_failure()
    assert t.seconds_locked() == 25          # capped
    now[0] += 25
    assert t.seconds_locked() == 0
    t.reset()
    t.record_failure()
    assert t.seconds_locked() == 0
