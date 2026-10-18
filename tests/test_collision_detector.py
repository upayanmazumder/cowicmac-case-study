"""Unit tests for shared channel collision detection."""

import math

import pytest

from cowic.analysis.metrics import WIFI, ZIGBEE, wifi_collision_rate, zigbee_gain_ratio
from cowic.exceptions import ConfigurationError
from cowic.sim.channel_medium import OVERLAP_TOLERANCE_MS, ChannelMedium


def test_channel_medium_instantiation() -> None:
    """Verify ChannelMedium starts with empty transmission log."""
    medium = ChannelMedium()
    assert len(medium.transmissions) == 0


def test_non_overlapping_transmissions_both_succeed() -> None:
    """Independent, non-overlapping bursts should both succeed."""
    medium = ChannelMedium()
    first = medium.register_transmission("WIFI_1", WIFI, start_ms=0.0, duration_ms=10.0)
    second = medium.register_transmission(
        "ZB_1", ZIGBEE, start_ms=10.0, duration_ms=5.0
    )
    assert first.success is True
    assert second.success is True


def test_overlapping_cross_protocol_transmissions_both_fail() -> None:
    """Overlapping WiFi and ZigBee bursts corrupt each other (Section 5.1)."""
    medium = ChannelMedium()
    wifi_rec = medium.register_transmission(
        "WIFI_1", WIFI, start_ms=0.0, duration_ms=10.0
    )
    zb_rec = medium.register_transmission(
        "ZB_1", ZIGBEE, start_ms=5.0, duration_ms=10.0
    )

    assert zb_rec.success is False
    # The earlier WiFi record must be retroactively flipped to failed.
    stored_wifi = next(r for r in medium.transmissions if r.trans_id == "WIFI_1")
    assert stored_wifi.success is False
    assert wifi_rec.success is True  # The returned snapshot is immutable.


def test_overlapping_same_protocol_transmissions_both_fail() -> None:
    """Two overlapping bursts of the same protocol also collide."""
    medium = ChannelMedium()
    medium.register_transmission("WIFI_1", WIFI, start_ms=0.0, duration_ms=10.0)
    second = medium.register_transmission("WIFI_2", WIFI, start_ms=9.0, duration_ms=5.0)
    assert second.success is False
    stored_first = next(r for r in medium.transmissions if r.trans_id == "WIFI_1")
    assert stored_first.success is False


def test_adjacent_transmissions_do_not_collide() -> None:
    """Back-to-back bursts sharing an endpoint are not overlapping."""
    medium = ChannelMedium()
    first = medium.register_transmission("WIFI_1", WIFI, start_ms=0.0, duration_ms=10.0)
    second = medium.register_transmission(
        "ZB_1", ZIGBEE, start_ms=10.0, duration_ms=5.0
    )
    assert first.success is True
    assert second.success is True


def test_failed_transmission_stays_failed_on_third_overlap() -> None:
    """A transmission already marked failed stays failed, never flips back."""
    medium = ChannelMedium()
    medium.register_transmission("A", WIFI, start_ms=0.0, duration_ms=10.0)
    medium.register_transmission("B", ZIGBEE, start_ms=5.0, duration_ms=10.0)
    # C overlaps only A, not B; A must remain failed.
    medium.register_transmission("C", WIFI, start_ms=1.0, duration_ms=1.0)
    stored_a = next(r for r in medium.transmissions if r.trans_id == "A")
    assert stored_a.success is False


def test_register_transmission_rejects_non_positive_duration() -> None:
    """Zero or negative duration bursts are invalid."""
    medium = ChannelMedium()
    with pytest.raises(ConfigurationError):
        medium.register_transmission("X", WIFI, start_ms=0.0, duration_ms=0.0)


def test_register_transmission_rejects_duplicate_trans_id() -> None:
    """Registering the same trans_id twice raises an error."""
    medium = ChannelMedium()
    medium.register_transmission("X", WIFI, start_ms=0.0, duration_ms=1.0)
    with pytest.raises(ConfigurationError):
        medium.register_transmission("X", WIFI, start_ms=5.0, duration_ms=1.0)


def test_successful_and_failed_transmission_filters() -> None:
    """successful_transmissions and failed_transmissions partition the log."""
    medium = ChannelMedium()
    medium.register_transmission("A", WIFI, start_ms=0.0, duration_ms=10.0)
    medium.register_transmission("B", ZIGBEE, start_ms=5.0, duration_ms=10.0)
    medium.register_transmission("C", ZIGBEE, start_ms=20.0, duration_ms=5.0)

    successful_ids = {r.trans_id for r in medium.successful_transmissions()}
    failed_ids = {r.trans_id for r in medium.failed_transmissions()}
    assert successful_ids == {"C"}
    assert failed_ids == {"A", "B"}


def test_filters_are_sorted_by_start_time_regardless_of_registration_order() -> None:
    """A later-registered but earlier-starting burst still sorts first."""
    medium = ChannelMedium()
    medium.register_transmission("LATE_REG", WIFI, start_ms=0.0, duration_ms=5.0)
    medium.register_transmission("EARLY_REG", WIFI, start_ms=50.0, duration_ms=5.0)
    medium.register_transmission("MID_REG", WIFI, start_ms=20.0, duration_ms=5.0)

    starts = [r.start_time_ms for r in medium.successful_transmissions()]
    assert starts == sorted(starts)
    assert [r.trans_id for r in medium.successful_transmissions()] == [
        "LATE_REG",
        "MID_REG",
        "EARLY_REG",
    ]


def test_reset_clears_transmission_log() -> None:
    """reset() clears stored transmissions so a medium can be reused."""
    medium = ChannelMedium()
    medium.register_transmission("A", WIFI, start_ms=0.0, duration_ms=10.0)
    medium.reset()
    assert medium.transmissions == []
    # A previously used trans_id can be reused after reset.
    record = medium.register_transmission("A", WIFI, start_ms=0.0, duration_ms=1.0)
    assert record.success is True


def test_record_reflects_later_collisions() -> None:
    """record() returns the current outcome, unlike the returned snapshot."""
    medium = ChannelMedium()
    snapshot = medium.register_transmission("WIFI_1", WIFI, 0.0, 10.0)
    medium.register_transmission("ZB_1", ZIGBEE, 5.0, 2.0)
    assert snapshot.success is True
    assert medium.record("WIFI_1").success is False
    assert medium.record("ZB_1").success is False


def test_record_unknown_id_raises() -> None:
    medium = ChannelMedium()
    with pytest.raises(KeyError):
        medium.record("missing")
    medium.register_transmission("A", WIFI, 0.0, 1.0)
    medium.reset()
    with pytest.raises(KeyError):
        medium.record("A")


@pytest.mark.parametrize(
    ("start_ms", "duration_ms"),
    [(0.0, math.nan), (math.nan, 5.0), (0.0, math.inf), (-math.inf, 5.0)],
)
def test_register_transmission_rejects_non_finite(
    start_ms: float, duration_ms: float
) -> None:
    """NaN/inf would otherwise never collide and silently report success."""
    medium = ChannelMedium()
    with pytest.raises(ConfigurationError):
        medium.register_transmission("X", WIFI, start_ms, duration_ms)
    assert medium.transmissions == []


def test_round_off_overlap_is_not_a_collision() -> None:
    """A sub-tolerance overlap (MILP round-off) counts as touching."""
    medium = ChannelMedium()
    # 0.00096 ms is the largest round-off overlap seen from the MILP (#16).
    medium.register_transmission("FG", WIFI, 0.0, 10.0 + 0.00096)
    zb = medium.register_transmission("ZB", ZIGBEE, 10.0, 5.0)
    assert zb.success is True
    assert medium.record("FG").success is True


def test_overlap_just_above_tolerance_collides() -> None:
    medium = ChannelMedium()
    medium.register_transmission("FG", WIFI, 0.0, 10.0 + 2 * OVERLAP_TOLERANCE_MS)
    zb = medium.register_transmission("ZB", ZIGBEE, 10.0, 5.0)
    assert zb.success is False


def test_medium_records_feed_the_metrics_layer() -> None:
    """ChannelMedium's own protocol strings must be the ones
    :mod:`cowic.analysis.metrics` matches against (``WIFI``/``ZIGBEE``), so
    a future typo in either module shows up here instead of silently
    zeroing every metric on a green suite (the engine registers the same
    literal strings, see ``simulation_engine.py``)."""
    medium = ChannelMedium()
    medium.register_transmission("W1", WIFI, start_ms=0.0, duration_ms=10.0)
    medium.register_transmission("W2", WIFI, start_ms=20.0, duration_ms=10.0)
    medium.register_transmission("Z1", ZIGBEE, start_ms=5.0, duration_ms=10.0)

    # W1/Z1 overlap and corrupt each other; W2/Z1 don't overlap.
    assert wifi_collision_rate(medium.transmissions) == pytest.approx(0.5)
    assert zigbee_gain_ratio(medium.transmissions, zigbee_capacity_ms=10.0) == 0.0
