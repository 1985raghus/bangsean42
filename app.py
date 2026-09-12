"""Local web app: Garmin Connect login, live progress, and training analysis.

Runs on your machine only (127.0.0.1) by default - it holds your Garmin
session in memory and reads/writes garminconnect's own token cache on disk,
but never stores your password anywhere. Start it with:

    python app.py

then open http://127.0.0.1:5000

Set APP_PASSWORD to enable the login gate below (required once this is
reachable from anywhere other than localhost - see the deploy notes in
README.md). Leave it unset for local use and nothing changes.
"""

import hmac
import os
import secrets
import time
from datetime import date, datetime

from flask import Flask, Response, jsonify, redirect, render_template, request
from flask import session as flask_session

from build_workouts import session_distance_km, session_duration_min, session_steps
from feel_store import all_feels, friendly_error, save_feel
from fuel import DEFAULT_WEIGHT_KG, all_day_types, day_guidance
from garmin_session import session
from race_plan import carb_load, fuel_plan, pacing_plan, race_morning
from token_store import sync_if_changed
from health import (
    compute_sleep_summary,
    compute_sweat_rate,
    compute_trend,
    fetch_hydration,
    fetch_sleep_entries,
    fetch_weight_entries,
    log_hydration,
    log_weight,
)
from fitness_snapshot import fetch_fitness_snapshot
from gear import fetch_shoes, project_to_race_day
from heat import DEFAULT_HUMIDITY_PCT, DEFAULT_TEMP_C, heat_adjusted_goals, heat_penalty_pct
from history import fetch_history, fetch_race_splits, race_pacing_summary, summarize
from insights import generate_insights
from plan_data import ACTIVE_PACE_SET, FLOOR_TIME_SEC, PACES, PRIMARY_TIME_SEC, RACE, SESSIONS, STRETCH_TIME_SEC
from readiness import compute_readiness, pivot_suggestion
from weekly_summary import generate_week_review
from progress import (
    avg_hr_by_kind,
    build_rows,
    compute_fade_forecast,
    compute_prediction,
    fetch_activities_by_date,
    pace_compliance_pct,
    planned_weekly_kpis,
    refine_quality_pace,
    rows_to_csv,
)

# Cloud hosts run on UTC, 7 hours behind the runner - before 7am Bangkok time
# the server would think it's still yesterday and serve the wrong day's session.
if hasattr(time, "tzset"):  # Unix only; a local Windows run already uses the machine's own clock
    os.environ.setdefault("TZ", "Asia/Bangkok")
    time.tzset()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

_APP_PASSWORD = os.environ.get("APP_PASSWORD")  # unset = local trusted-machine mode, gate disabled


@app.before_request
def _require_app_password():
    if not _APP_PASSWORD:
        return None  # gate disabled - unchanged local behavior
    if request.endpoint in ("login_gate", "login_gate_post", "static", "healthz"):
        return None
    if flask_session.get("authed"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "locked"}), 401  # fetch() can't follow a redirect to an HTML page usefully
    return redirect("/gate")


@app.before_request
def _sync_garmin_token():
    if session.status == "logged_in":
        sync_if_changed()  # no-op locally; keeps the Supabase copy current when deployed


@app.post("/gate/logout")
def gate_logout():
    flask_session.clear()
    return redirect("/gate")


@app.get("/healthz")
def healthz():
    # For an uptime pinger: keeps a free-tier host from sleeping (and showing its
    # own wake-up page). No password, no Garmin call, nothing private in the reply.
    return jsonify({"ok": True})


@app.get("/gate")
def login_gate():
    return render_template("gate.html", error=None)


@app.post("/gate")
def login_gate_post():
    entered = request.form.get("password", "")
    if _APP_PASSWORD and hmac.compare_digest(entered, _APP_PASSWORD):
        flask_session["authed"] = True
        flask_session.permanent = True
        return redirect("/")
    return render_template("gate.html", error="Wrong password")


_CACHE_TTL_SECONDS = 300
_HISTORY_CACHE_TTL_SECONDS = 3600  # history barely changes minute to minute
_cache: dict[str, tuple[float, dict]] = {}


def _cached(key: str, build, ttl: int = _CACHE_TTL_SECONDS, force: bool = False):
    now = time.time()
    hit = _cache.get(key)
    if not force and hit and now - hit[0] < ttl:
        return hit[1]
    value = build()
    _cache[key] = (now, value)
    return value


def _cache_age(key: str) -> float | None:
    hit = _cache.get(key)
    return round(time.time() - hit[0], 1) if hit else None


def _plan_start_end() -> tuple[str, date]:
    plan_start = SESSIONS[0]["date"]
    plan_end_date = datetime.strptime(SESSIONS[-1]["date"], "%Y-%m-%d").date()
    return plan_start, plan_end_date


def _fetch_progress() -> dict:
    plan_start, plan_end_date = _plan_start_end()
    today = date.today()
    fetch_end = min(today, plan_end_date)

    activity_by_date = {}
    if fetch_end >= datetime.strptime(plan_start, "%Y-%m-%d").date():
        activity_by_date = fetch_activities_by_date(session.client, plan_start, fetch_end.isoformat())

    rows = build_rows(SESSIONS, activity_by_date, today)
    refine_quality_pace(session.client, rows)
    sessions_by_date = {s["date"]: s for s in SESSIONS}
    prediction = compute_prediction(rows, sessions_by_date)
    prediction["fadeForecast"] = compute_fade_forecast(session.client, rows, prediction)

    due = [r for r in rows if r["status"] != "upcoming"]
    completed = sum(1 for r in due if r["status"] in ("done", "partial"))

    return {
        "today": today.isoformat(),
        "dueCount": len(due),
        "completedCount": completed,
        "rows": rows,
        "prediction": prediction,
    }


def _current_week(rows: list[dict]) -> str:
    due = [r for r in rows if r["status"] != "upcoming"]
    return due[-1]["week"] if due else rows[0]["week"]


def _fetch_insights() -> list[dict]:
    progress = _cached("progress", _fetch_progress)
    kpis = planned_weekly_kpis(SESSIONS)
    return generate_insights(session.client, progress["rows"], kpis, _current_week(progress["rows"]))


def _week_order(rows: list[dict]) -> list[str]:
    order = []
    for r in rows:
        if r["week"] not in order:
            order.append(r["week"])
    return order


def _fetch_weekly_review(week: str) -> dict:
    progress = _cached("progress", _fetch_progress)
    return generate_week_review(session.client, progress["rows"], week, _week_order(progress["rows"]))


def _fetch_history_summary() -> dict:
    plan_start_date = datetime.strptime(SESSIONS[0]["date"], "%Y-%m-%d").date()
    activities = fetch_history(session.client, plan_start_date)
    summary = summarize(activities, plan_start_date)

    for race in summary["races"]:
        splits = fetch_race_splits(session.client, race["activityId"]) if race.get("activityId") else []
        race["splits"] = splits
        race["pacing"] = race_pacing_summary(splits)

    return summary


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/session")
def api_session():
    return jsonify({**session.state(), "gateEnabled": bool(_APP_PASSWORD)})


@app.post("/api/login")
def api_login():
    data = request.get_json(force=True) or {}
    email, password = data.get("email"), data.get("password")
    if not email or not password:
        return jsonify({"error": "email and password are required"}), 400
    session.start_login(email, password)
    return jsonify(session.state())


@app.post("/api/mfa")
def api_mfa():
    data = request.get_json(force=True) or {}
    code = data.get("code")
    if not code:
        return jsonify({"error": "code is required"}), 400
    session.submit_mfa(code)
    return jsonify(session.state())


@app.post("/api/logout")
def api_logout():
    data = request.get_json(silent=True) or {}
    session.logout(forget_device=bool(data.get("forget")))
    _cache.clear()
    return jsonify(session.state())


@app.get("/api/plan")
def api_plan():
    sessions = []
    for s in SESSIONS:
        week, weekday = s["name"].split(" ")[0], s["name"].split(" ")[1]
        kind = next((b["kind"] for b in s["blocks"] if b["role"] in ("main", "repeat")), "easy")
        reps = next(
            (f"{b['reps']}x{b['rep_km']:g}km" for b in s["blocks"] if b["role"] == "repeat"),
            None,
        )
        lo, hi = PACES[kind]
        sessions.append({
            "week": week,
            "day": weekday,
            "date": s["date"],
            "title": s["name"].split(" - ", 1)[1],
            "km": round(session_distance_km(s), 1),
            "kind": kind,
            "reps": reps,
            "paceLo": lo,
            "paceHi": hi,
            "strides": bool(s.get("note")),
            "note": s.get("note"),
            "steps": session_steps(s),
            "durationMin": session_duration_min(s),
        })
    return jsonify({"race": RACE, "sessions": sessions, "plannedKpis": planned_weekly_kpis(SESSIONS)})


@app.get("/api/progress")
def api_progress():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("progress", _fetch_progress, force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({**data, "cacheAgeSec": _cache_age("progress"), "cacheTtlSec": _CACHE_TTL_SECONDS})


@app.get("/api/analysis")
def api_analysis():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        progress = _cached("progress", _fetch_progress, force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502

    weekly: dict[str, dict] = {}
    for row in progress["rows"]:
        wk = weekly.setdefault(row["week"], {"week": row["week"], "plannedKm": 0.0, "actualKm": 0.0, "hasActual": False})
        wk["plannedKm"] += row["plannedKm"]
        if row["actualKm"] is not None:
            wk["actualKm"] += row["actualKm"]
            wk["hasActual"] = True

    weekly_series = [
        {**w, "plannedKm": round(w["plannedKm"], 1), "actualKm": round(w["actualKm"], 1) if w["hasActual"] else None}
        for w in weekly.values()
    ]

    return jsonify({
        "weekly": weekly_series,
        "paceHistory": progress["prediction"].get("history", []),
        "prediction": progress["prediction"],
        "paceCompliancePct": pace_compliance_pct(progress["rows"]),
        "avgHrByKind": avg_hr_by_kind(progress["rows"]),
    })


@app.get("/api/insights")
def api_insights():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("insights", _fetch_insights, ttl=_CACHE_TTL_SECONDS, force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"insights": data, "cacheAgeSec": _cache_age("insights"), "cacheTtlSec": _CACHE_TTL_SECONDS})


@app.get("/api/weekly-summary")
def api_weekly_summary():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        progress = _cached("progress", _fetch_progress, force=force)
        weeks = _week_order(progress["rows"])
        week = request.args.get("week") or _current_week(progress["rows"])
        if week not in weeks:
            return jsonify({"error": f"unknown week {week!r}"}), 400
        data = _cached(f"weekly-{week}", lambda: _fetch_weekly_review(week), force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({**data, "availableWeeks": weeks, "cacheAgeSec": _cache_age(f"weekly-{week}"), "cacheTtlSec": _CACHE_TTL_SECONDS})


@app.get("/api/weight")
def api_weight_get():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        entries = _cached("weight", lambda: fetch_weight_entries(session.client), force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"entries": entries, "trend": compute_trend(entries)})


@app.post("/api/weight")
def api_weight_post():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    data = request.get_json(force=True) or {}
    weight = data.get("weight")
    on_date = data.get("date")
    if not weight or float(weight) <= 0:
        return jsonify({"error": "a positive weight in kg is required"}), 400
    try:
        log_weight(session.client, float(weight), on_date)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    _cache.pop("weight", None)
    return jsonify({"ok": True})


@app.get("/api/sleep")
def api_sleep():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        entries = _cached("sleep", lambda: fetch_sleep_entries(session.client), force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({"entries": entries, "summary": compute_sleep_summary(entries)})


@app.get("/api/hydration")
def api_hydration_get():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("hydration", lambda: fetch_hydration(session.client), force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(data)


@app.post("/api/hydration")
def api_hydration_post():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    data = request.get_json(force=True) or {}
    ml = data.get("ml")
    if not ml or float(ml) <= 0:
        return jsonify({"error": "a positive amount in ml is required"}), 400
    try:
        log_hydration(session.client, float(ml))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    _cache.pop("hydration", None)
    return jsonify({"ok": True})


@app.post("/api/sweat-rate")
def api_sweat_rate():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    data = request.get_json(force=True) or {}
    try:
        pre_kg = float(data["preKg"])
        post_kg = float(data["postKg"])
        fluid_ml = float(data.get("fluidMl") or 0)
        duration_min = float(data["durationMin"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "preKg, postKg, and durationMin are required numbers"}), 400
    try:
        result = compute_sweat_rate(pre_kg, post_kg, fluid_ml, duration_min)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


def _fetch_gear() -> dict:
    race_date = datetime.strptime(RACE["date"], "%Y-%m-%d").date()
    shoes = fetch_shoes(session.client)
    for shoe in shoes:
        shoe["raceProjection"] = project_to_race_day(shoe, race_date)
    return {"shoes": shoes, "raceDate": RACE["date"]}


@app.get("/api/gear")
def api_gear():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("gear", _fetch_gear, force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({**data, "cacheAgeSec": _cache_age("gear"), "cacheTtlSec": _CACHE_TTL_SECONDS})


def _latest_weight_kg() -> tuple[float, bool]:
    """Latest logged weight, or a default - returns (kg, was_assumed)."""
    try:
        entries = _cached("weight", lambda: fetch_weight_entries(session.client))
    except Exception:
        entries = []
    if entries:
        return entries[-1]["weightKg"], False
    return DEFAULT_WEIGHT_KG, True


def _session_on(on_date: str) -> dict | None:
    return next((s for s in SESSIONS if s["date"] == on_date), None)


@app.get("/api/feel")
def api_feel_get():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    try:
        return jsonify({"feels": all_feels()})
    except Exception as exc:
        # Degrade to "no ratings yet" so the rest of Today still renders.
        return jsonify({"feels": {}, "error": friendly_error(exc)})


@app.post("/api/feel")
def api_feel_post():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    data = request.get_json(silent=True) or {}
    try:
        activity_id = int(data["activityId"])
        rpe = int(data["rpe"])
        run_date = str(data["date"])
        datetime.strptime(run_date, "%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "activityId, date (YYYY-MM-DD) and rpe are required"}), 400
    if activity_id <= 0 or not 1 <= rpe <= 10:
        return jsonify({"error": "rpe must be between 1 and 10"}), 400
    try:
        save_feel(activity_id, run_date, rpe)
    except Exception as exc:
        return jsonify({"error": friendly_error(exc)}), 503
    return jsonify({"ok": True})


@app.get("/api/fuel")
def api_fuel():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    on_date = request.args.get("date") or date.today().isoformat()
    s = _session_on(on_date)
    kind = next((b["kind"] for b in s["blocks"] if b["role"] in ("main", "repeat")), None) if s else None
    duration = session_duration_min(s) if s else None
    weight, assumed = _latest_weight_kg()
    return jsonify({
        **day_guidance(weight, kind, duration, DEFAULT_TEMP_C),
        "date": on_date,
        "sessionKind": kind,
        "durationMin": duration,
        "weightAssumed": assumed,
        "dayTypes": all_day_types(weight),
    })


_TIER_GOAL_SEC = {"floor": FLOOR_TIME_SEC, "primary": PRIMARY_TIME_SEC, "stretch": STRETCH_TIME_SEC}


@app.get("/api/race-plan")
def api_race_plan():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    tier = request.args.get("tier", ACTIVE_PACE_SET)
    if tier not in _TIER_GOAL_SEC:
        return jsonify({"error": f"tier must be one of {sorted(_TIER_GOAL_SEC)}"}), 400
    try:
        temp_c = float(request.args.get("tempC", DEFAULT_TEMP_C))
        humidity = float(request.args.get("humidity", DEFAULT_HUMIDITY_PCT))
        sweat_arg = request.args.get("sweatRate")
        sweat_rate = float(sweat_arg) if sweat_arg else None
    except ValueError:
        return jsonify({"error": "tempC, humidity and sweatRate must be numbers"}), 400

    # Goal times are anchored to the runner's own proven race-morning heat
    # (see heat.py), so this only shifts the pacing when the forecast differs.
    penalty = heat_penalty_pct(temp_c, humidity)
    goal_sec = _TIER_GOAL_SEC[tier] * (1 + penalty)
    weight, assumed = _latest_weight_kg()
    return jsonify({
        "tier": tier,
        "activeTier": ACTIVE_PACE_SET,
        "tempC": temp_c,
        "humidityPct": humidity,
        "heatPenaltyPct": round(penalty * 100, 1),
        "pacing": pacing_plan(goal_sec),
        "fuel": fuel_plan(goal_sec, temp_c, sweat_rate),
        "raceMorning": race_morning(weight),
        "carbLoad": carb_load(weight),
        "weightAssumed": assumed,
    })


@app.get("/api/fitness-snapshot")
def api_fitness_snapshot():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("fitness-snapshot", lambda: fetch_fitness_snapshot(session.client), force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({**data, "cacheAgeSec": _cache_age("fitness-snapshot"), "cacheTtlSec": _CACHE_TTL_SECONDS})


def _fetch_readiness() -> dict:
    data = compute_readiness(session.client)
    if data.get("available"):
        progress = _cached("progress", _fetch_progress)
        today_iso = date.today().isoformat()
        today_row = next((r for r in progress["rows"] if r["date"] == today_iso), None)
        data["pivotSuggestion"] = pivot_suggestion(data, today_row["kind"] if today_row else None)
    return data


@app.get("/api/readiness")
def api_readiness():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("readiness", _fetch_readiness, ttl=900, force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({**data, "cacheAgeSec": _cache_age("readiness"), "cacheTtlSec": 900})


@app.get("/api/heat-pace")
def api_heat_pace():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    try:
        temp_c = float(request.args.get("tempC", DEFAULT_TEMP_C))
        humidity_pct = float(request.args.get("humidity", DEFAULT_HUMIDITY_PCT))
    except ValueError:
        return jsonify({"error": "tempC and humidity must be numbers"}), 400
    goal_times_sec = {"primary": PRIMARY_TIME_SEC, "floor": FLOOR_TIME_SEC, "stretch": STRETCH_TIME_SEC}
    try:
        history = _cached("history", _fetch_history_summary, ttl=_HISTORY_CACHE_TTL_SECONDS)
        races = history.get("races", [])
    except Exception:
        races = []
    return jsonify(heat_adjusted_goals(goal_times_sec, temp_c, humidity_pct, races))


@app.get("/api/export/progress.csv")
def export_progress_csv():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    try:
        progress = _cached("progress", _fetch_progress)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    csv_text = rows_to_csv(progress["rows"])
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bangsaen_progress_{date.today().isoformat()}.csv"},
    )


@app.get("/api/history")
def api_history():
    if session.status != "logged_in":
        return jsonify({"error": "not_logged_in"}), 401
    force = request.args.get("refresh") == "1"
    try:
        data = _cached("history", _fetch_history_summary, ttl=_HISTORY_CACHE_TTL_SECONDS, force=force)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify({**data, "cacheAgeSec": _cache_age("history"), "cacheTtlSec": _HISTORY_CACHE_TTL_SECONDS})


session.try_cached_login()  # runs on import too, so gunicorn (which never hits __main__) still restores the session

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
