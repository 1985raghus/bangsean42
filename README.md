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
- `build_workouts.py` — converts a session into a Garmin Connect structured workout (interval repeat groups, plain-text pace description), and exposes its steps and estimated duration to the web app.
- `push_to_garmin.py` — logs into Garmin Connect, creates or updates each workout, and schedules it on your calendar so it syncs to the watch.
- `progress.py` — plan vs. actual: adherence, work-interval pace for quality sessions (warmup/recovery/cooldown laps excluded), recency-weighted race prediction, fade forecast, CSV export.
- `insights.py` — mistake-detection rules (easy runs too hard, quality too fast or slow, long-run fade, aerobic decoupling, short sleep, ramp spikes, missed sessions), each with a specific suggestion.
- `readiness.py` — daily readiness score from sleep, resting HR and training load, plus a "swap today's quality session" suggestion when it's low.
- `fuel.py` — daily carb/protein targets scaled to body weight, and before/during/after-run fueling by session.
- `race_plan.py` — race-day pacing (a deliberately slower first 5 km), gel/fluid/sodium timeline, carb-load and race-morning guidance.
- `heat.py` — heat-adjusted goal times, anchored to the temperatures of your own two prior Bangsaen finishes.
- `health.py` — weight, sleep, hydration and sweat rate, backed by Garmin Connect's own records.
- `history.py` — prior races (km-by-km splits), training-block comparisons, VO2max history.
- `gear.py` / `fitness_snapshot.py` — shoe mileage from Garmin Gear; Garmin's own VO2max and training-load range.
- `garmin_session.py` + `token_store.py` — the web app's Garmin login (background-thread MFA) and token persistence (local file, or Supabase when deployed).
- `track_progress.py` — CLI: pulls your actual runs back from Garmin Connect and compares them against the plan.
- `app.py` + `templates/index.html` — the web app (Flask API + a single-page, mobile-first frontend).

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

Race day (2026-11-15, Bangsaen Marathon) is intentionally **not** uploaded
as a workout — add it to your Garmin Connect calendar as a race event by hand.

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

For tempo and MP sessions the pace used is the work-interval laps only —
warmup, recovery jogs and cooldown are filtered out by comparing each lap
against easy pace, which works whatever the watch's auto-lap setting does.
The last three sessions are recency-weighted (1x/2x/3x). With only a few
sessions it's still a direction, not a promise.

### Web app

For the same data in a browser instead of the terminal:

```
python app.py
```

Then open http://127.0.0.1:5000. The first time, use **Owner sign-in**
(Garmin email/password, plus the MFA code if your account asks for one);
after that it reconnects silently from the cached token at
`~/.garminconnect`. Your password is never stored — only the session token
Garmin returns.

Three tabs, mobile-first, each answering one question:

- **Today** — *Am I ready, and what do I run?* Readiness with its reason,
  the next session with its target pace band and steps, a coaching cue from
  your last run of the same type, today's fueling (carbs, protein,
  before/during/after, water), your last run against its target, this week,
  and any coach flags from the past 7 days.
- **Progress** — *Am I on track for race day?* Predicted finish against the
  floor/primary/stretch tiers, a pace-vs-target chart for every run,
  easy-vs-hard heart-rate gap, all coach flags, weekly volume, the race plan
  (pacing, fuel timeline, race-week eating, race-day conditions), the
  2024/2025 pacing lessons, and the full 11-week plan with CSV export.
- **Body** — sleep, weight (logs to Garmin Connect), carb targets by day
  type, a sweat test that feeds the race-day fluid plan, and shoe mileage.

Data is pulled on demand (page load, tab switch, or the refresh button) and
cached for 5 minutes (history for an hour) — no background polling. The app
and the CLI share `progress.py`, so the numbers always agree.

### Deploying (e.g. Render)

The repo deploys as-is: `Procfile` runs gunicorn with a **single worker** —
required, because the Garmin session and cache live in memory. Set these
environment variables on the host:

| Variable | What it does |
|---|---|
| `APP_PASSWORD` | Turns on the password page. Visitors only ever see this one field, never your Garmin email. |
| `FLASK_SECRET_KEY` | Any long random string; keeps you signed in across restarts. |
| `SUPABASE_URL`, `SUPABASE_KEY` | Where the Garmin token is kept so it survives restarts. Use the **secret** key — the server is the only thing that talks to Supabase. |
| `TZ` | Optional. Defaults to `Asia/Bangkok` so "today" is your day, not the server's UTC day. |

Supabase needs one table (enable Row Level Security, no policies — only the
secret key can reach it):

```sql
create table garmin_token (id text primary key, token_json jsonb, updated_at timestamptz default now());
```

Garmin rotates the refresh token every time the session refreshes, so the
server re-saves the token to Supabase whenever the file changes. Don't run
a second copy (e.g. locally) from the *same* saved token — the two will
invalidate each other. A separate owner sign-in on each machine gives each
its own independent session.

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
