from garminconnect.workout import (
    ConditionType,
    ExecutableStep,
    RepeatGroup,
    RunningWorkout,
    StepType,
    TargetType,
    WorkoutSegment,
    create_repeat_group,
)

from plan_data import PACES

_ROLE_STEP_TYPE = {
    "warmup": (StepType.WARMUP, "warmup", 1),
    "main": (StepType.INTERVAL, "interval", 3),
    "cooldown": (StepType.COOLDOWN, "cooldown", 2),
}

_PACE_ZONE_TYPE = {
    "workoutTargetTypeId": TargetType.PACE_ZONE,
    "workoutTargetTypeKey": "pace.zone",  # dotted, matching Garmin's own convention (e.g. "no.target") -
    "displayOrder": 6,                     # "pace_zone" (underscore) uploads fine but the app can't match
}                                           # the key to a known target type, so it renders as "-:----:--"


_HR_ZONE_TYPE = {
    "workoutTargetTypeId": TargetType.HEART_RATE_ZONE,
    "workoutTargetTypeKey": "heart.rate.zone",  # dotted, same convention as pace.zone
    "displayOrder": 4,
}


def _hr_target(low_bpm: int, high_bpm: int) -> dict:
    """A custom heart-rate range in bpm, not one of Garmin's predefined zones.

    Same trick as the pace target: zoneNumber is omitted entirely. Sending 0
    makes the server look up predefined zone 0, which does not exist, and it
    silently nulls the bpm values instead of erroring.

    This exists because pace targets do not work for this runner. Asked for
    5:46 he ran 5:17 and called it comfortable; four marathon-pace sessions in
    a row finished above the heart-rate ceiling. A pace target leaves restraint
    to a feeling that under-reports by about ten beats. An HR target makes the
    watch do the governing.
    """
    return {
        "targetType": _HR_ZONE_TYPE,
        "targetValueOne": float(low_bpm),
        "targetValueTwo": float(high_bpm),
    }


def _distance_condition(display_order: int = 3) -> dict:
    return {
        "conditionTypeId": ConditionType.DISTANCE,
        "conditionTypeKey": "distance",
        "displayOrder": display_order,
        "displayable": True,
    }


def _pace_to_sec_per_km(pace: str) -> float:
    minutes, seconds = pace.split(":")
    return int(minutes) * 60 + int(seconds)


def _pace_target(kind: str) -> dict:
    slow_pace, fast_pace = PACES[kind]
    slow_sec = _pace_to_sec_per_km(slow_pace)
    fast_sec = _pace_to_sec_per_km(fast_pace)
    return {
        "targetType": _PACE_ZONE_TYPE,
        "targetValueOne": 1000.0 / slow_sec,
        "targetValueTwo": 1000.0 / fast_sec,
        # No zoneNumber: sending 0 makes Garmin's server treat it as "look up
        # predefined zone #0", which doesn't exist - it silently nulls out
        # targetValueOne/Two instead of erroring. Omitting the field entirely
        # (confirmed against a real Garmin-generated workout, which sends
        # zoneNumber: null) is what makes a custom pace range actually stick.
    }


def _avg_pace_sec_per_km(kind: str) -> float:
    slow_pace, fast_pace = PACES[kind]
    return (_pace_to_sec_per_km(slow_pace) + _pace_to_sec_per_km(fast_pace)) / 2.0


_KIND_LABEL = {"mp": "MP", "tempo": "Tempo", "easy": "Easy", "long": "Long", "recovery": "Recovery"}


def pace_summary(session: dict) -> str | None:
    """Plain-text target pace reference, since the pace_zone target on each
    step isn't obviously visible everywhere the workout shows up."""
    parts = []
    for block in session["blocks"]:
        if block["role"] not in ("main", "repeat"):
            continue
        if block.get("hr"):
            lo, hi = block["hr"]
            if block["role"] == "repeat":
                parts.append(f"{block['reps']}x{block['rep_km']:g}km @ HR {lo}-{hi} (easy float between)")
            else:
                parts.append(f"{block['km']:g}km @ HR {lo}-{hi}")
            continue
        slow, fast = PACES[block["kind"]]
        label = f"{_KIND_LABEL[block['kind']]} {slow}-{fast}/km"
        if block["role"] == "repeat":
            label += " (float recovery easy between reps)"
        parts.append(label)
    if not parts:
        return None
    kind = "Target" if any("HR" in x for x in parts) else "Target pace"
    return kind + ": " + " -> ".join(parts)


def _block_target(block: dict) -> dict:
    """A block's "hr" window, when it has one, otherwise its kind's pace band."""
    hr = block.get("hr")
    return _hr_target(*hr) if hr else _pace_target(block["kind"])


def _build_simple_step(block: dict, step_order: int) -> ExecutableStep:
    step_type_id, step_type_key, display_order = _ROLE_STEP_TYPE[block["role"]]
    return ExecutableStep(
        stepOrder=step_order,
        stepType={
            "stepTypeId": step_type_id,
            "stepTypeKey": step_type_key,
            "displayOrder": display_order,
        },
        endCondition=_distance_condition(),
        endConditionValue=block["km"] * 1000.0,
        **_block_target(block),
    )


def _build_repeat_group(block: dict, step_order: int) -> RepeatGroup:
    """A repeated distance interval + float recovery, shown on-watch as "N reps"."""
    interval_step = ExecutableStep(
        stepOrder=step_order + 1,
        stepType={"stepTypeId": StepType.INTERVAL, "stepTypeKey": "interval", "displayOrder": 3},
        endCondition=_distance_condition(),
        endConditionValue=block["rep_km"] * 1000.0,
        **_block_target(block),
    )
    recovery_step = ExecutableStep(
        stepOrder=step_order + 2,
        stepType={"stepTypeId": StepType.RECOVERY, "stepTypeKey": "recovery", "displayOrder": 4},
        endCondition=_distance_condition(),
        endConditionValue=block["recovery_km"] * 1000.0,
        **_pace_target(block.get("recovery_kind", "recovery")),
    )
    return create_repeat_group(block["reps"], [interval_step, recovery_step], step_order)


def _step_span(block: dict) -> int:
    # A repeat group occupies 3 stepOrder slots: the group, its interval, its recovery.
    return 3 if block["role"] == "repeat" else 1


def _block_distance_km(block: dict) -> float:
    if block["role"] == "repeat":
        return block["reps"] * (block["rep_km"] + block["recovery_km"])
    return block["km"]


def session_distance_km(session: dict) -> float:
    """Total distance a session covers, including warmup/cooldown/recovery jogs."""
    return sum(_block_distance_km(block) for block in session["blocks"])


def session_duration_min(session: dict) -> int:
    """Estimated duration at the middle of each block's target pace range."""
    return round(sum(_block_duration_secs(block) for block in session["blocks"]) / 60)


def session_steps(session: dict) -> list[dict]:
    """The session's blocks as display-ready steps, with each block's own pace range."""
    steps = []
    for block in session["blocks"]:
        slow, fast = PACES[block["kind"]]
        if block["role"] == "repeat":
            rec_slow, rec_fast = PACES[block.get("recovery_kind", "recovery")]
            steps.append({
                "role": "repeat",
                "kind": block["kind"],
                "reps": block["reps"],
                "km": block["rep_km"],
                "paceSlow": slow,
                "paceFast": fast,
                "recoveryKm": block["recovery_km"],
                "recoveryPaceSlow": rec_slow,
                "recoveryPaceFast": rec_fast,
            })
        else:
            steps.append({"role": block["role"], "kind": block["kind"], "km": block["km"], "paceSlow": slow, "paceFast": fast})
    return steps


def _block_duration_secs(block: dict) -> float:
    if block["role"] == "repeat":
        return block["reps"] * (
            block["rep_km"] * _avg_pace_sec_per_km(block["kind"])
            + block["recovery_km"] * _avg_pace_sec_per_km(block.get("recovery_kind", "recovery"))
        )
    return block["km"] * _avg_pace_sec_per_km(block["kind"])


def build_workout(session: dict) -> RunningWorkout:
    blocks = session["blocks"]
    steps: list[ExecutableStep | RepeatGroup] = []
    order = 1
    for block in blocks:
        if block["role"] == "repeat":
            steps.append(_build_repeat_group(block, order))
        else:
            steps.append(_build_simple_step(block, order))
        order += _step_span(block)

    total_seconds = sum(_block_duration_secs(block) for block in blocks)
    description = " | ".join(p for p in (pace_summary(session), session.get("note")) if p) or None
    return RunningWorkout(
        workoutName=session["name"],
        estimatedDurationInSecs=round(total_seconds),
        description=description,
        workoutSegments=[
            WorkoutSegment(
                segmentOrder=1,
                sportType={"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
                workoutSteps=steps,
            )
        ],
    )
