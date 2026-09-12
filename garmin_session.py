"""Single-user, in-memory Garmin Connect login state for the local web app.

Login is interactive (email/password, optionally an MFA code) but a web
request/response cycle can't block waiting for a second request the way a
terminal's input() can. This runs garminconnect's login() in a background
thread and hands it a prompt_mfa callback that blocks on a queue until the
browser posts the code - the same public API push_to_garmin.py already uses,
just adapted so the blocking happens off the request thread.

Tokens persist to TOKEN_STORE_PATH (garminconnect's own cache format), so
after the first successful login, restarting the app logs back in silently
with no password or MFA prompt - exactly like the CLI scripts already do.
"""

import os
import queue
import threading

from garminconnect import Garmin

from token_store import delete_token, restore_token_to_disk, save_token_from_disk

TOKEN_STORE_PATH = os.path.expanduser("~/.garminconnect")


class GarminSession:
    def __init__(self) -> None:
        self.client: Garmin | None = None
        self.status = "logged_out"  # logged_out | logging_in | awaiting_mfa | logged_in | error
        self.error: str | None = None
        self._mfa_queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()

    def try_cached_login(self) -> bool:
        """Attempt a silent login from cached tokens only. No prompts, no thread."""
        with self._lock:
            if self.status == "logged_in":
                return True
            restore_token_to_disk()  # no-op locally; pulls from Supabase when deployed
            client = Garmin()
            try:
                client.login(TOKEN_STORE_PATH)
            except Exception:
                return False
            self.client = client
            self.status = "logged_in"
            self.error = None
            save_token_from_disk()  # keep Supabase in sync in case the token refreshed
            return True

    def start_login(self, email: str, password: str) -> None:
        with self._lock:
            if self.status == "logging_in":
                return
            self.status = "logging_in"
            self.error = None
            self._mfa_queue = queue.Queue()

        def worker() -> None:
            client = Garmin(email, password, prompt_mfa=self._wait_for_mfa)
            try:
                client.login(TOKEN_STORE_PATH)
            except Exception as exc:
                self.status = "error"
                self.error = str(exc)
                return
            self.client = client
            self.status = "logged_in"
            save_token_from_disk()

        threading.Thread(target=worker, daemon=True).start()

    def _wait_for_mfa(self) -> str:
        self.status = "awaiting_mfa"
        code = self._mfa_queue.get()  # blocks the login worker thread only
        self.status = "logging_in"
        return code

    def submit_mfa(self, code: str) -> bool:
        if self.status != "awaiting_mfa":
            return False
        self._mfa_queue.put(code)
        return True

    def logout(self, forget_device: bool = False) -> None:
        with self._lock:
            self.client = None
            self.status = "logged_out"
            self.error = None
        if forget_device:
            delete_token()
            if os.path.exists(TOKEN_STORE_PATH):
                import shutil

                shutil.rmtree(TOKEN_STORE_PATH, ignore_errors=True)

    def state(self) -> dict:
        return {"status": self.status, "error": self.error}


session = GarminSession()
