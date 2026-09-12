"""Post-run "how did it feel" ratings: session RPE on a 1-10 scale.

The Forerunner 935 can't record perceived effort, and it's the missing half
of this runner's known failure pattern - a run that *felt* easy but was
paced too hot. The runner taps a rating after each run, and the coaching
cues compare it with the effort that session was meant to feel like.

Stored in the Supabase table `run_feel` when Supabase is configured (the
deployed app), otherwise in a small local JSON file so a local run still works.
"""

import json
import os
from pathlib import Path

from db import supabase_client

_TABLE = "run_feel"
_LOCAL_FILE = Path(os.path.expanduser("~/.bangsaen/run_feel.json"))


def _read_local() -> dict:
    if not _LOCAL_FILE.exists():
        return {}
    return json.loads(_LOCAL_FILE.read_text())


def all_feels() -> dict[str, dict]:
    """Activity id (as a string) -> {"rpe": int, "date": "YYYY-MM-DD"}."""
    client = supabase_client()
    if client is None:
        return _read_local()
    rows = client.table(_TABLE).select("activity_id, run_date, rpe").execute().data
    return {str(r["activity_id"]): {"rpe": r["rpe"], "date": r["run_date"]} for r in rows}


def save_feel(activity_id: int, run_date: str, rpe: int) -> None:
    client = supabase_client()
    if client is None:
        data = _read_local()
        data[str(activity_id)] = {"rpe": rpe, "date": run_date}
        _LOCAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_FILE.write_text(json.dumps(data))
        return
    client.table(_TABLE).upsert({"activity_id": activity_id, "run_date": run_date, "rpe": rpe}).execute()


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "run_feel" in text or "PGRST205" in text:
        return "The run_feel table isn't set up in Supabase yet - run its SQL from the README once."
    return text
