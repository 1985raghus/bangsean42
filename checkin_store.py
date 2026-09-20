"""Daily check-in: mood and motivation, one tap a day.

The watch measures the body. This is the only place the runner's own read on
the day gets recorded - and over an 11-week build it's the series that explains
the weeks the numbers can't. Stored in Supabase when configured (the deployed
app), otherwise a small local JSON file so a local run still works.

Same shape as feel_store (post-run RPE); kept separate because they answer
different questions: how the RUN felt vs how the DAY feels.
"""

import json
import os
from pathlib import Path

from db import supabase_client

_TABLE = "daily_checkin"
_LOCAL_FILE = Path(os.path.expanduser("~/.bangsaen/daily_checkin.json"))

SCALE_MIN, SCALE_MAX = 1, 5
MOOD_LABELS = {1: "Rough", 2: "Flat", 3: "Fine", 4: "Good", 5: "Great"}
MOTIVATION_LABELS = {1: "Empty", 2: "Low", 3: "Steady", 4: "Keen", 5: "Fired up"}


def _read_local() -> dict:
    if not _LOCAL_FILE.exists():
        return {}
    return json.loads(_LOCAL_FILE.read_text())


def all_checkins() -> dict[str, dict]:
    """Date (YYYY-MM-DD) -> {"mood": int, "motivation": int, "note": str}."""
    client = supabase_client()
    if client is None:
        return _read_local()
    rows = client.table(_TABLE).select("checkin_date, mood, motivation, note").execute().data
    return {
        r["checkin_date"]: {"mood": r["mood"], "motivation": r["motivation"], "note": r.get("note") or ""}
        for r in rows
    }


def save_checkin(checkin_date: str, mood: int, motivation: int, note: str = "") -> None:
    client = supabase_client()
    entry = {"mood": mood, "motivation": motivation, "note": note or ""}
    if client is None:
        data = _read_local()
        data[checkin_date] = entry
        _LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_FILE.write_text(json.dumps(data))
        return
    client.table(_TABLE).upsert({"checkin_date": checkin_date, **entry}).execute()


def summarize(checkins: dict[str, dict], days: list[str]) -> dict:
    """Streak and averages over the given dates (oldest first)."""
    present = [checkins[d] for d in days if d in checkins]
    streak = 0
    for d in reversed(days):
        if d not in checkins:
            break
        streak += 1
    if not present:
        return {"logged": 0, "streak": 0, "avgMood": None, "avgMotivation": None}
    return {
        "logged": len(present),
        "streak": streak,
        "avgMood": round(sum(c["mood"] for c in present) / len(present), 1),
        "avgMotivation": round(sum(c["motivation"] for c in present) / len(present), 1),
    }


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if _TABLE in text or "PGRST205" in text:
        return "The daily_checkin table isn't set up in Supabase yet - run its SQL from the README once."
    return text
