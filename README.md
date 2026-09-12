# Bangsaen Marathon Plan → Garmin Connect

An 11-week marathon plan (2026-09-02 to race day 2026-11-15), built for a
Forerunner 935 via Garmin Connect (unofficial API). **ACTIVE_PACE_SET is
"stretch"** (sub-4:00, 5:41/km) by explicit choice — 490 km total, peaking
at ~59.5 km in week 8 with a 30K peak long run.

Two more conservative tiers exist in `plan_data.py` if training data says to
back off: **primary 4:35:00** (6:31/km) and **floor 4:45:00** (6:45/km,
roughly matches the account's actual last two Bangsaen finishes: 4:42:00 in
2024, 4:46:24 in 2025). A milder, recalibrated version of this plan (273km,
smoothed ramp rate) was tried first and rejected as too conservative — this
is the deliberate, ambitious choice instead. The Coach KPIs (ramp rate,
long-run share) still run live and will flag real spikes (W5 currently
shows +45%) — that's visibility for self-monitoring, not a gate this file
enforces on its own.

4 running days/week: Tue easy, Thu easy, Fri quality, Sun long; rest
Mon/Wed/Sat (built around a mandatory Tuesday office day and Mon/Thu/Fri
WFH - Week 1 ran the original Tue/Thu/Sat/Sun cadence as a one-time
transition). Tempo and marathon-pace sessions are built as repeated
intervals (e.g. 3x1.5K @ Tempo with a short jog between), so the watch
counts down reps the way a Garmin Coach plan does, instead of showing one
flat continuous run. Every workout's description also states its target
pace in plain text (e.g. "Target pace: Tempo 5:21-5:11/km"), since the
pace-zone data on the steps isn't visible everywhere the workout shows up.

- `plan_data.py` — the plan itself: the three pace tiers and every session (edit this to change dates/distances/paces).
- `insights.py` — mistake-detection rules (effort mismatch, positive splits, ramp spikes, missed sessions) that generate specific suggestions, not just flags.
- `health.py` — weight logging and trend analysis, backed by Garmin Connect's own body-composition record.
- `build_workouts.py` — converts a session into a Garmin Connect structured workout, including interval repeat groups and the plain-text pace description.
- `push_to_garmin.py` — logs into Garmin Connect, creates or updates each workout, and schedules it on your calendar so it syncs to the watch.
- `progress.py` — shared logic for comparing the plan to what actually happened (adherence + race-time prediction); used by both `track_progress.py` and `app.py`.
- `history.py` — pulls and summarizes training history from before the plan started (prior races, VO2max trend, base volume); used by `app.py`'s "Reality check" view.
- `track_progress.py` — CLI: pulls your actual runs back from Garmin Connect and compares them against the plan.
- `garmin_session.py` — manages the Garmin Connect login for the web app (background-thread MFA handling, token caching).
- `app.py` + `templates/index.html` — local web app: Garmin Connect login, live progress, and training analysis in the browser.

## Setup

```
pip install -r requirements.txt
```

Set credentials as environment variables (or you'll be prompted):

```
set GARMIN_EMAIL=you@example.com
set GARMIN_PASSWORD=yourpassword
```

If your account has MFA enabled, you'll be prompted for the code interactively.

## Usage

Preview what would be created, without touching your account:

```
python push_to_garmin.py --dry-run
```

Push everything for real:

```
python push_to_garmin.py
```

Workouts sync to the 935 next time Garmin Connect Mobile syncs with the
watch over Bluetooth. The script is safe to re-run — a session whose name
already exists gets updated in place (same workout ID, so any calendar
schedule pointing at it stays valid) rather than skipped, so editing
`plan_data.py` and re-running always syncs your account to match.

If you've previously pushed an older version of this plan and the session
names changed, clean up the stale ones first:

```
python push_to_garmin.py --clean
```

This only deletes workouts matching this plan's `W<n> ...` naming that are no
longer in `plan_data.py` — it never touches anything else in your account.

Race day (2026-11-15, Bangsaen Marathon, goal 4:15:00) is intentionally
**not** uploaded as a workout — add it to your Garmin Connect calendar as a
race event by hand.

### Tracking progress & race-time prediction

Once you're into the plan, check adherence against what actually synced from
the watch:

```
python track_progress.py
python track_progress.py --week W7   # just one week
```

For each session due so far it reports done / partial / missed, actual
distance vs. planned, and whether your average pace landed in the target
zone. Matching is by calendar date, so log runs on the day they're
scheduled for.

It also prints a **race-time prediction** built from your actual
marathon-pace session paces (MP interval workouts and MP-finish long runs),
compared against the primary (4:35:00), floor (4:45:00), and stretch
(sub-4:00) tiers, with a recommendation once there's enough data:

- **Ahead of stretch** — comfortably inside sub-4:00 territory; real
  evidence for a conversation about tightening the goal, not an automatic switch.
- **On track for primary** — keep training at the primary paces.
- **Between primary and floor** — still on for a PB, watch the next few MP sessions.
- **Behind floor** — switch `ACTIVE_PACE_SET` to `"floor"` in `plan_data.py`
  (or ask me to) and re-push; same distances and structure, easier paces.

This is a rough estimate — it uses each session's whole-activity average
pace, which runs a little slower than true rep pace since it includes
warmup/cooldown/recovery jogs. Read it as a trend, not a lab number.

### Web app

For the same data in a browser instead of the terminal:

```
python app.py
```

Then open http://127.0.0.1:5000. First run shows a login screen (Garmin
Connect email/password, with an MFA field that appears if your account
needs it); after that it logs back in silently from the cached token at
`~/.garminconnect` — no password prompt until that token expires or you log
out with "forget this device."

It runs on your machine only (127.0.0.1, not exposed to the network) and
never stores your password — only garminconnect's own session token is
cached, the same file the CLI scripts already use.

The dashboard is tabbed (it grew past a single scroll's worth of content):

- **Today** — the tiles, distance-covered progress ring, and the **Coach
  Insights** feed (`insights.py`): live mistake-detection rules checked
  against this account's own history - easy days run too hard, quality
  sessions off target, a missed session, a ramp-rate spike about to happen,
  or (the one that closes the loop) a completed long run positive-splitting
  the same way both prior marathons did. Each insight names what happened
  and what to actually do about it, most-severe first.
- **Training** — race-time prediction, Coach KPIs (ramp rate, long-run
  share, pace-zone compliance, avg HR by session type), weekly volume and
  MP-pace-trend charts, and the full week-by-week schedule.
- **Health** — log weight (writes to Garmin Connect's own body-composition
  record via `health.py`, the same place a synced smart scale would land -
  no separate database), a trend chart, and a nutrition-coach card that
  reads the weekly rate of change against the current training load.
- **History** — the Reality Check (prior finishes, VO2max trend, base
  volume) and "3 Marathons, One Pattern" (km-by-km pacing charts for both
  prior races, a 3-cycle effort-variation comparison, and a data-driven
  coach's verdict).

Data is pulled on demand (page load, tab switch, or the Refresh button) and
cached for 5 minutes (history for an hour, since it barely changes) — no
background polling. The app and the CLI share `progress.py`, so the
numbers always agree; `track_progress.py` prints the same Coach KPIs and
Coach Insights the app shows.

## Assumptions to check

- `plan_data.py` holds three pace tiers derived from real Garmin history on
  this account — two prior Bangsaen finishes (4:42:00, 4:46:24), current
  VO2max at a 3-year low, and a ~4 km/week base over the 16 weeks before
  this plan starts. `ACTIVE_PACE_SET` is currently `"stretch"` (sub-4:00) by
  explicit choice, overriding that data; `"primary"` (4:35:00) and
  `"floor"` (4:45:00) are the more conservative fallbacks if training shows
  it's not holding up.
- 4 runs/week (Tue/Thu/Sat/Sun), rest Mon/Wed/Fri, long run Sunday. Week 1's
  Tuesday session was moved from 09-01 to 09-02 (today) as a one-time
  exception since the plan starts mid-week — every week from W2 on keeps
  the full Tue/Thu/Sat/Sun cadence with 3 rest days.
- Total volume is 490 km over 11 weeks, peaking around 59.5 km/week (week
  7-8) with a 30 km long run, then a taper — a real ~10-15x jump from the
  account's actual recent volume. A smoothed, lower-volume version (273km,
  no ramp spike over +19%) was built and rejected in favor of this one; the
  Coach KPIs still flag real spikes live (currently W5, +45%) for
  self-monitoring rather than blocking anything.
- Long-run share of weekly volume runs ~38-51%, higher than the ~25-30%
  often quoted for higher-mileage plans — worth watching given the overall
  volume and pace are both aggressive relative to the account's base.
- `track_progress.py` and `app.py` match Garmin activity JSON fields
  (`distance`, `duration`, `startTimeLocal`) that are standard for this API
  but not pinned down by a live test here — if a live run shows off
  numbers, tell me and I'll adjust the field names.
- `app.py`'s login flow (happy path, MFA, and error handling) is unit-tested
  against a mocked Garmin client, but the actual network round-trip to
  Garmin's login servers hasn't been exercised with a real account — if the
  first live login behaves unexpectedly, tell me what you see and I'll fix it.
- Bangsaen is coastal Thailand — heat/humidity affect both race-day pacing
  and hydration/electrolyte needs regardless of how early the race starts.
