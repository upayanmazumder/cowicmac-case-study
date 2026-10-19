"""Tests for coexistence metrics against hand-computed mock runs."""

import math

import pytest

from cowic.analysis.metrics import (
    MetricsCalculator,
    delay_overhead,
    spectrum_utilization,
    wifi_collision_rate,
    wifi_drop_rate,
    zigbee_clean_capacity_ms,
    zigbee_gain_ratio,
    zigbee_payload_gain,
)
from cowic.exceptions import ConfigurationError
from cowic.models.frame_group import FrameGroup, SchedulingDecision
from cowic.sim.channel_medium import ChannelMedium, TransmissionRecord


def _fg(fg_id: int, start: float, duration: float, bound: float) -> FrameGroup:
    return FrameGroup(fg_id, start, duration, start + duration + bound, start, False)


def _rec(
    protocol: str, start: float, end: float, success: bool = True
) -> TransmissionRecord:
    return TransmissionRecord(f"{protocol}-{start}", protocol, start, end, success)


# One 100 ms cycle, hand-checkable:
#   FG 1 [10, 30]  kept           delay 0
#   FG 2 [40, 50]  moved to 60    delay 20
#   FG 3 [70, 90]  postponed to 100, deadline 140 -> still deliverable
#   FG 4 [92, 97]  postponed to 100, deadline 102 -> 100 + 5 > 102, dropped
FGS = {
    1: _fg(1, 10.0, 20.0, 100.0),
    2: _fg(2, 40.0, 10.0, 100.0),
    3: _fg(3, 70.0, 20.0, 50.0),
    4: _fg(4, 92.0, 5.0, 5.0),
}
DECISIONS = [
    SchedulingDecision(1, 10.0, False, 0.0),
    SchedulingDecision(2, 60.0, False, 20.0),
    SchedulingDecision(3, 100.0, True, 30.0),
    SchedulingDecision(4, 100.0, True, 8.0),
]


def test_wifi_drop_rate_counts_only_deadline_missing_postponements() -> None:
    # Dropped: FG 4 (5 ms) out of 20 + 10 + 20 + 5 = 55 ms requested.
    assert wifi_drop_rate(DECISIONS, FGS) == pytest.approx(5.0 / 55.0)


def test_wifi_drop_rate_zero_without_postponement() -> None:
    kept = [SchedulingDecision(1, 10.0, False, 0.0)]
    assert wifi_drop_rate(kept, FGS) == 0.0
    assert wifi_drop_rate([], FGS) == 0.0


def test_delay_overhead() -> None:
    jitter, moved, max_delay = delay_overhead(DECISIONS, FGS)
    # Delivered FGs 1-3 have delays 0, 20, 30; FG 4 is dropped.
    assert jitter == pytest.approx(
        math.sqrt(((0 - 50 / 3) ** 2 + (20 - 50 / 3) ** 2 + (30 - 50 / 3) ** 2) / 3)
    )
    assert moved == 3  # FGs 2, 3 and 4 left their original start.
    assert max_delay == 30.0


def test_delay_overhead_ignores_round_off_and_single_fg() -> None:
    decisions = [SchedulingDecision(1, 10.0 + 1e-9, False, 1e-9)]
    jitter, moved, max_delay = delay_overhead(decisions, FGS)
    assert (jitter, moved) == (0.0, 0)
    assert max_delay == pytest.approx(1e-9)
    assert delay_overhead([], FGS) == (0.0, 0, 0.0)


def test_zigbee_gain_uses_clean_capacity_not_attempts() -> None:
    records = [
        _rec("zigbee", 0.0, 4.0),
        _rec("zigbee", 10.0, 14.0),
        _rec("zigbee", 20.0, 24.0, success=False),
        _rec("wifi", 30.0, 60.0),
    ]
    # 8 ms delivered of a 62 ms clean-medium capacity.
    assert zigbee_gain_ratio(records, 62.0) == pytest.approx(8.0 / 62.0)


def test_zigbee_gain_caps_at_one_and_handles_zero_capacity() -> None:
    assert zigbee_gain_ratio([_rec("zigbee", 0.0, 80.0)], 62.0) == 1.0
    assert zigbee_gain_ratio([], 0.0) == 0.0


def test_zigbee_clean_capacity() -> None:
    assert zigbee_clean_capacity_ms(62.0, 10) == 620.0
    with pytest.raises(ConfigurationError):
        zigbee_clean_capacity_ms(-1.0, 10)
    with pytest.raises(ConfigurationError):
        zigbee_clean_capacity_ms(62.0, -1)


def test_wifi_collision_rate_is_airtime_weighted() -> None:
    records = [
        _rec("wifi", 0.0, 30.0),
        _rec("wifi", 40.0, 50.0, success=False),
        _rec("zigbee", 45.0, 48.0, success=False),
    ]
    assert wifi_collision_rate(records) == pytest.approx(10.0 / 40.0)
    assert wifi_collision_rate([]) == 0.0


def test_spectrum_utilization() -> None:
    records = [
        _rec("wifi", 0.0, 30.0),
        _rec("zigbee", 40.0, 44.0),
        _rec("wifi", 50.0, 60.0, success=False),
        _rec("zigbee", 95.0, 110.0),  # Only [95, 100] is inside the horizon.
        _rec("zigbee_beacon", 70.0, 72.0),
        _rec("ctc", 60.0, 61.0),  # Not a data protocol.
    ]
    # Useful: 30 + 4 + 5 + 2 beacon = 41, plus 10 ms sensing, over 100 ms.
    assert spectrum_utilization(records, 100.0, 10.0) == pytest.approx(0.51)


def test_spectrum_utilization_caps_and_validates() -> None:
    assert spectrum_utilization([_rec("wifi", 0.0, 100.0)], 100.0, 10.0) == 1.0
    for horizon in (0.0, -1.0, math.nan):
        with pytest.raises(ConfigurationError):
            spectrum_utilization([], horizon)
    with pytest.raises(ConfigurationError):
        spectrum_utilization([], 100.0, -1.0)


def test_calculator_on_channel_medium_ground_truth() -> None:
    """End-to-end on a real ChannelMedium, as the engine would produce it."""
    medium = ChannelMedium()
    medium.register_transmission("w1", "wifi", 10.0, 20.0)  # FG 1
    medium.register_transmission("w2", "wifi", 60.0, 10.0)  # FG 2, moved
    medium.register_transmission("z1", "zigbee", 35.0, 4.0)  # clean
    medium.register_transmission("zb1", "zigbee_beacon", 30.0, 2.0)
    medium.register_transmission("z2", "zigbee", 65.0, 4.0)  # hits FG 2

    metrics = MetricsCalculator().compute(
        medium.transmissions,
        DECISIONS,
        list(FGS.values()),
        horizon_ms=100.0,
        zigbee_capacity_ms=zigbee_clean_capacity_ms(20.0, 1),
        control_airtime_ms=5.0,
    )
    assert metrics.zigbee_gain_ratio == pytest.approx(4.0 / 20.0)
    assert metrics.wifi_drop_rate == pytest.approx(5.0 / 55.0)
    # Successful: FG 1 (20) + Beacon1 (2) + z1 (4) + 5 control = 31 of 100.
    assert metrics.spectrum_utilization == pytest.approx(0.31)
    assert metrics.wifi_collision_rate == pytest.approx(10.0 / 30.0)
    assert metrics.moved_fg_count == 3
    assert metrics.max_fg_delay_ms == 30.0
    assert metrics.wifi_jitter_ms > 0.0


def test_calculator_accepts_mapping_and_rejects_unknown_fg() -> None:
    calc = MetricsCalculator()
    metrics = calc.compute([], DECISIONS, FGS, horizon_ms=100.0, zigbee_capacity_ms=0)
    assert metrics.zigbee_gain_ratio == 0.0
    with pytest.raises(KeyError):
        calc.compute(
            [],
            [SchedulingDecision(99, 0.0, False, 0.0)],
            FGS,
            horizon_ms=100.0,
            zigbee_capacity_ms=0.0,
        )


def test_wifi_drop_rate_counts_each_fg_once_on_duplicate_entries() -> None:
    """A decisions list with more than one entry per fg_id (e.g. a full
    per-cycle history rather than one final outcome per FG) must not
    double-count that FG's airtime - only its last entry counts."""
    duplicated = [
        SchedulingDecision(4, 70.0, True, 0.0),  # stale, superseded below
        SchedulingDecision(4, 100.0, True, 8.0),  # final: dropped
    ]
    single = [SchedulingDecision(4, 100.0, True, 8.0)]
    assert wifi_drop_rate(duplicated, FGS) == wifi_drop_rate(single, FGS)


def test_zigbee_gain_ratio_clips_to_horizon() -> None:
    """A record straddling the horizon boundary only contributes its
    in-window portion to G_z when horizon_ms is given."""
    records = [_rec("zigbee", 95.0, 110.0)]
    unclipped = zigbee_gain_ratio(records, zigbee_capacity_ms=100.0)
    clipped = zigbee_gain_ratio(records, zigbee_capacity_ms=100.0, horizon_ms=100.0)
    assert unclipped == pytest.approx(15.0 / 100.0)
    assert clipped == pytest.approx(5.0 / 100.0)


def test_wifi_collision_rate_clips_to_horizon() -> None:
    """Consistent windowing with spectrum_utilization/zigbee_gain_ratio."""
    records = [
        _rec("wifi", 95.0, 110.0, success=False),
        _rec("wifi", 0.0, 50.0, success=True),
    ]
    unclipped = wifi_collision_rate(records)
    clipped = wifi_collision_rate(records, horizon_ms=100.0)
    # Unclipped: 15 ms failed / 65 ms total. Clipped: 5 ms failed / 55 ms total.
    assert unclipped == pytest.approx(15.0 / 65.0)
    assert clipped == pytest.approx(5.0 / 55.0)


def test_delay_overhead_excludes_collided_fg_with_delivered_fg_ids() -> None:
    """An FG scheduled on time (not postponed) but lost to a collision must
    not count as 'delivered' for jitter/max-delay when delivered_fg_ids is
    supplied, even though the deadline-only heuristic would include it."""
    on_time = [SchedulingDecision(1, 10.0, False, 0.0)]
    jitter, moved, max_delay = delay_overhead(on_time, FGS, delivered_fg_ids=set())
    assert max_delay == 0.0
    assert moved == 0
    # Without delivered_fg_ids, the same decision is treated as delivered.
    _, _, max_delay_no_filter = delay_overhead(on_time, FGS)
    assert max_delay_no_filter == 0.0  # delay_ms is 0 either way here


def test_calculator_threads_delivered_fg_ids_into_delay_overhead() -> None:
    calc = MetricsCalculator()
    metrics = calc.compute(
        [],
        DECISIONS,
        FGS,
        horizon_ms=100.0,
        zigbee_capacity_ms=0.0,
        delivered_fg_ids={1, 2},  # FG 3 excluded despite not being "dropped"
    )
    assert metrics.max_fg_delay_ms == 20.0  # only FG 2's delay remains


def test_fg_moved_earlier_counts_as_moved() -> None:
    """Issue #36: delay_ms is never negative, so an early move must be
    detected from the scheduled start."""
    fg = _fg(7, 50.0, 10.0, 100.0)
    early = SchedulingDecision(7, 40.0, False, 0.0)
    _, moved, max_delay = delay_overhead([early], {7: fg})
    assert moved == 1
    assert max_delay == 0.0


def test_payload_gain_is_delivered_over_ideal() -> None:
    """Issue #33: G_z = delivered payload / ideal clean-window payload."""
    assert zigbee_payload_gain(45.0, 60.0) == pytest.approx(0.75)
    assert zigbee_payload_gain(60.0, 60.0) == 1.0
    assert zigbee_payload_gain(0.0, 0.0) == 0.0


def test_payload_gain_rejects_delivered_above_ideal() -> None:
    with pytest.raises(ConfigurationError):
        zigbee_payload_gain(61.0, 60.0)
    with pytest.raises(ConfigurationError):
        zigbee_payload_gain(-1.0, 60.0)


def test_drop_rate_counts_collision_destroyed_fgs() -> None:
    """Issue #34: a transmitted FG that is not delivered is lost."""
    # FG 1 kept but collided, FG 2 kept and delivered, FG 4 dropped (5 ms).
    delivered = {2}
    rate = wifi_drop_rate(DECISIONS, FGS, delivered)
    # Lost: FG 1 (20 ms) + FG 4 (5 ms) of 55 ms. FG 3 is still postponed.
    assert rate == pytest.approx(25.0 / 55.0)
    # Without delivery data only the deadline drop counts.
    assert wifi_drop_rate(DECISIONS, FGS) == pytest.approx(5.0 / 55.0)


def test_calculator_uses_payload_gain_when_given() -> None:
    metrics = MetricsCalculator().compute(
        [],
        DECISIONS,
        FGS,
        horizon_ms=100.0,
        zigbee_capacity_ms=40.0,
        zigbee_delivered_payload_ms=30.0,
    )
    assert metrics.zigbee_gain_ratio == pytest.approx(0.75)
