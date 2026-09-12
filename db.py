"""Shared Supabase client for the deployed app.

Returns None when SUPABASE_URL / SUPABASE_KEY aren't set (a local run), so
callers can fall back to local storage instead of failing.
"""

import os
from functools import lru_cache


def supabase_configured() -> bool:
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))


@lru_cache(maxsize=1)
def _client():
    from supabase import create_client

    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def supabase_client():
    return _client() if supabase_configured() else None
