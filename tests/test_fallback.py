"""Tests for the whitespace-expansion fallback scheduler."""

import pytest

from cowic.exceptions import ConfigurationError
from cowic.models.frame_group import FrameGroup
from cowic.wifi.baselines import find_whitespaces
from cowic.wifi.fallback import (
    FALLBACK_STATUS,
    NO_SENSING_STATUS,
    fallback_whitespace_expansion,
)
from traces.playback import frame_groups_from_frame, iter_cycles
from traces.synthetic_generator import build_trace, load_profile


def _fg(fg_id: int, start: float, duration: float, bound: float = 100.0) -> FrameGroup:
    return FrameGroup(
        fg_id=fg_id,
        arrival_time_ms=start,
        duration_ms=duration,
        deadline_ms=start + duration + bound,
        original_start_ms=start,
        qos_sensitive=False,
    )


# Gaps in [0, 100]: [0, 10], [20, 50], [55, 80], [90, 100].
FGS = [_fg(1, 10.0, 10.0), _fg(2, 50.0, 5.0), _fg(3, 80.0, 10.0)]


def _windows(fgs: list[FrameGroup], **kwargs: float) -> tuple[float, ...]:
    r = fallback_whitespace_expansion(fgs, 0.0, 100.0, **kwargs)
    return (r.t_sense_start_ms, r.t_sense_end_ms, r.t_free_start_ms, r.t_free_end_ms)


def test_uses_earliest_fitting_gap_without_moving_wifi() -> None:
    result = fallback_whitespace_expansion(FGS, 0.0, 100.0, 20.0, 5.0)
    # [0, 10] is too small; [20, 50] holds 25 ms.
    assert (result.t_sense_start_ms, result.t_sense_end_ms) == (20.0, 25.0)
    assert (result.t_free_start_ms, result.t_free_end_ms) == (25.0, 45.0)
    assert result.solver_status == FALLBACK_STATUS
    assert all(d.delay_ms == 0.0 for d in result.decisions)


def test_no_sensing_window() -> None:
    assert _windows(FGS, d_free_ms=8.0) == (0.0, 0.0, 0.0, 8.0)


def test_zero_request_moves_nothing() -> None:
    result = fallback_whitespace_expansion(FGS, 0.0, 100.0, 0.0)
    assert result.t_free_end_ms == result.t_free_start_ms
    assert all(d.delay_ms == 0.0 for d in result.decisions)


def test_extends_largest_gap_by_deferring_wifi() -> None:
    result = fallback_whitespace_expansion(FGS, 0.0, 100.0, 40.0)
    # Largest gap [20, 50] extends to [20, 60]; FG 2 moves to 60.
    assert (result.t_free_start_ms, result.t_free_end_ms) == (20.0, 60.0)
    starts = {d.fg_id: d.scheduled_start_ms for d in result.decisions}
    assert starts == {1: 10.0, 2: 60.0, 3: 80.0}
    assert not any(d.is_postponed for d in result.decisions)


def test_extension_stops_before_a_deadline_miss() -> None:
    # FG 2 may start at most 4 ms late, so the window can end at 54 at most.
    fgs = [_fg(1, 10.0, 10.0), _fg(2, 50.0, 5.0, bound=4.0), _fg(3, 80.0, 10.0)]
    result = fallback_whitespace_expansion(fgs, 0.0, 100.0, 40.0)
    assert result.t_free_start_ms == 20.0
    assert result.t_free_end_ms == pytest.approx(54.0, abs=1e-6)
    assert not any(d.is_postponed for d in result.decisions)


def test_widest_window_ignores_preexisting_unrelated_postponement() -> None:
    # FG 1 sits in its own clean slot before the target gap. FG 2 and FG 3
    # already conflict with each other in their *original* positions (FG 3
    # starts at 63, before FG 2's natural end at 65), pushing FG 3 past its
    # deadline regardless of any window - a pre-existing, window-unrelated
    # postponement. The largest natural gap is [10, 60] (width 50); a 55ms
    # request cannot fit, forcing the extend path with safe_end=60.
    #
    # Feasibility is judged against a no-window baseline: FG 3's
    # postponement already exists there, so it must not block extending
    # the window. FG 2's natural slot does conflict with an extended
    # window, so the window grows only until FG 2 would miss its own
    # deadline (bisection finds that boundary, just under 61 ms here).
    fgs = [
        _fg(1, 0.0, 10.0, bound=190.0),
        _fg(2, 60.0, 5.0, bound=1.0),
        _fg(3, 63.0, 5.0, bound=1.0),
    ]
    result = fallback_whitespace_expansion(fgs, 0.0, 100.0, 55.0)
    assert result.t_free_start_ms == 10.0
    assert result.t_free_end_ms == pytest.approx(61.0, abs=1e-6)
    by_id = {d.fg_id: d for d in result.decisions}
    assert by_id[2].is_postponed is False  # window stops just short of its deadline
    assert by_id[3].is_postponed is True  # pre-existing conflict, unrelated to window


def test_widest_window_reclaims_gap_despite_carryover_forced_postponement() -> None:
    # CodeRabbit's reported scenario: a 100-200ms cycle, channel_free_at=110
    # (carryover), a FG at 100-105ms due at 108ms (carryover alone already
    # pushes it past its deadline - postponed regardless of any window),
    # and a second FG at 160-170ms. The 110-160ms idle gap must not be lost
    # just because the first FG was always going to be postponed.
    carryover_only_postponed = _fg(1, 100.0, 5.0, bound=3.0)  # due 108
    clear_fg = _fg(2, 160.0, 10.0, bound=100.0)
    result = fallback_whitespace_expansion(
        [carryover_only_postponed, clear_fg],
        100.0,
        200.0,
        d_free_ms=50.0,
        channel_free_at=110.0,
    )
    assert result.t_free_end_ms - result.t_free_start_ms == 50.0
    by_id = {d.fg_id: d for d in result.decisions}
    assert by_id[1].is_postponed is True  # carryover alone already did this
    assert by_id[2].is_postponed is False


def test_fitting_gap_does_not_postpone_fg_that_carryover_alone_keeps_on_time() -> None:
    # CodeRabbit's reported "fitting gap" scenario: a 100-200ms cycle,
    # channel_free_at=110, a FG at 100-105ms due at 130ms (carryover alone
    # does NOT miss its deadline: pushed to 110, ends 115 <= 130), and a
    # second FG at 160-170ms. find_whitespaces (unaware of channel_free_at)
    # reports [105, 160] as one 55ms gap; naively taking window [105, 155]
    # would sweep up the carryover-pushed FG and force an avoidable
    # postponement (115 -> window_end=155, ending at 160 > deadline 130).
    # The fallback must not do that: it may fall back to an empty window
    # (the idle time past the carryover-pushed FG isn't found by the
    # carryover-unaware gap search), but it must never introduce a
    # postponement that channel_free_at alone would not have caused.
    carryover_pushed_on_time = _fg(1, 100.0, 5.0, bound=25.0)  # due 130
    clear_fg = _fg(2, 160.0, 10.0, bound=100.0)
    result = fallback_whitespace_expansion(
        [carryover_pushed_on_time, clear_fg],
        100.0,
        200.0,
        d_free_ms=50.0,
        channel_free_at=110.0,
    )
    by_id = {d.fg_id: d for d in result.decisions}
    assert by_id[1].is_postponed is False
    assert by_id[1].scheduled_start_ms == 110.0


def test_sensing_window_is_reserved_in_full_when_whitespace_is_short() -> None:
    """Issue #48: the only gap is 6 ms, but sensing needs 10 ms. Sensing is
    mandatory, so FG 2 (no slack) is postponed out of it. FG 2 is postponed
    anyway, so the free window may then extend without a new postponement."""
    fgs = [_fg(1, 0.0, 10.0, bound=0.0), _fg(2, 16.0, 84.0, bound=0.0)]
    result = fallback_whitespace_expansion(fgs, 0.0, 100.0, 62.0, 10.0)
    assert result.solver_status == FALLBACK_STATUS
    assert (result.t_sense_start_ms, result.t_sense_end_ms) == (10.0, 20.0)
    assert result.t_free_start_ms == 20.0
    assert result.t_free_end_ms == pytest.approx(82.0)
    first, second = result.decisions
    assert not first.is_postponed and first.delay_ms == 0.0
    assert second.is_postponed


def test_fully_busy_cycle_still_gets_its_sensing_window() -> None:
    """Issue #48 acceptance case: a fully busy cycle returns a 10 ms
    sensing window instead of a zero-length one."""
    fg = FrameGroup(1, 0.0, 50.0, 150.0, 0.0, False)
    result = fallback_whitespace_expansion([fg], 0.0, 50.0, 10.0, 10.0)
    assert result.t_sense_end_ms - result.t_sense_start_ms == pytest.approx(10.0)
    assert result.decisions[0].is_postponed


def test_cycle_shorter_than_sensing_window_is_flagged() -> None:
    result = fallback_whitespace_expansion([], 0.0, 5.0, 10.0, 10.0)
    assert result.solver_status == NO_SENSING_STATUS
    assert result.t_sense_end_ms == result.t_sense_start_ms
    assert result.t_free_end_ms == result.t_free_start_ms


def test_fully_busy_cycle_extends_from_cycle_start() -> None:
    result = fallback_whitespace_expansion([_fg(1, 0.0, 50.0)], 0.0, 50.0, 10.0)
    assert (result.t_free_start_ms, result.t_free_end_ms) == (0.0, 0.0)
    assert result.decisions[0].delay_ms == 0.0


def test_window_capped_by_cycle_end() -> None:
    result = fallback_whitespace_expansion([], 0.0, 30.0, 62.0)
    assert (result.t_free_start_ms, result.t_free_end_ms) == (0.0, 30.0)


@pytest.mark.parametrize("profile", ["Traffic_I", "Traffic_II"])
def test_invariants_on_real_trace(profile: str) -> None:
    fgs = frame_groups_from_frame(build_trace(load_profile(profile), 10.0))
    for cycle in iter_cycles(fgs, cycle_ms=1000.0):
        members = list(cycle.frame_groups)
        result = fallback_whitespace_expansion(
            members, cycle.start_ms, cycle.end_ms, 62.0, 10.0
        )
        start, end = result.t_sense_start_ms, result.t_free_end_ms
        gaps = find_whitespaces(members, cycle.start_ms, cycle.end_ms)
        largest = max((b - a for a, b in gaps), default=0.0)

        assert min(largest, 72.0) - 1e-9 <= end - start <= 72.0 + 1e-9
        assert result.t_sense_end_ms == result.t_free_start_ms
        by_id = {fg.fg_id: fg for fg in members}
        for decision in result.decisions:
            assert not decision.is_postponed
            fg_end = decision.scheduled_start_ms + by_id[decision.fg_id].duration_ms
            assert fg_end <= start + 1e-9 or decision.scheduled_start_ms >= end
            if decision.delay_ms > 0:
                assert fg_end <= by_id[decision.fg_id].deadline_ms + 1e-9


def test_invalid_inputs() -> None:
    with pytest.raises(ConfigurationError):
        fallback_whitespace_expansion(FGS, 0.0, 100.0, -1.0)
    with pytest.raises(ConfigurationError):
        fallback_whitespace_expansion(FGS, 0.0, 100.0, 10.0, -1.0)
    with pytest.raises(ConfigurationError):
        fallback_whitespace_expansion(FGS, 100.0, 0.0, 10.0)
