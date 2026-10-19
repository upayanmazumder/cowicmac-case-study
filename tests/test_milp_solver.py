"""Unit tests for the MILP scheduler (Eq. 11-17)."""

import pulp
import pytest

from cowic.exceptions import ConfigurationError
from cowic.models.frame_group import FrameGroup
from cowic.wifi.milp_scheduler import (
    OPTIMAL_STATUS,
    UNPROVEN_DROP_STATUS,
    UNPROVEN_STATUS,
    HardConstraintMILPScheduler,
    MILPScheduler,
)


def _fg(
    fg_id: int,
    start: float,
    duration: float = 10.0,
    deadline: float = 500.0,
    arrival: float = 0.0,
) -> FrameGroup:
    return FrameGroup(
        fg_id=fg_id,
        arrival_time_ms=arrival,
        duration_ms=duration,
        deadline_ms=deadline,
        original_start_ms=start,
        qos_sensitive=False,
    )


def test_milp_scheduler_instantiation() -> None:
    """Verify MILPScheduler initialization with default weights."""
    scheduler = MILPScheduler(gamma=10.0, lambda_weight=1.0, mu_base=100.0)
    assert scheduler.gamma == 10.0
    assert scheduler.lambda_weight == 1.0
    assert scheduler.mu_base == 100.0


def test_schedule_rejects_invalid_horizon() -> None:
    """t_end_ms must be strictly greater than t_start_ms."""
    scheduler = MILPScheduler()
    try:
        scheduler.schedule(
            [], d_free_ms=10.0, d_sensing_ms=5.0, t_start_ms=100.0, t_end_ms=50.0
        )
    except ConfigurationError:
        pass
    else:
        raise AssertionError("expected ConfigurationError for invalid horizon")


def test_schedule_rejects_negative_window_durations() -> None:
    """Negative free/sensing window durations are invalid."""
    scheduler = MILPScheduler()
    try:
        scheduler.schedule(
            [], d_free_ms=-1.0, d_sensing_ms=5.0, t_start_ms=0.0, t_end_ms=100.0
        )
    except ConfigurationError:
        pass
    else:
        raise AssertionError("expected ConfigurationError for negative d_free_ms")


def test_schedule_empty_frame_groups_places_windows_at_start() -> None:
    """With no FGs, the free window is placed first, then the sensing window."""
    scheduler = MILPScheduler()
    result = scheduler.schedule(
        [], d_free_ms=30.0, d_sensing_ms=10.0, t_start_ms=0.0, t_end_ms=100.0
    )
    assert result.decisions == []
    assert result.t_free_start_ms == 0.0
    assert result.t_free_end_ms == 30.0
    assert result.t_sense_start_ms == 30.0
    assert result.t_sense_end_ms == 40.0
    assert result.solver_status == "Optimal"


def test_schedule_empty_frame_groups_infeasible_when_windows_overflow() -> None:
    """Windows that cannot both fit in the horizon are reported infeasible."""
    scheduler = MILPScheduler()
    result = scheduler.schedule(
        [], d_free_ms=80.0, d_sensing_ms=30.0, t_start_ms=0.0, t_end_ms=75.0
    )
    assert result.solver_status == "Infeasible"


def test_schedule_single_fg_outside_windows_keeps_original_start() -> None:
    """A single FG with ample slack should not need to move."""
    scheduler = MILPScheduler()
    fgs = [_fg(1, start=0.0, duration=10.0, deadline=500.0)]
    result = scheduler.schedule(
        fgs, d_free_ms=20.0, d_sensing_ms=10.0, t_start_ms=0.0, t_end_ms=200.0
    )
    assert result.solver_status == "Optimal"
    assert len(result.decisions) == 1
    decision = result.decisions[0]
    assert decision.fg_id == 1
    assert decision.is_postponed is False


def test_schedule_resolves_overlap_with_free_window() -> None:
    """An FG originally overlapping the free window must be moved clear of it."""
    scheduler = MILPScheduler()
    # FG originally at [10, 30) would overlap a free window placed at [0, 40).
    fgs = [_fg(1, start=10.0, duration=20.0, deadline=500.0, arrival=0.0)]
    result = scheduler.schedule(
        fgs, d_free_ms=40.0, d_sensing_ms=10.0, t_start_ms=0.0, t_end_ms=200.0
    )
    assert result.solver_status == "Optimal"
    decision = result.decisions[0]
    fg_end = decision.scheduled_start_ms + 20.0
    free_start, free_end = result.t_free_start_ms, result.t_free_end_ms
    overlaps_free = decision.scheduled_start_ms < free_end and free_start < fg_end
    assert not overlaps_free, "scheduled FG must not overlap the free window"


def test_schedule_never_overlaps_sensing_window() -> None:
    """Eq. 17: no WiFi FG may ever overlap the sensing window."""
    scheduler = MILPScheduler()
    fgs = [
        _fg(1, start=0.0, duration=15.0, deadline=500.0),
        _fg(2, start=20.0, duration=15.0, deadline=500.0),
        _fg(3, start=45.0, duration=15.0, deadline=500.0),
    ]
    result = scheduler.schedule(
        fgs, d_free_ms=0.0, d_sensing_ms=10.0, t_start_ms=0.0, t_end_ms=120.0
    )
    assert result.solver_status == "Optimal"
    sense_start, sense_end = result.t_sense_start_ms, result.t_sense_end_ms
    for decision in result.decisions:
        fg = next(f for f in fgs if f.fg_id == decision.fg_id)
        fg_end = decision.scheduled_start_ms + fg.duration_ms
        overlaps_sense = (
            decision.scheduled_start_ms < sense_end and sense_start < fg_end
        )
        assert not overlaps_sense, f"FG {fg.fg_id} overlaps the sensing window"


def test_schedule_produces_non_overlapping_frame_groups() -> None:
    """Scheduled (non-postponed) FGs must never overlap one another."""
    scheduler = MILPScheduler()
    fgs = [
        _fg(1, start=0.0, duration=10.0, deadline=300.0),
        _fg(2, start=5.0, duration=10.0, deadline=300.0),
        _fg(3, start=8.0, duration=10.0, deadline=300.0),
    ]
    result = scheduler.schedule(
        fgs, d_free_ms=0.0, d_sensing_ms=0.0, t_start_ms=0.0, t_end_ms=200.0
    )
    assert result.solver_status == "Optimal"
    scheduled = [
        (d, next(f for f in fgs if f.fg_id == d.fg_id).duration_ms)
        for d in result.decisions
        if not d.is_postponed
    ]
    intervals = sorted(
        (d.scheduled_start_ms, d.scheduled_start_ms + dur) for d, dur in scheduled
    )
    for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
        assert e1 <= s2 + 1e-6, "scheduled FGs must not overlap"


def test_schedule_postpones_urgent_fg_only_when_unavoidable() -> None:
    """A FG that cannot fit before its deadline within the horizon is postponed."""
    scheduler = MILPScheduler()
    # Deadline is before t_end_ms, so the FG must be scheduled this cycle if
    # feasible; here it is feasible (ample room), so it should NOT postpone.
    fgs = [_fg(1, start=0.0, duration=10.0, deadline=50.0, arrival=0.0)]
    result = scheduler.schedule(
        fgs, d_free_ms=0.0, d_sensing_ms=0.0, t_start_ms=0.0, t_end_ms=100.0
    )
    assert result.solver_status == "Optimal"
    assert result.decisions[0].is_postponed is False


def test_schedule_postponed_fg_reported_at_cycle_end() -> None:
    """A postponed FG's s_prime is unconstrained once P_k=1 (Eq. 14's
    causality/deadline terms relax), so the solver can park it anywhere,
    typically near original_start_ms to minimize the irrelevant Eq. 12
    delay term. Reporting must not leak that phantom position: a
    postponed FG is always reported at t_end_ms, matching every other
    postponement in the codebase (defer_around_window, _carryover_busy)."""
    scheduler = MILPScheduler()
    # Arrives at 95 needing 20ms: s >= 95 and s + 20 <= t_end_ms (100) is
    # impossible, forcing postponement regardless of cost weights.
    late_fg = _fg(1, start=95.0, duration=20.0, deadline=300.0, arrival=95.0)
    result = scheduler.schedule(
        [late_fg], d_free_ms=0.0, d_sensing_ms=0.0, t_start_ms=0.0, t_end_ms=100.0
    )
    assert result.solver_status == "Optimal"
    decision = result.decisions[0]
    assert decision.is_postponed is True
    assert decision.scheduled_start_ms == 100.0
    assert decision.delay_ms == 5.0


def test_schedule_overlaps_the_cheaper_fg_instead_of_moving_either() -> None:
    """Eq. 1 term 1 prices overlap by FG duration: with two FGs packed
    back-to-back and no room for d_free elsewhere, the optimal free
    window overlaps the 10 ms FG (cost 10) rather than the 30 ms FG
    (cost 30). Neither FG needs to move to achieve this (the window
    simply lands on the cheaper FG's existing slot), so this is strictly
    cheaper than any solution that pays lambda * y to move either FG
    clear, verifying the MILP actually minimizes Eq. 1 rather than
    defaulting to "do nothing"."""
    scheduler = MILPScheduler(mu_base=1e6)
    cheap = _fg(1, start=0.0, duration=10.0, deadline=500.0, arrival=0.0)
    costly = _fg(2, start=10.0, duration=30.0, deadline=500.0, arrival=10.0)
    result = scheduler.schedule(
        [cheap, costly], d_free_ms=10.0, d_sensing_ms=0.0, t_start_ms=0.0, t_end_ms=40.0
    )
    assert result.solver_status == "Optimal"
    assert result.t_free_start_ms == pytest.approx(0.0)
    assert result.t_free_end_ms == pytest.approx(10.0)
    decisions = {d.fg_id: d for d in result.decisions}
    assert decisions[1].scheduled_start_ms == pytest.approx(0.0)
    assert decisions[2].scheduled_start_ms == pytest.approx(10.0)
    assert decisions[1].delay_ms == pytest.approx(0.0)
    assert decisions[2].delay_ms == pytest.approx(0.0)
    assert not decisions[1].is_postponed and not decisions[2].is_postponed


def test_schedule_stale_fg_does_not_corrupt_big_m() -> None:
    """A FG whose deadline sits far outside the horizon doesn't need a
    looser Big-M than the model provides. It cannot be served (its deadline
    has already passed), so since Issue #38 it is dropped and the cycle
    still solves, instead of the whole model going Infeasible."""
    scheduler = MILPScheduler()
    stale = _fg(1, start=1010.0, duration=10.0, deadline=5.0, arrival=0.0)
    result = scheduler.schedule(
        [stale], d_free_ms=0.0, d_sensing_ms=0.0, t_start_ms=1000.0, t_end_ms=1100.0
    )
    assert result.solver_status == "Optimal"
    assert result.decisions[0].is_postponed is True
    assert result.decisions[0].scheduled_start_ms == 1100.0


def test_schedule_infeasible_problem_reports_all_postponed_with_no_window() -> None:
    """A non-Optimal status must never silently report FGs at their
    original position with a seemingly-valid window: both could overlap
    the sensing window, breaking the hard Eq. 17 guarantee for a caller
    that only checks the schedule, not solver_status."""
    scheduler = MILPScheduler()
    # The free and sensing windows (80 + 30 ms) cannot fit a 100 ms cycle.
    # A single unservable FG no longer causes this (Issue #38 drops it), so
    # the windows are what make the model infeasible here.
    fg = _fg(1, start=10.0, duration=5.0, deadline=500.0, arrival=0.0)
    result = scheduler.schedule(
        [fg], d_free_ms=80.0, d_sensing_ms=30.0, t_start_ms=0.0, t_end_ms=100.0
    )
    assert result.solver_status != "Optimal"
    assert result.t_free_start_ms == result.t_free_end_ms
    assert result.t_sense_start_ms == result.t_sense_end_ms
    (decision,) = result.decisions
    assert decision.is_postponed is True


def test_schedule_non_optimal_clamps_delay_for_future_fg() -> None:
    """A FG whose original_start_ms is after t_end_ms (not yet arrived)
    must report delay_ms=0.0 in the non-optimal path, not a negative
    value, matching the clamping already applied on the optimal path."""
    scheduler = MILPScheduler()
    fg = _fg(1, start=10.0, duration=5.0, deadline=500.0, arrival=0.0)
    future_fg = _fg(2, start=500.0, duration=5.0, deadline=600.0, arrival=500.0)
    result = scheduler.schedule(
        [fg, future_fg],
        d_free_ms=80.0,  # 80 + 30 ms of windows cannot fit the 100 ms cycle.
        d_sensing_ms=30.0,
        t_start_ms=0.0,
        t_end_ms=100.0,
    )
    assert result.solver_status != "Optimal"
    by_id = {d.fg_id: d for d in result.decisions}
    assert by_id[2].is_postponed is True
    assert by_id[2].delay_ms == 0.0


def _free_window_overlap(fgs: list[FrameGroup], result: object) -> float:
    """Total airtime of scheduled (not postponed) FGs inside the free window."""
    by_id = {fg.fg_id: fg for fg in fgs}
    overlap = 0.0
    for d in result.decisions:  # type: ignore[attr-defined]
        if d.is_postponed:
            continue
        end = d.scheduled_start_ms + by_id[d.fg_id].duration_ms
        lo = max(d.scheduled_start_ms, result.t_free_start_ms)  # type: ignore[attr-defined]
        hi = min(end, result.t_free_end_ms)  # type: ignore[attr-defined]
        overlap += max(0.0, hi - lo)
    return overlap


def test_hard_scheduler_is_milp_with_hard_flag() -> None:
    scheduler = HardConstraintMILPScheduler(gamma=5.0)
    assert isinstance(scheduler, MILPScheduler)
    assert scheduler.hard_free_window is True
    assert scheduler.gamma == 5.0
    assert MILPScheduler().hard_free_window is False


def test_hard_mode_vacates_fg_that_soft_mode_lets_overlap() -> None:
    """A 90 ms window in a 100 ms cycle cannot avoid a 20 ms FG with a tight
    deadline. Soft mode accepts the overlap; hard mode vacates the FG."""
    fgs = [_fg(1, start=0.0, duration=20.0, deadline=25.0)]
    soft = MILPScheduler().schedule(fgs, 90.0, 0.0, 0.0, 100.0)
    hard = HardConstraintMILPScheduler().schedule(fgs, 90.0, 0.0, 0.0, 100.0)

    assert soft.solver_status == "Optimal"
    assert _free_window_overlap(fgs, soft) > 0.0
    assert hard.solver_status == "Optimal"
    assert _free_window_overlap(fgs, hard) == 0.0
    (decision,) = hard.decisions
    assert decision.is_postponed
    assert decision.scheduled_start_ms == 100.0


def test_hard_mode_moves_rather_than_vacates_when_possible() -> None:
    fgs = [_fg(1, start=10.0, duration=10.0, deadline=500.0)]
    result = HardConstraintMILPScheduler().schedule(fgs, 40.0, 0.0, 0.0, 200.0)
    assert result.solver_status == "Optimal"
    assert not result.decisions[0].is_postponed
    assert _free_window_overlap(fgs, result) == 0.0


def test_hard_mode_never_overlaps_free_window_under_load() -> None:
    fgs = [
        _fg(i, start=i * 12.0, duration=10.0, deadline=i * 12.0 + 10.0 + bound)
        for i, bound in enumerate([5.0, 50.0, 5.0, 100.0, 5.0, 50.0, 5.0, 100.0])
    ]
    result = HardConstraintMILPScheduler().schedule(fgs, 62.0, 10.0, 0.0, 120.0)
    assert result.solver_status == "Optimal"
    assert _free_window_overlap(fgs, result) == pytest.approx(0.0, abs=1e-3)


def test_moving_earlier_is_penalized_like_moving_later() -> None:
    """Issue #36: Eq. 1 penalizes |s'_k - s_k|. The FG must end by the cycle
    end (100 ms), so the cheapest start is 90 ms, not 0 ms."""
    fg = FrameGroup(1, 0.0, 10.0, 500.0, 95.0, False)
    result = MILPScheduler(lambda_weight=1.0, mu_base=1e6).schedule(
        [fg], 0.0, 0.0, 0.0, 100.0
    )
    assert result.solver_status == OPTIMAL_STATUS
    (decision,) = result.decisions
    assert not decision.is_postponed
    assert decision.scheduled_start_ms == pytest.approx(90.0, abs=1e-6)


class _TimeLimitedSolver(pulp.LpSolver):  # type: ignore[misc]
    """Mark a real HiGHS incumbent as feasible without an optimality proof."""

    def actualSolve(self, lp: pulp.LpProblem, **kwargs: object) -> int:  # noqa: N802 (PuLP API)
        pulp.HiGHS(msg=False).actualSolve(lp)
        assert lp.status == pulp.LpStatusOptimal
        lp.assignStatus(pulp.LpStatusOptimal, pulp.LpSolutionIntegerFeasible)
        return int(lp.status)


def test_time_limited_solution_is_not_reported_as_optimal() -> None:
    """Issue #39: an unproven incumbent gets a distinct status."""
    fg = FrameGroup(1, 0.0, 10.0, 500.0, 50.0, False)
    result = MILPScheduler(solver=_TimeLimitedSolver()).schedule(
        [fg], 10.0, 0.0, 0.0, 100.0
    )
    assert result.solver_status == UNPROVEN_STATUS
    assert result.solver_status != OPTIMAL_STATUS


@pytest.mark.parametrize("hard", [False, True])
def test_unproven_solution_with_drops_is_not_accepted(hard: bool) -> None:
    """A time-limited incumbent cannot prove its drops are unavoidable."""
    fgs = [
        FrameGroup(1, 0.0, 60.0, 60.0, 0.0, False),
        FrameGroup(2, 0.0, 60.0, 60.0, 0.0, False),
    ]
    scheduler = MILPScheduler(solver=_TimeLimitedSolver(), hard_free_window=hard)
    result = scheduler.schedule(fgs, 0.0, 0.0, 0.0, 100.0)
    assert result.solver_status == UNPROVEN_DROP_STATUS
    assert result.t_free_start_ms == result.t_free_end_ms


def test_unservable_fg_is_dropped_instead_of_failing_the_cycle() -> None:
    """Issue #38: two 60 ms FGs that must both finish by 60 ms cannot share a
    100 ms cycle, and neither can be postponed in time. The whole cycle used
    to be Infeasible. Now exactly one is dropped and the other is kept."""
    fgs = [
        FrameGroup(1, 0.0, 60.0, 60.0, 0.0, False),
        FrameGroup(2, 0.0, 60.0, 60.0, 0.0, False),
    ]
    result = MILPScheduler().schedule(fgs, 0.0, 0.0, 0.0, 100.0)
    assert result.solver_status == OPTIMAL_STATUS
    kept = [d for d in result.decisions if not d.is_postponed]
    dropped = [d for d in result.decisions if d.is_postponed]
    assert len(kept) == 1 and len(dropped) == 1
    assert kept[0].scheduled_start_ms == pytest.approx(0.0)
    assert dropped[0].scheduled_start_ms == 100.0  # Past its 60 ms deadline.


def test_drop_penalty_keeps_fg_when_overlap_is_cheaper() -> None:
    """An urgent FG stays on air despite unavoidable free-window overlap."""
    fg = FrameGroup(1, 0.0, 60.0, 60.0, 0.0, False)
    result = MILPScheduler().schedule([fg], 60.0, 0.0, 0.0, 100.0)
    assert result.solver_status == OPTIMAL_STATUS
    assert not result.decisions[0].is_postponed
    assert result.t_free_start_ms < fg.duration_ms
