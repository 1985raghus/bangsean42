"""Bangsaen Marathon (2026-11-15), Forerunner 935.

Ambitious build, by explicit request: ACTIVE_PACE_SET is "stretch"
(sub-4:00, 5:41/km) and distances are back to the original ~490km/11-week
version (peak week ~59.5km, 30K peak long run).

This is a real ~10x jump from the account's actual ~4km/week base over the
16 weeks before the plan starts, and neither of the account's last two
Bangsaen finishes (4:42:00 in 2024, 4:46:24 in 2025) came close to sub-4:00
pace. That data is why "primary" (4:35:00) and "floor" (4:45:00) exist below
as more conservative tiers - switch ACTIVE_PACE_SET to either (or ask me to)
if training data says to back off. The Coach KPIs in app.py / track_progress.py
will flag ramp-rate and long-run-share issues live either way; they're
visibility, not a gate - this file is what actually decides what gets pushed.

4 running days/week: Tue easy, Thu easy, Fri quality, Sun long; rest
Mon/Wed/Sat. Built around a mandatory Tuesday office day and Mon/Thu/Fri
WFH - Week 1 still runs the original Tue/Thu/Sat/Sun cadence (one-time,
already underway when the schedule changed); every week from W2 on uses
the new one. Quality sessions (tempo/MP) are broken into repeated distance
intervals with a float recovery jog between reps, so the watch shows
"interval 1 of N" the same way a Garmin Coach plan does, instead of one
flat continuous block.

`track_progress.py` estimates a predicted finish time from actual MP-effort
session paces once they exist, and compares it against PRIMARY_TIME_SEC /
FLOOR_TIME_SEC / STRETCH_TIME_SEC below regardless of which tier is active.
"""

MARATHON_KM = 42.195

# Aerobic bands widened 2026-09-24. The old easy/long zones were derived straight
# from goal pace and were far too fast to run aerobically in 28-30C: easy runs came
# in at 6:31/km with HR 162-170, only ~12 beats under tempo. Slowing to 7:10-7:30
# dropped HR to 151-153 - the same system that builds base. These bands are now set
# off marathon pace by effort, not arithmetic: easy MP+60..110s, long MP+65..100s,
# recovery MP+115..160s. Long sits alongside easy, not faster: the quality in a long
# run belongs in its MP finish, not in its steady kilometres.
PACE_SETS = {
    "primary": {  # 4:35:00 marathon (6:31/km)
        "recovery": ("9:10", "8:25"),
        "easy": ("8:20", "7:30"),
        "long": ("8:10", "7:35"),
        "mp": ("6:36", "6:26"),
        "tempo": ("6:11", "6:01"),
    },
    "floor": {  # 4:45:00 marathon (6:45/km) - safety net, ~matches recent history
        "recovery": ("9:25", "8:40"),
        "easy": ("8:35", "7:45"),
        "long": ("8:25", "7:50"),
        "mp": ("6:50", "6:40"),
        "tempo": ("6:25", "6:15"),
    },
    "stretch": {  # sub-4:00 marathon (5:41/km) - active
        "recovery": ("8:20", "7:35"),
        "easy": ("7:30", "6:40"),
        "long": ("7:20", "6:45"),
        "mp": ("5:46", "5:36"),
        "tempo": ("5:21", "5:11"),
    },
}

ACTIVE_PACE_SET = "stretch"
PACES = PACE_SETS[ACTIVE_PACE_SET]


def _time_to_sec(hms: str) -> int:
    hours, minutes, seconds = hms.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


PRIMARY_TIME_SEC = _time_to_sec("4:35:00")
FLOOR_TIME_SEC = _time_to_sec("4:45:00")
STRETCH_TIME_SEC = _time_to_sec("4:00:00")

STRIDES_NOTE = "Finish with 4-6 x 20s relaxed strides, full recovery between."

# Execution notes added after the W1-W3 review (2026-09-20). Every quality
# session so far came in faster than its target band (MP reps 5:13-5:35 vs
# 5:46-5:36; tempo reps 5:09/4:52/4:51 vs 5:21-5:11; the 20K's MP finish at
# 5:31 and HR 181), while easy runs averaged HR 168 - only ~12 beats under
# tempo. The distances were fine; the efforts were not, so the fix is written
# into the workout descriptions the watch actually shows.
EASY_NOTE = (
    "Effort, not pace: keep HR at or under ~155. In this heat that lands around 7:10-7:45/km, "
    "and slower is fine - the pace is just what that effort gives on the day. Finish with "
    "4-6 x 20s relaxed strides if the legs feel flat, to keep the turnover."
)
LONG_NOTE = (
    "Steady and easy the whole way: HR 155-162, not the 167-168 these have been running. "
    "If the last 3km drop off by more than 15s/km, you started too fast."
)
TEMPO_NOTE = (
    "Start rep 1 at the SLOW end of the tempo band and stay there. 18 Sep ran 5:09 / 4:52 / 4:51 "
    "with HR climbing to 185 - even reps beat fast ones."
)
MP_NOTE = (
    "Marathon effort, not tempo: hold the slow end of the MP band, HR in the low 170s. "
    "11 Sep's reps ran 5:13-5:35 at HR 180+, which is threshold work in disguise."
)
MP_FINISH_NOTE = (
    "The finish is marathon effort, not a time trial. 20 Sep's 5K finish ran 5:31/km at HR 181 - "
    "hold the MP band and keep HR under ~178. The point is race pace when tired, not racing it."
)


def _session(date, name, blocks, note=None):
    return {"date": date, "name": name, "blocks": blocks, "note": note}


def _simple(date, name, kind, km, note=None):
    return _session(date, name, [{"role": "main", "kind": kind, "km": km}], note)


def _reps(date, name, kind, reps, rep_km, recovery_km=0.4, warmup_km=2.0, cooldown_km=2.0, note=None):
    return _session(
        date,
        name,
        [
            {"role": "warmup", "kind": "easy", "km": warmup_km},
            {
                "role": "repeat",
                "kind": kind,
                "reps": reps,
                "rep_km": rep_km,
                "recovery_km": recovery_km,
                "recovery_kind": "recovery",
            },
            {"role": "cooldown", "kind": "easy", "km": cooldown_km},
        ],
        note,
    )


def _long_with_finish(date, name, long_km, mp_km, note=None):
    return _session(
        date,
        name,
        [
            {"role": "main", "kind": "long", "km": long_km},
            {"role": "main", "kind": "mp", "km": mp_km},
        ],
        note,
    )


SESSIONS = [
    # Week 1 (2026-09-01 to 09-07) — one-time transition week under the old
    # Tue/Thu/Sat/Sun cadence: the Tue quality session already ran (moved to
    # Wed 09-02, "today" at the time), so it's left as historical record.
    # From Week 2 on, the schedule is Tue/Thu/Fri/Sun (rest Mon/Wed/Sat),
    # chosen around a mandatory Tuesday office day and Mon/Thu/Fri WFH:
    #   Sun long -> Mon rest -> Tue easy -> Wed rest -> Thu easy -> Fri
    #   quality -> Sat rest -> Sun long. Two full rest days after the long
    #   run before the next hard effort; one rest day before both the long
    #   run and after the quality session. Session content (distance/pace/
    #   reps) is unchanged from the original Tue/Thu/Sat/Sun plan - only
    #   which day of the week each one lands on changed: the quality session
    #   moved from Tue to Fri, and the second easy day moved from Sat to Tue
    #   (carrying its strides note, back to the original Tue-strides spot).
    _reps("2026-09-02", "W1 Tue - Tempo Intervals (3x1.5K @ Tempo)", "tempo", 3, 1.5, recovery_km=0.3),
    _simple("2026-09-03", "W1 Thu - Easy (8K)", "easy", 8),
    _simple("2026-09-05", "W1 Sat - Easy + Strides (8K)", "easy", 8, STRIDES_NOTE),
    _simple("2026-09-06", "W1 Sun - Long Run (16K)", "long", 16),
    # Week 2
    _simple("2026-09-08", "W2 Tue - Easy + Strides (8K)", "easy", 8, STRIDES_NOTE),
    _simple("2026-09-10", "W2 Thu - Easy (9K)", "easy", 9),
    _reps("2026-09-11", "W2 Fri - Marathon Pace Intervals (3x2K @ MP)", "mp", 3, 2.0),
    _simple("2026-09-13", "W2 Sun - Long Run (18K)", "long", 18),
    # Week 3
    _simple("2026-09-15", "W3 Tue - Easy + Strides (9K)", "easy", 9, STRIDES_NOTE),
    _simple("2026-09-17", "W3 Thu - Easy (9K)", "easy", 9),
    _reps("2026-09-18", "W3 Fri - Tempo Intervals (3x2K @ Tempo)", "tempo", 3, 2.0),
    _long_with_finish("2026-09-20", "W3 Sun - Long Run w/ MP Finish (20K)", 15, 5),
    # Week 4 - cutback
    _simple("2026-09-22", "W4 Tue - Easy (7K)", "easy", 7, EASY_NOTE),
    _simple("2026-09-24", "W4 Thu - Easy (8K)", "easy", 8, EASY_NOTE),
    _reps("2026-09-25", "W4 Fri - Tempo Intervals (2x1.5K @ Tempo)", "tempo", 2, 1.5, recovery_km=0.3, note=TEMPO_NOTE),
    _simple("2026-09-27", "W4 Sun - Long Run, easy (15K)", "long", 15, LONG_NOTE),
    # Week 5 - volume smoothed from 54.6km after the W1-W3 review: the missed
    # W3 Tuesday left 46.2km (W2) as the biggest week actually run, so full
    # volume returns at ~+12% on that instead of +19%. The MP session keeps
    # its full 8km of work - intensity discipline is the goal, not less work.
    _simple("2026-09-29", "W5 Tue - Easy + Strides (8K)", "easy", 8, EASY_NOTE + " " + STRIDES_NOTE),
    _simple("2026-10-01", "W5 Thu - Easy (9K)", "easy", 9, EASY_NOTE),
    _reps("2026-10-02", "W5 Fri - Marathon Pace Intervals (4x2K @ MP)", "mp", 4, 2.0, note=MP_NOTE),
    _simple("2026-10-04", "W5 Sun - Long Run (21K)", "long", 21, LONG_NOTE),
    # Week 6
    _simple("2026-10-06", "W6 Tue - Easy + Strides (9K)", "easy", 9, EASY_NOTE + " " + STRIDES_NOTE),
    _simple("2026-10-08", "W6 Thu - Easy (10K)", "easy", 10, EASY_NOTE),
    _reps("2026-10-09", "W6 Fri - Tempo Intervals (3x2K @ Tempo)", "tempo", 3, 2.0, note=TEMPO_NOTE),
    _long_with_finish("2026-10-11", "W6 Sun - Long Run w/ MP Finish (24K)", 16, 8, MP_FINISH_NOTE),
    # Week 7
    _simple("2026-10-13", "W7 Tue - Easy + Strides (9K)", "easy", 9, EASY_NOTE + " " + STRIDES_NOTE),
    _simple("2026-10-15", "W7 Thu - Easy (10K)", "easy", 10, EASY_NOTE),
    _reps("2026-10-16", "W7 Fri - Marathon Pace Intervals (3x3K @ MP)", "mp", 3, 3.0, recovery_km=0.5, note=MP_NOTE),
    _simple("2026-10-18", "W7 Sun - Long Run, peak distance (26K)", "long", 26, LONG_NOTE),
    # Week 8 - peak
    _simple("2026-10-20", "W8 Tue - Easy + Strides (8K)", "easy", 8, EASY_NOTE + " " + STRIDES_NOTE),
    _simple("2026-10-22", "W8 Thu - Easy (10K)", "easy", 10, EASY_NOTE),
    _reps("2026-10-23", "W8 Fri - Tempo Intervals (3x2K @ Tempo)", "tempo", 3, 2.0, note=TEMPO_NOTE),
    _long_with_finish("2026-10-25", "W8 Sun - Peak Long Run w/ MP Finish (30K)", 20, 10, MP_FINISH_NOTE),
    # Week 9 - taper begins
    _simple("2026-10-27", "W9 Tue - Easy (7K)", "easy", 7, EASY_NOTE),
    _simple("2026-10-29", "W9 Thu - Easy (8K)", "easy", 8, EASY_NOTE),
    _reps("2026-10-30", "W9 Fri - Marathon Pace Intervals (3x2K @ MP)", "mp", 3, 2.0, note=MP_NOTE),
    _simple("2026-11-01", "W9 Sun - Long Run, easy (18K)", "long", 18, LONG_NOTE),
    # Week 10 - taper
    _simple("2026-11-03", "W10 Tue - Easy + Strides (6K)", "easy", 6, EASY_NOTE + " " + STRIDES_NOTE),
    _simple("2026-11-05", "W10 Thu - Easy (6K)", "easy", 6, EASY_NOTE),
    _reps("2026-11-06", "W10 Fri - Marathon Pace Sharpener (3x1K @ MP)", "mp", 3, 1.0, recovery_km=0.2, note=MP_NOTE),
    _long_with_finish("2026-11-08", "W10 Sun - Easy w/ MP pickups (12K)", 8, 4, MP_FINISH_NOTE),
    # Week 11 - race week (Sun is race day, not a training run). Fri's
    # "easy + strides" (moved from the old Tue slot) doubles as a classic
    # pre-race opener, 2 days out.
    _simple("2026-11-10", "W11 Tue - Shakeout + Strides (3K)", "recovery", 3, STRIDES_NOTE),
    _simple("2026-11-12", "W11 Thu - Very Easy Shakeout (4K)", "recovery", 4),
    _simple("2026-11-13", "W11 Fri - Easy + Strides (5K)", "easy", 5, STRIDES_NOTE),
]

RACE = {
    "date": "2026-11-15",
    "name": "Bangsaen Marathon - RACE DAY",
    "distance_km": MARATHON_KM,
    "primary_time": "4:35:00",
    "primary_pace": "6:31/km",
    "floor_time": "4:45:00",
    "floor_pace": "6:45/km",
    "stretch_time": "4:00:00",
    "stretch_pace": "5:41/km",
}
