"""Garmin token persistence via Supabase, with a local-file fallback.

garminconnect caches its login token as a single JSON file at
TOKEN_STORE_PATH/garmin_tokens.json. That's fine on a machine that stays
around, but a cloud host's disk isn't guaranteed to survive a restart or
redeploy - so once this app is deployed, the token needs somewhere durable
to live. Supabase provides that: one row, upserted on every successful
login, restored to the local file before garminconnect tries to log in.

Locally (no SUPABASE_URL/SUPABASE_KEY set), every function here is a no-op
that returns immediately - nothing changes for local development, and
garminconnect's own local-file cache keeps working exactly as before.
"""

import json
import os
from pathlib import Path

from db import supabase_client, supabase_configured

TOKEN_STORE_PATH = os.path.expanduser("~/.garminconnect")
_TOKEN_FILE = Path(TOKEN_STORE_PATH) / "garmin_tokens.json"

_ROW_ID = "default"  # single-user app - one row is enough


def restore_token_to_disk() -> bool:
    """Pulls the saved token from Supabase and writes it to the local file
    garminconnect expects, before it attempts login. No-op (returns False)
    if Supabase isn't configured, or nothing's been saved there yet.
    """
    client = supabase_client()
    if not client:
        return False
    try:
        result = client.table("garmin_token").select("token_json").eq("id", _ROW_ID).execute()
    except Exception:
        return False
    if not result.data:
        return False
    Path(TOKEN_STORE_PATH).mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(json.dumps(result.data[0]["token_json"]))
    return True


def save_token_from_disk() -> bool:
    """Pushes the current token file to Supabase so it survives a restart.
    Returns False (without raising) if Supabase isn't configured, there's no
    token file yet, or the write failed - persistence is never worth failing
    a login over.
    """
    client = supabase_client()
    if not client or not _TOKEN_FILE.exists():
        return False
    try:
        token_json = json.loads(_TOKEN_FILE.read_text())
        client.table("garmin_token").upsert({"id": _ROW_ID, "token_json": token_json}).execute()
    except Exception:
        return False
    return True


_last_synced_mtime: float | None = None


def sync_if_changed() -> None:
    """Pushes the token file to Supabase whenever it has changed since the last push.

    Garmin rotates the refresh token every time garminconnect refreshes the
    session, and garminconnect rewrites only the local file when it does. A
    Supabase copy saved just at login therefore goes stale within hours -
    the next restart restores a refresh token Garmin has already retired.
    Checking the file's mtime on each request is cheap and keeps the cloud
    copy current. No-op when Supabase isn't configured.
    """
    global _last_synced_mtime
    if not supabase_configured() or not _TOKEN_FILE.exists():
        return
    mtime = _TOKEN_FILE.stat().st_mtime
    if mtime == _last_synced_mtime:
        return
    if save_token_from_disk():
        _last_synced_mtime = mtime


def delete_token() -> None:
    """Mirrors GarminSession.logout(forget_device=True) - clears the Supabase copy too."""
    client = supabase_client()
    if not client:
        return
    try:
        client.table("garmin_token").delete().eq("id", _ROW_ID).execute()
    except Exception:
        pass
