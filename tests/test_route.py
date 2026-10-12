from route import _continuity


def _stream(*segs):
    """(seconds, metres_per_second) legs -> cumulative (dist, elapsed) samples."""
    out, dist, t = [(0.0, 0.0, None)], 0.0, 0.0
    for secs, speed in segs:
        for _ in range(int(secs)):
            t += 1.0
            dist += speed
            out.append((dist, t, None))
    return out


def test_continuity_reads_walks_and_longest_stretch():
    # 10 min run, 2 min walk, 20 min run, 1 min walk, 5 min run
    c = _continuity(_stream((600, 3.0), (120, 1.2), (1200, 3.0), (60, 1.0), (300, 3.0)))
    assert c["breaks"] == 2
    assert c["longestRunMin"] == 20
    assert c["walkPct"] == round(100 * 180 / 2280)


def test_continuous_run_has_no_breaks():
    c = _continuity(_stream((1800, 3.2)))
    assert c == {"walkPct": 0, "longestRunMin": 30, "breaks": 0}


def test_too_short_to_judge_returns_none():
    assert _continuity(_stream((30, 3.0))) is None
