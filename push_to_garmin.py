import argparse
import os
import re
import time
from getpass import getpass

from garminconnect import Garmin

from build_workouts import build_workout
from garmin_session import TOKEN_STORE_PATH
from plan_data import RACE, SESSIONS

_PLAN_NAME_PATTERN = re.compile(r"^W\d+ ")


def login() -> Garmin:
    """Log in, preferring the cached token from a previous session/app.py login."""
    cached = Garmin()
    try:
        cached.login(TOKEN_STORE_PATH)
        return cached
    except Exception:
        pass

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ")
    password = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: ")
    client = Garmin(email, password, prompt_mfa=lambda: input("MFA code: "))
    client.login(TOKEN_STORE_PATH)
    return client


def existing_plan_workouts(client: Garmin) -> list[dict]:
    """All workouts in the account whose name matches this plan's 'W<n> ...' naming."""
    workouts = []
    start = 0
    while True:
        batch = client.get_workouts(start=start, limit=100)
        if not batch:
            break
        workouts.extend(w for w in batch if _PLAN_NAME_PATTERN.match(w.get("workoutName", "")))
        if len(batch) < 100:
            break
        start += 100
    return workouts


def existing_workout_ids(client: Garmin) -> dict[str, int]:
    """Map workoutName -> workoutId for everything currently in the account."""
    by_name: dict[str, int] = {}
    start = 0
    while True:
        batch = client.get_workouts(start=start, limit=100)
        if not batch:
            break
        for w in batch:
            by_name[w.get("workoutName", "")] = w["workoutId"]
        if len(batch) < 100:
            break
        start += 100
    return by_name


def clean_stale_workouts(client: Garmin) -> None:
    """Delete previously-uploaded plan workouts that are no longer in SESSIONS.

    Matches on the 'W<n> ...' naming convention so it only ever touches
    workouts this script created, never anything else in the account.
    """
    current_names = {session["name"] for session in SESSIONS}
    stale = [w for w in existing_plan_workouts(client) if w.get("workoutName") not in current_names]

    if not stale:
        print("No stale plan workouts found.")
        return

    print(f"Found {len(stale)} stale plan workout(s) to delete:")
    for workout in stale:
        name = workout.get("workoutName", "")
        try:
            client.delete_workout(workout["workoutId"])
            print(f"  DELETED: {name}")
        except Exception as exc:
            print(f"  FAILED TO DELETE: {name}  ({exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Push the marathon plan to Garmin Connect")
    parser.add_argument("--dry-run", action="store_true", help="Build and print workouts without uploading")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete previously-uploaded plan workouts that are no longer in SESSIONS, then exit",
    )
    args = parser.parse_args()

    print(f"{len(SESSIONS)} sessions to push. Race day is not uploaded as a workout:")
    print(f"  {RACE['date']} - {RACE['name']} - primary {RACE['primary_time']} ({RACE['primary_pace']}), stretch {RACE['stretch_time']}")
    print("  Add it to your Garmin Connect calendar manually as a race event.\n")

    if args.dry_run:
        for session in SESSIONS:
            workout = build_workout(session)
            print(f"[DRY RUN] {session['date']}  {session['name']}  ({workout.estimatedDurationInSecs // 60} min est.)")
        return

    client = login()

    if args.clean:
        clean_stale_workouts(client)
        return

    existing = existing_workout_ids(client)

    created, updated, failed = [], [], []
    for session in SESSIONS:
        name = session["name"]
        workout = build_workout(session)
        try:
            if name in existing:
                client.update_workout(existing[name], workout.to_dict())
                print(f"UPDATED: {session['date']}  {name}")
                updated.append(name)
            else:
                result = client.upload_running_workout(workout)
                client.schedule_workout(result["workoutId"], session["date"])
                print(f"CREATED: {session['date']}  {name}")
                created.append(name)
        except Exception as exc:
            print(f"FAILED: {name}  ({exc})")
            failed.append(name)
        time.sleep(1)

    print(f"\nDone. {len(created)} created, {len(updated)} updated, {len(failed)} failed.")
    if failed:
        print("Failed sessions:")
        for name in failed:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
