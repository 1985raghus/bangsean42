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

TOKEN_STORE_PATH = os.path.expanduser("~/.garminconnect")
_TOKEN_FILE = Path(TOKEN_STORE_PATH) / "garmin_tokens.json"

_SUPABASE_URL = os.environ.get("SUPABASE_URL")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
_ROW_ID = "default"  # single-user app - one row is enough


def _supabase_client():
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return None
    from supabase import create_client

    return create_client(_SUPABASE_URL, _SUPABASE_KEY)


def restore_token_to_disk() -> bool:
    """Pulls the saved token from Supabase and writes it to the local file
    garminconnect expects, before it attempts login. No-op (returns False)
    if Supabase isn't configured, or nothing's been saved there yet.
    """
    client = _supabase_client()
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


def save_token_from_disk() -> None:
    """After a successful login, pushes the current token to Supabase so it
    survives a restart. No-op if Supabase isn't configured, or there's no
    local token file yet.
    """
    client = _supabase_client()
    if not client or not _TOKEN_FILE.exists():
        return
    try:
        token_json = json.loads(_TOKEN_FILE.read_text())
        client.table("garmin_token").upsert({"id": _ROW_ID, "token_json": token_json}).execute()
    except Exception:
        pass  # persistence is a nice-to-have here, not worth failing login over


def delete_token() -> None:
    """Mirrors GarminSession.logout(forget_device=True) - clears the Supabase copy too."""
    client = _supabase_client()
    if not client:
        return
    try:
        client.table("garmin_token").delete().eq("id", _ROW_ID).execute()
    except Exception:
        pass
