"""Lightweight whitespace-expansion scheduler (base paper, Section 4).

Used by the AP when the MILP is unavailable or returns no optimal schedule.
Instead of solving the optimization, the AP exploits existing whitespace:

1. If an idle gap can hold the whole request, the earliest such gap is used
   and no WiFi FG moves.
2. Otherwise the largest gap is extended as much as possible: overlapping FGs
   are deferred past the window, and the window grows only while no deferred
   FG would miss its deadline or overflow the cycle.

The reserved interval holds the sensing window first, then the
interference-free window. The sensing window is mandatory (Eq. 17): if the
whitespace cannot hold it, it is reserved anyway and FGs are deferred or
postponed out of it. Only the interference-free part follows the rule that
the window may not cause a new postponement. If the cycle is too short for
the sensing window, the result has ``NO_SENSING_STATUS`` and no window.
"""

import math
import time
from collections.abc import Sequence

from cowic.exceptions import ConfigurationError
from cowic.models.frame_group import FrameGroup
from cowic.wifi.baselines import defer_around_window, find_whitespaces
from cowic.wifi.milp_scheduler import ScheduleResult

FALLBACK_STATUS = "fallback_whitespace"
# The cycle (after carryover) is shorter than the sensing window.
NO_SENSING_STATUS = "fallback_no_sensing"
# Exp 5 fallback: full window reserved, overlapping WiFi vacated.
VACATE_STATUS = "fallback_vacate"
_SEARCH_STEPS = 40  # Bisection steps; resolution is cycle_length / 2**40.


def fallback_whitespace_expansion(
    frame_groups: Sequence[FrameGroup],
    t_start_ms: float,
    t_end_ms: float,
    d_free_ms: float,
    d_sensing_ms: float = 0.0,
    channel_free_at: float = -math.inf,
) -> ScheduleResult:
    """Reserve ``d_sensing_ms + d_free_ms`` by using or extending a whitespace.

    When ``channel_free_at`` is ``-inf`` (no carryover), no FG is ever
    postponed: when the full request would force a postponement, the
    window is shortened instead. With carryover active, a FG whose natural
    position falls before ``channel_free_at`` is deferred past it and *can*
    be postponed if that deferral alone (independent of the reserved
    window) would miss its deadline or overflow the cycle - it already
    cannot transmit where it was originally placed.

    ``channel_free_at``: airtime already occupied before any FG in
    ``frame_groups`` (e.g. a carried-over FG still transmitting at
    ``t_start_ms``). A FG naturally positioned before it is deferred to
    start no earlier than ``channel_free_at``, exactly as it would be if it
    overlapped the reserved window directly - gap search alone (clipping
    ``find_whitespaces`` to ``[t_start_ms, t_end_ms]``) is not enough, since
    an FG positioned *before* an already-raised ``t_start_ms`` is invisible
    to it rather than correctly pushed later.

    A natural-looking "fitting" gap (found via ``find_whitespaces``, which
    only sees each FG's *original* position) can still force an avoidable
    postponement once ``channel_free_at`` pushes an earlier FG into it: the
    first fitting gap tried is skipped in favor of the next one that
    introduces no postponement beyond the no-window baseline; if none
    qualify, the extend-the-largest-gap path (:func:`_widest_window`) is
    used instead, which already applies the same baseline comparison.
    """
    if d_free_ms < 0 or d_sensing_ms < 0:
        raise ConfigurationError(
            f"Window durations must be non-negative, got d_free={d_free_ms}, "
            f"d_sensing={d_sensing_ms}"
        )
    began = time.perf_counter()
    gaps = find_whitespaces(frame_groups, t_start_ms, t_end_ms)
    need = d_sensing_ms + d_free_ms

    window = _choose_fitting_gap(frame_groups, gaps, need, t_end_ms, channel_free_at)
    if window is not None:
        window_start, window_end = window
    else:
        window_start, gap_end = max(
            gaps, key=lambda gap: gap[1] - gap[0], default=(t_start_ms, t_start_ms)
        )
        target = min(window_start + need, t_end_ms)
        window_end = _widest_window(
            frame_groups, window_start, gap_end, target, t_end_ms, channel_free_at
        )

    status = FALLBACK_STATUS
    if d_sensing_ms > 0 and window_end - window_start < d_sensing_ms:
        # Sensing is mandatory (Eq. 17). Reserve it in full, accept the
        # postponements it forces, then extend the free window only while
        # that adds no postponement beyond the sensing-only reservation.
        window_start = min(window_start, t_end_ms - d_sensing_ms)
        if window_start < t_start_ms:
            window_start = window_end = t_start_ms
            status = NO_SENSING_STATUS
        else:
            sense_only_end = window_start + d_sensing_ms
            window_end = _widest_window(
                frame_groups,
                window_start,
                sense_only_end,
                min(window_start + need, t_end_ms),
                t_end_ms,
                channel_free_at,
                baseline_end=sense_only_end,
            )

    sense_end = min(window_start + d_sensing_ms, window_end)
    return ScheduleResult(
        decisions=defer_around_window(
            frame_groups, window_start, window_end, t_end_ms, channel_free_at
        ),
        t_free_start_ms=sense_end,
        t_free_end_ms=window_end,
        t_sense_start_ms=window_start,
        t_sense_end_ms=sense_end,
        solver_status=status,
        solve_time_ms=(time.perf_counter() - began) * 1000.0,
    )


def vacate_window(
    frame_groups: Sequence[FrameGroup],
    t_start_ms: float,
    t_end_ms: float,
    d_free_ms: float,
    d_sensing_ms: float = 0.0,
    channel_free_at: float = -math.inf,
) -> ScheduleResult:
    """Exp 5 fallback: reserve the full request and vacate WiFi from it.

    Exp 5 forcibly removes every WiFi FG that overlaps the reserved window
    (Sec. 5.1), so the window is never shortened (Issue #38). It starts at
    the largest whitespace, moved earlier if needed so it fits the cycle.
    FGs that overlap it are deferred past it, or postponed if they then
    miss their deadline or the cycle end. If the cycle is shorter than the
    request, the result has ``NO_SENSING_STATUS`` and no window.
    """
    if d_free_ms < 0 or d_sensing_ms < 0:
        raise ConfigurationError(
            f"Window durations must be non-negative, got d_free={d_free_ms}, "
            f"d_sensing={d_sensing_ms}"
        )
    began = time.perf_counter()
    need = d_sensing_ms + d_free_ms
    gaps = find_whitespaces(frame_groups, t_start_ms, t_end_ms)
    largest_start = max(
        gaps, key=lambda gap: gap[1] - gap[0], default=(t_start_ms, t_start_ms)
    )[0]
    window_start = min(largest_start, t_end_ms - need)
    status = VACATE_STATUS
    if window_start < t_start_ms:
        window_start = window_end = t_start_ms
        status = NO_SENSING_STATUS
    else:
        window_end = window_start + need
    sense_end = min(window_start + d_sensing_ms, window_end)
    return ScheduleResult(
        decisions=defer_around_window(
            frame_groups, window_start, window_end, t_end_ms, channel_free_at
        ),
        t_free_start_ms=sense_end,
        t_free_end_ms=window_end,
        t_sense_start_ms=window_start,
        t_sense_end_ms=sense_end,
        solver_status=status,
        solve_time_ms=(time.perf_counter() - began) * 1000.0,
    )


def _baseline_postponed(
    frame_groups: Sequence[FrameGroup],
    window_start: float,
    t_end_ms: float,
    channel_free_at: float,
    window_end: float | None = None,
) -> frozenset[int]:
    """FG ids postponed by the baseline reservation ``[window_start, window_end]``.

    With ``window_end`` omitted the baseline is no window at all
    (``channel_free_at`` alone). The fallback passes the sensing-only
    reservation when the sensing window had to be forced.

    A FG already postponed here (e.g. carryover alone pushes it past its
    deadline) cannot be "saved" by any choice of window, so it must not
    count against a candidate window's feasibility.
    """
    return frozenset(
        d.fg_id
        for d in defer_around_window(
            frame_groups,
            window_start,
            window_start if window_end is None else window_end,
            t_end_ms,
            channel_free_at,
        )
        if d.is_postponed
    )


def _feasible(
    frame_groups: Sequence[FrameGroup],
    window_start: float,
    window_end: float,
    t_end_ms: float,
    channel_free_at: float,
    baseline_postponed: frozenset[int],
) -> bool:
    """True if this window introduces no postponement beyond the baseline."""
    decisions = defer_around_window(
        frame_groups, window_start, window_end, t_end_ms, channel_free_at
    )
    return all(not d.is_postponed or d.fg_id in baseline_postponed for d in decisions)


def _choose_fitting_gap(
    frame_groups: Sequence[FrameGroup],
    gaps: list[tuple[float, float]],
    need: float,
    t_end_ms: float,
    channel_free_at: float,
) -> tuple[float, float] | None:
    """Earliest gap (by original FG positions) that fits ``need`` without
    introducing a postponement beyond the no-window baseline, or ``None``
    if no fitting gap qualifies."""
    fitting = [gap for gap in gaps if gap[1] - gap[0] >= need]
    if not fitting:
        return None
    baseline = _baseline_postponed(
        frame_groups, fitting[0][0], t_end_ms, channel_free_at
    )
    for gap_start, _ in fitting:
        window_end = gap_start + need
        if _feasible(
            frame_groups, gap_start, window_end, t_end_ms, channel_free_at, baseline
        ):
            return gap_start, window_end
    return None


def _widest_window(
    frame_groups: Sequence[FrameGroup],
    window_start: float,
    safe_end: float,
    target_end: float,
    t_end_ms: float,
    channel_free_at: float = -math.inf,
    baseline_end: float | None = None,
) -> float:
    """Largest feasible window end in ``[safe_end, target_end]``.

    ``baseline_end`` sets the reservation whose postponements are accepted
    (default: none). The fallback passes the end of a forced sensing window.

    ``safe_end`` (the end of the idle gap) usually never moves an FG, but
    deferral can still cascade: an FG bumped to clear the window's start may
    itself now overlap ``safe_end``, forcing a further, otherwise-avoidable
    postponement. If even ``safe_end`` is infeasible, return ``window_start``
    (an empty window) instead, so the fallback never introduces a
    postponement that would not occur without it. See
    :func:`fallback_whitespace_expansion` for ``channel_free_at``.

    Feasibility is judged against a no-window baseline (``channel_free_at``
    alone, no reserved interval): an FG already postponed there - e.g. one
    that carryover alone pushes past its deadline - cannot be "saved" by
    shrinking the window, so it must not count against the window's size.
    Only postponements the window itself newly introduces are disqualifying.
    """
    baseline = _baseline_postponed(
        frame_groups, window_start, t_end_ms, channel_free_at, baseline_end
    )

    def feasible(end: float) -> bool:
        return _feasible(
            frame_groups, window_start, end, t_end_ms, channel_free_at, baseline
        )

    if feasible(target_end):
        return target_end
    if not feasible(safe_end):
        return window_start
    low, high = safe_end, target_end
    for _ in range(_SEARCH_STEPS):
        mid = (low + high) / 2.0
        if feasible(mid):
            low = mid
        else:
            high = mid
    return low
