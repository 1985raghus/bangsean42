"""Daily readiness score: sleep + resting HR + training-load ratio.

Everything else in this app (insights.py, weekly_summary.py) looks
backwards - it flags a mistake after it already happened. This is the one
proactive signal: given last night's sleep, this morning's resting HR
against your own baseline, and the acute:chronic training-load ratio
(ACWR - a real injury-risk metric from sports science, see Gabbett 2016),
should today lean toward the plan as written or toward backing off.

Each component is independent and degrades gracefully: with only ~2 weeks
of data so far, the training-load baseline in particular is still thin,
so that component reports itself unavailable rather than guessing from too
little history. The score is only ever computed from whichever components
have enough data.
"""

from datetime import date, timedelta

from garminconnect import Garmin

from fitness_snapshot import fetch_fitness_snapshot
from health import fetch_sleep_entries
from progress import fetch_activities_by_date

_SLEEP_BASELINE_NIGHTS = 13  # trailing nights (excl. last night) averaged for a personal baseline
_RHR_BASELINE_NIGHTS = 13
_SHORT_SLEEP_HOURS = 6.0
_MILD_SHORT_SLEEP_HOURS = 7.0
_ELEVATED_RHR_BPM = 5.0
_MILD_ELEVATED_RHR_BPM = 2.0

_ACUTE_DAYS = 7
_CHRONIC_DAYS = 28
_MIN_CHRONIC_DAYS_WITH_RUNS = 10  # below this, the 4-week baseline is too thin to trust
_ACWR_SPIKE = 1.5  # classic injury-risk threshold (Gabbett 2016)
_ACWR_CAUTION = 1.3
_ACWR_DETRAIN = 0.8


def daily_distance_km(client: Garmin, as_of: date, days: int) -> dict[str, float]:
    start = as_of - timedelta(days=days - 1)
    activity_by_date = fetch_activities_by_date(client, start.isoformat(), as_of.isoformat())
    return {d: round(a.get("distance", 0) / 1000.0, 2) for d, a in activity_by_date.items()}


def compute_acwr(daily_km: dict[str, float], as_of: date) -> dict:
    def _age_days(d: str) -> int:
        return (as_of - date.fromisoformat(d)).days

    acute_total = sum(km for d, km in daily_km.items() if 0 <= _age_days(d) < _ACUTE_DAYS)
    chronic_entries = {d: km for d, km in daily_km.items() if 0 <= _age_days(d) < _CHRONIC_DAYS}
    chronic_days_with_runs = sum(1 for km in chronic_entries.values() if km > 0)
    chronic_weekly_avg = sum(chronic_entries.values()) / (_CHRONIC_DAYS / 7)
    ratio = round(acute_total / chronic_weekly_avg, 2) if chronic_weekly_avg > 0 else None

    return {
        "acuteKm": round(acute_total, 1),
        "chronicWeeklyAvgKm": round(chronic_weekly_avg, 1),
        "chronicDaysWithRuns": chronic_days_with_runs,
        "ratio": ratio,
        "baselineReady": chronic_days_with_runs >= _MIN_CHRONIC_DAYS_WITH_RUNS,
    }


def _sleep_component(entries: list[dict]) -> dict:
    if not entries:
        return {"available": False}
    last_night = entries[-1]
    baseline_pool = entries[:-1][-_SLEEP_BASELINE_NIGHTS:]
    baseline_avg = round(sum(e["hours"] for e in baseline_pool) / len(baseline_pool), 1) if baseline_pool else None

    if last_night["hours"] < _SHORT_SLEEP_HOURS:
        delta = -25
    elif last_night["hours"] < _MILD_SHORT_SLEEP_HOURS:
        delta = -10
    else:
        delta = 0

    return {
        "available": True,
        "lastNightHours": last_night["hours"],
        "lastNightDate": last_night["date"],
        "baselineAvgHours": baseline_avg,
        "scoreDelta": delta,
    }


def _rhr_component(entries: list[dict]) -> dict:
    rhr_entries = [e for e in entries if e.get("restingHr")]
    if len(rhr_entries) < 2:
        return {"available": False}
    latest = rhr_entries[-1]
    baseline_pool = rhr_entries[:-1][-_RHR_BASELINE_NIGHTS:]
    if not baseline_pool:
        return {"available": False}
    baseline_avg = sum(e["restingHr"] for e in baseline_pool) / len(baseline_pool)
    delta_bpm = latest["restingHr"] - baseline_avg

    if delta_bpm >= _ELEVATED_RHR_BPM:
        delta = -25
    elif delta_bpm >= _MILD_ELEVATED_RHR_BPM:
        delta = -10
    else:
        delta = 0

    return {
        "available": True,
        "latestRhr": latest["restingHr"],
        "baselineAvgRhr": round(baseline_avg, 1),
        "deltaBpm": round(delta_bpm, 1),
        "scoreDelta": delta,
    }


def _acwr_load_component(acwr: dict) -> dict:
    if not acwr["baselineReady"] or acwr["ratio"] is None:
        return {"available": False, "source": "acwr", "reason": "still building a 4-week training-load baseline"}

    ratio = acwr["ratio"]
    if ratio > _ACWR_SPIKE:
        flag, delta = "spike", -30
        note = f"Training-load ratio {ratio} - acute volume is well above your 4-week average, real injury-risk territory (>{_ACWR_SPIKE})."
    elif ratio > _ACWR_CAUTION:
        flag, delta = "caution", -15
        note = f"Training-load ratio {ratio} - creeping above the {_ACWR_DETRAIN}-{_ACWR_CAUTION} sweet spot."
    elif ratio < _ACWR_DETRAIN:
        flag, delta = "detraining", -5
        note = f"Training-load ratio {ratio} - below the {_ACWR_DETRAIN}-{_ACWR_CAUTION} sweet spot, recent volume has dropped off."
    else:
        flag, delta = "sweet_spot", 0
        note = f"Training-load ratio {ratio} sits inside the {_ACWR_DETRAIN}-{_ACWR_CAUTION} sweet spot."

    return {"available": True, "source": "acwr", "ratio": ratio, "flag": flag, "scoreDelta": delta, "note": note}


def _native_load_component(snapshot: dict | None) -> dict | None:
    """Garmin's own personalized acute-load range ("load tunnel"), calibrated from years of
    this account's history - already populated when this app's own ACWR baseline (only ~2
    weeks of in-cycle data) isn't. Preferred over the ACWR fallback whenever it's available.
    """
    if not snapshot or not snapshot.get("loadVerdict"):
        return None

    load, lo, hi = snapshot["weeklyTrainingLoad"], snapshot["loadTunnelMin"], snapshot["loadTunnelMax"]
    verdict = snapshot["loadVerdict"]
    if verdict == "above_range":
        flag, delta = "spike", -30
        note = f"Weekly training load {load} is above your personalized range ({lo}-{hi}, from Garmin's own training-status algorithm) - real overreach risk."
    elif verdict == "below_range":
        flag, delta = "detraining", -5
        note = f"Weekly training load {load} is below your personalized range ({lo}-{hi})."
    else:
        flag, delta = "sweet_spot", 0
        note = f"Weekly training load {load} sits inside your personalized range ({lo}-{hi})."

    return {"available": True, "source": "garmin_native", "flag": flag, "scoreDelta": delta, "note": note,
            "weeklyTrainingLoad": load, "loadTunnelMin": lo, "loadTunnelMax": hi}


def compute_readiness(client: Garmin, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()

    sleep_entries = fetch_sleep_entries(client, days=30)
    daily_km = daily_distance_km(client, as_of, _CHRONIC_DAYS)
    acwr = compute_acwr(daily_km, as_of)

    try:
        snapshot = fetch_fitness_snapshot(client, as_of.isoformat())
    except Exception:
        snapshot = None

    sleep = _sleep_component(sleep_entries)
    rhr = _rhr_component(sleep_entries)
    load = _native_load_component(snapshot) or _acwr_load_component(acwr)

    available = [c for c in (sleep, rhr, load) if c.get("available")]
    if not available:
        return {
            "available": False,
            "message": "Not enough sleep, resting-HR, or training-load history yet to compute a readiness score.",
        }

    score = max(0, min(100, 100 + sum(c["scoreDelta"] for c in available)))
    if score >= 80:
        verdict, headline = "green", "Good to push"
    elif score >= 60:
        verdict, headline = "amber", "Proceed, but respect the zones"
    else:
        verdict, headline = "red", "Prioritize recovery today"

    reasons = []
    if sleep.get("available") and sleep["scoreDelta"] < 0:
        reasons.append(
            f"Slept {sleep['lastNightHours']}h last night vs a {sleep['baselineAvgHours']}h baseline."
        )
    if rhr.get("available") and rhr["scoreDelta"] < 0:
        reasons.append(
            f"Resting HR {rhr['latestRhr']}bpm is {rhr['deltaBpm']:+.1f} above your {rhr['baselineAvgRhr']}bpm baseline."
        )
    if load.get("available") and load["scoreDelta"] < 0:
        reasons.append(load["note"])
    if not reasons:
        reasons.append("Sleep, resting HR, and training load all look normal against your own baseline.")

    return {
        "available": True,
        "score": score,
        "verdict": verdict,
        "headline": headline,
        "reasons": reasons,
        "sleep": sleep,
        "restingHr": rhr,
        "trainingLoad": {**load, **acwr},
    }


_QUALITY_KINDS = {"tempo", "mp"}


def pivot_suggestion(readiness: dict, today_kind: str | None) -> dict | None:
    """The one piece that turns this score into an actual decision: if today's plan calls
    for a hard session but readiness says otherwise, say so explicitly instead of leaving
    the runner to notice the contradiction themselves.
    """
    if not readiness.get("available") or today_kind not in _QUALITY_KINDS:
        return None

    kind_label = "Marathon Pace" if today_kind == "mp" else "Tempo"
    if readiness["verdict"] == "red":
        return {
            "severity": "critical",
            "message": (
                f"Readiness is red ({readiness['score']}/100) and today is a {kind_label} session. "
                "Swap it for an easy run or a rest day - pushing quality on top of this is exactly how "
                "the injury-risk signal in this score turns into an actual injury."
            ),
        }
    if readiness["verdict"] == "amber":
        return {
            "severity": "warning",
            "message": (
                f"Readiness is amber ({readiness['score']}/100) and today is a {kind_label} session. "
                "Still worth doing, but hold the target pace zone strictly rather than pushing past it."
            ),
        }
    return None
