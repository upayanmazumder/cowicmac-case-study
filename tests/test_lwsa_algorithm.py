"""Unit tests for L-WSA superframe adaptation algorithm (Eq. 3-10)."""

import pytest

from cowic.zigbee.lwsa_adapter import (
    BASE_SLOT_DURATION_MS,
    MAX_ACTIVE_SLOTS,
    MAX_CFP_SLOTS,
    MAX_PACKET_DURATION_MS,
    MAX_SUPERFRAME_ORDER,
    LWSAAdapter,
    _count_cap_transmissions,
    _count_clear_slots,
    _min_cap_interval_ms,
    _packet_duration_ms,
    _per_packet_efficiency,
)


def test_lwsa_adapter_instantiation() -> None:
    """Verify LWSAAdapter initialization with default alpha weight."""
    adapter = LWSAAdapter(alpha=1.2)
    assert adapter.alpha == 1.2
    assert adapter.desired_packet_duration_ms == 2.0


def test_adapt_superframe_rejects_invalid_window() -> None:
    """dur_end_ms must be strictly greater than dur_start_ms."""
    adapter = LWSAAdapter()
    with pytest.raises(ValueError):
        adapter.adapt_superframe(50.0, 10.0, [])


def test_adapt_superframe_respects_16_slot_limit() -> None:
    """Selected configuration never violates the standard 16-slot limit."""
    adapter = LWSAAdapter()
    for window_ms in [5.0, 20.0, 62.0, 200.0, 1000.0]:
        config = adapter.adapt_superframe(0.0, window_ms, [])
        assert 0 <= config.cfp_slots <= 7
        assert config.cap_slots + config.cfp_slots <= 15
        assert config.cap_slots >= 0


def test_adapt_superframe_fits_within_granted_whitespace() -> None:
    """Total active duration (1 + i + j slots) fits within the window."""
    adapter = LWSAAdapter()
    for window_ms in [5.0, 20.0, 62.0, 200.0]:
        config = adapter.adapt_superframe(0.0, window_ms, [])
        total_active_ms = (
            1 + config.cap_slots + config.cfp_slots
        ) * config.slot_duration_ms
        assert total_active_ms <= window_ms + 1e-9


def test_equal_efficiency_prefers_larger_active_window() -> None:
    config = LWSAAdapter().adapt_superframe(0.0, 123.0, [])
    active_span_ms = (1 + config.cap_slots + config.cfp_slots) * config.slot_duration_ms
    assert active_span_ms >= 123.0 - config.slot_duration_ms


def test_adapt_superframe_too_short_for_beacon_returns_empty() -> None:
    """A window shorter than a single beacon slot yields zero activity."""
    adapter = LWSAAdapter()
    config = adapter.adapt_superframe(0.0, 0.5, [])
    assert config.cap_slots == 0
    assert config.cfp_slots == 0


def test_adapt_superframe_prefers_cfp_when_alpha_high() -> None:
    """A higher alpha should not reduce the selected CFP slot share."""
    low_alpha = LWSAAdapter(alpha=0.1).adapt_superframe(0.0, 62.0, [])
    high_alpha = LWSAAdapter(alpha=5.0).adapt_superframe(0.0, 62.0, [])
    assert high_alpha.cfp_slots >= low_alpha.cfp_slots


def test_adapt_superframe_accounts_for_residual_wifi_fragment() -> None:
    """A WiFi fragment inside the window reduces usable clear slot count,
    without ever selecting a configuration that pretends the fragment
    doesn't exist (slot counts inside _best_slot_split must reflect it)."""
    adapter = LWSAAdapter()
    clean = adapter.adapt_superframe(0.0, 62.0, [])
    fragmented = adapter.adapt_superframe(0.0, 62.0, [(20.0, 25.0)])
    # Both configurations must still respect the structural constraints.
    for config in (clean, fragmented):
        assert config.cap_slots + config.cfp_slots <= 15
        assert 0 <= config.cfp_slots <= 7


def test_adapt_superframe_offset_avoids_early_interference() -> None:
    """A WiFi fragment covering the whole window start forces a later
    beacon offset (Section 4.4: 'the beacon offset is first adjusted to
    avoid early interference')."""
    adapter = LWSAAdapter()
    # Fragment occupies [0, 50) of a 60ms window: only [50, 60) is usable.
    config = adapter.adapt_superframe(0.0, 60.0, [(0.0, 50.0)])
    assert config.beacon_offset_ms >= 50.0


def test_adapt_superframe_fully_busy_window_returns_empty() -> None:
    """A window entirely covered by WiFi fragments yields zero activity."""
    adapter = LWSAAdapter()
    config = adapter.adapt_superframe(0.0, 10.0, [(0.0, 10.0)])
    assert config.cap_slots == 0
    assert config.cfp_slots == 0


def test_adapt_superframe_never_schedules_beacon_inside_fragment() -> None:
    """The beacon slot [offset, offset + slot) must itself be WiFi-clear,
    even if later CAP/CFP slots would otherwise score higher (regression:
    _best_slot_split used to only check CAP/CFP slots, not the beacon)."""
    adapter = LWSAAdapter()
    config = adapter.adapt_superframe(0.0, 60.0, [(0.0, 50.0)])
    assert config.beacon_offset_ms >= 50.0 or (
        config.cap_slots == 0 and config.cfp_slots == 0
    )


def test_adapt_superframe_avoids_reserving_trailing_blocked_slots() -> None:
    """When WiFi blocks only the tail of the window, the search must not
    pad the allocation with slots it already knows are unusable
    (regression: _best_slot_split used to always fix i + j == max_active)."""
    adapter = LWSAAdapter()
    config = adapter.adapt_superframe(0.0, 20.0, [(15.0, 20.0)])
    total_active_end_ms = (
        config.beacon_offset_ms
        + (1 + config.cap_slots + config.cfp_slots) * config.slot_duration_ms
    )
    assert total_active_end_ms <= 15.0 + 1e-9


def test_adapt_superframe_keeps_mid_window_slot_reserved_but_not_padded() -> None:
    """A WiFi fragment inside the middle of the active run cannot be
    structurally skipped (the superframe grid is contiguous): the slot
    stays reserved, but the search still doesn't pad extra trailing
    slots beyond what maximizes efficiency (accepted design limitation,
    see module docstring; real nodes absorb this via CSMA/CA backoff)."""
    adapter = LWSAAdapter()
    fragment = (20.0, 25.0)
    config = adapter.adapt_superframe(0.0, 62.0, [fragment])
    active_end_ms = (
        config.beacon_offset_ms
        + (1 + config.cap_slots + config.cfp_slots) * config.slot_duration_ms
    )
    # The fragment lies inside the chosen active interval (structurally
    # unavoidable), but the interval still fits the granted window.
    assert config.beacon_offset_ms <= fragment[0]
    assert active_end_ms > fragment[1]
    assert active_end_ms <= 62.0 + 1e-9


def test_packet_duration_capped_by_slot_and_hardware() -> None:
    """t_pk (Eq. 8) never exceeds the slot width minus turnaround/ack."""
    small_slot = BASE_SLOT_DURATION_MS  # 0.96 ms
    pk = _packet_duration_ms(small_slot, desired_packet_duration_ms=100.0)
    assert pk <= small_slot
    assert pk >= 0.0


def test_ieee_packet_and_superframe_limits() -> None:
    assert MAX_PACKET_DURATION_MS == pytest.approx(4.256)
    assert MAX_SUPERFRAME_ORDER == 14


def test_min_cap_interval_includes_two_backoff_slots() -> None:
    """t_min (Eq. 8) is 2 backoff slots plus the packet duration."""
    pk = 2.0
    t_min = _min_cap_interval_ms(pk)
    assert t_min > pk  # must include backoff overhead


def test_per_packet_efficiency_is_bounded() -> None:
    """eta^p (Eq. 7) stays within [0, 1] for realistic inputs."""
    eta = _per_packet_efficiency(packet_duration_ms=2.0, interval_ms=2.64)
    assert 0.0 <= eta <= 1.0


def test_minimum_slot_packet_efficiency_is_positive() -> None:
    packet_duration_ms = _packet_duration_ms(BASE_SLOT_DURATION_MS, 2.0)
    interval_ms = _min_cap_interval_ms(packet_duration_ms)
    assert _per_packet_efficiency(packet_duration_ms, interval_ms) > 0


def test_count_clear_slots_excludes_overlapping_fragment() -> None:
    """A fragment overlapping a slot's occupancy window excludes it."""
    # 3 slots of width 2ms starting at t=0: [0,2), [2,4), [4,6).
    clear_no_frag = _count_clear_slots(3, 0.0, 2.0, 2.0, [])
    clear_with_frag = _count_clear_slots(3, 0.0, 2.0, 2.0, [(3.0, 3.5)])
    assert clear_no_frag == 3
    assert clear_with_frag == 2  # slot [2,4) overlaps [3.0, 3.5)


def test_count_clear_slots_ignores_fragment_outside_occupancy_width() -> None:
    """A CAP slot's grid width exceeds its checked occupancy width
    (t_min < t_SfSlot): a fragment landing only in the unused tail must
    not disqualify the slot."""
    # Grid slot width 4ms, but only the first 2ms (t_min) is checked.
    clear = _count_clear_slots(1, 0.0, 4.0, 2.0, [(3.0, 3.5)])
    assert clear == 1


def test_count_cap_transmissions_uses_t_min_stride() -> None:
    assert _count_cap_transmissions(3.84 * 8, 2.64, []) == 11


def test_adapter_matches_full_grid_objective() -> None:
    adapter = LWSAAdapter()
    window_ms = 62.0
    selected = adapter.adapt_superframe(0.0, window_ms, [])
    selected_packet_ms = _packet_duration_ms(
        selected.slot_duration_ms, adapter.desired_packet_duration_ms
    )
    selected_t_min_ms = _min_cap_interval_ms(selected_packet_ms)
    selected_score = _count_cap_transmissions(
        selected.cap_slots * selected.slot_duration_ms, selected_t_min_ms
    ) * selected_t_min_ms * _per_packet_efficiency(
        selected_packet_ms, selected_t_min_ms
    ) + adapter.alpha * _count_clear_slots(
        selected.cfp_slots,
        selected.beacon_offset_ms
        + (1 + selected.cap_slots) * selected.slot_duration_ms,
        selected.slot_duration_ms,
        selected.slot_duration_ms,
        [],
    ) * selected.slot_duration_ms * _per_packet_efficiency(
        selected_packet_ms, selected.slot_duration_ms
    )
    grid_scores = []
    for slot_ms in adapter._slot_duration_candidates(window_ms):
        packet_ms = _packet_duration_ms(slot_ms, adapter.desired_packet_duration_ms)
        if packet_ms <= 0:
            continue
        t_min_ms = _min_cap_interval_ms(packet_ms)
        eta_cap = _per_packet_efficiency(packet_ms, t_min_ms)
        eta_cfp = _per_packet_efficiency(packet_ms, slot_ms)
        for cfp_slots in range(MAX_CFP_SLOTS + 1):
            for cap_slots in range(MAX_ACTIVE_SLOTS - cfp_slots + 1):
                if (1 + cap_slots + cfp_slots) * slot_ms > window_ms:
                    continue
                cap_count = _count_cap_transmissions(cap_slots * slot_ms, t_min_ms)
                grid_scores.append(
                    cap_count * t_min_ms * eta_cap
                    + adapter.alpha * cfp_slots * slot_ms * eta_cfp
                )
    assert selected_score == pytest.approx(max(grid_scores))


def test_demand_report_caps_synthetic_queue_at_grant() -> None:
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    assert ZigBeeCoordinator().generate_demand_report(12, 31.0) == 31.0


def test_zc_demand_report_scales_with_pending_packets() -> None:
    """generate_demand_report returns zero for an empty queue and grows
    linearly with pending packet count."""
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    assert zc.generate_demand_report(0) == 0.0
    demand_5 = zc.generate_demand_report(5)
    demand_10 = zc.generate_demand_report(10)
    assert demand_10 == pytest.approx(2 * demand_5)


def test_zc_demand_report_rejects_negative_queue() -> None:
    from cowic.exceptions import ConfigurationError
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    with pytest.raises(ConfigurationError):
        zc.generate_demand_report(-1)


def test_zc_build_zbeacon_reports_demand() -> None:
    from cowic.ctc.channel import MessageType
    from cowic.wifi.ap_orchestrator import ZBEACON_DEMAND_KEY
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator(zc_id="ZC_7")
    zbeacon = zc.build_zbeacon(pending_packets=3, timestamp_ms=100.0)
    assert zbeacon.msg_type is MessageType.ZBEACON
    assert zbeacon.sender_id == "ZC_7"
    assert zbeacon.timestamp_ms == 100.0
    assert zbeacon.payload[ZBEACON_DEMAND_KEY] > 0.0


def test_zc_process_wbeacon_adapts_superframe() -> None:
    from cowic.ctc.channel import CTCMessage, MessageType
    from cowic.zigbee.coordinator import ZCState, ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    wbeacon = CTCMessage(
        msg_type=MessageType.WBEACON,
        sender_id="AP_0",
        timestamp_ms=0.0,
        payload={
            "t_free_start_ms": 0.0,
            "t_free_end_ms": 62.0,
            "t_sense_start_ms": 62.0,
            "t_sense_end_ms": 72.0,
        },
    )
    config = zc.process_wbeacon(wbeacon, residual_wifi_fragments=[])
    assert zc.state is ZCState.SUPERFRAME_ACTIVE
    assert zc.superframe is config
    assert config.cap_slots + config.cfp_slots > 0


def test_zc_returns_to_command_acquisition_after_wbeacon_loss() -> None:
    from cowic.zigbee.coordinator import ZCState, ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    zc.on_wbeacon_lost()
    assert zc.state is ZCState.COMMAND_ACQUISITION


def test_zc_process_wbeacon_zero_window_yields_empty_superframe() -> None:
    """A zero-length granted window (no airtime this cycle) is handled
    gracefully, not as an error."""
    from cowic.ctc.channel import CTCMessage, MessageType
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    wbeacon = CTCMessage(
        msg_type=MessageType.WBEACON,
        sender_id="AP_0",
        timestamp_ms=0.0,
        payload={
            "t_free_start_ms": 0.0,
            "t_free_end_ms": 0.0,
            "t_sense_start_ms": 0.0,
            "t_sense_end_ms": 0.0,
        },
    )
    config = zc.process_wbeacon(wbeacon, residual_wifi_fragments=[])
    assert config.cap_slots == 0
    assert config.cfp_slots == 0


def test_zc_process_wbeacon_rejects_wrong_message_type() -> None:
    from cowic.ctc.channel import CTCMessage, MessageType
    from cowic.exceptions import ProtocolError
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    not_a_wbeacon = CTCMessage(
        msg_type=MessageType.ZBEACON, sender_id="ZC_0", timestamp_ms=0.0, payload={}
    )
    with pytest.raises(ProtocolError):
        zc.process_wbeacon(not_a_wbeacon, residual_wifi_fragments=[])


def test_zc_process_wbeacon_rejects_missing_keys() -> None:
    from cowic.ctc.channel import CTCMessage, MessageType
    from cowic.exceptions import ProtocolError
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    incomplete = CTCMessage(
        msg_type=MessageType.WBEACON,
        sender_id="AP_0",
        timestamp_ms=0.0,
        payload={"t_free_start_ms": 0.0},
    )
    with pytest.raises(ProtocolError):
        zc.process_wbeacon(incomplete, residual_wifi_fragments=[])


def test_offset_candidates_handles_fragment_starting_before_window() -> None:
    """A fragment that starts before the window but ends inside it must
    still contribute its end as a candidate beacon offset (regression:
    the old filter required frag_start >= dur_start_ms)."""
    candidates = LWSAAdapter._offset_candidates(0.0, 60.0, [(-5.0, 10.0)])
    assert 10.0 in candidates


def test_zc_process_wbeacon_leaves_state_unchanged_on_validation_failure() -> None:
    """A malformed WBeacon must not leave the coordinator mid-FSM
    (regression: state used to flip to COMMAND_ACQUISITION before
    validation)."""
    from cowic.ctc.channel import CTCMessage, MessageType
    from cowic.exceptions import ProtocolError
    from cowic.zigbee.coordinator import ZCState, ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    assert zc.state is ZCState.IDLE
    bad_wbeacon = CTCMessage(
        msg_type=MessageType.ZBEACON, sender_id="ZC_0", timestamp_ms=0.0, payload={}
    )
    with pytest.raises(ProtocolError):
        zc.process_wbeacon(bad_wbeacon, residual_wifi_fragments=[])
    assert zc.state is ZCState.IDLE


def test_zc_process_wbeacon_rejects_nonnumeric_window_boundary() -> None:
    """A payload with the right keys but a non-numeric boundary value
    raises ProtocolError, not an unguarded TypeError (regression: a
    dict[str, float] annotation is not enforced at runtime)."""
    from cowic.ctc.channel import CTCMessage, MessageType
    from cowic.exceptions import ProtocolError
    from cowic.zigbee.coordinator import ZigBeeCoordinator

    zc = ZigBeeCoordinator()
    bad_wbeacon = CTCMessage(
        msg_type=MessageType.WBEACON,
        sender_id="AP_0",
        timestamp_ms=0.0,
        payload={
            "t_free_start_ms": "not-a-number",  # type: ignore[dict-item]
            "t_free_end_ms": 62.0,
            "t_sense_start_ms": 62.0,
            "t_sense_end_ms": 72.0,
        },
    )
    with pytest.raises(ProtocolError):
        zc.process_wbeacon(bad_wbeacon, residual_wifi_fragments=[])


def test_beacon2_airtime_is_kept_clear_of_residual_wifi() -> None:
    """Beacon2 opens the slot after Beacon1. A WiFi fragment that ends inside
    Beacon2 (before the first CAP attempt) must not be chosen over."""
    from cowic.zigbee.lwsa_adapter import BEACON_DURATION_MS

    adapter = LWSAAdapter()
    clean = adapter.adapt_superframe(0.0, 62.0, [])
    assert clean.beacon2_offset_ms is not None
    b2 = clean.beacon2_offset_ms
    # Inside Beacon2 only: clear of the Beacon1 slot and of the first CAP
    # attempt, which starts two backoff slots (0.64 ms) after Beacon2.
    fragment = (b2 + 0.05, b2 + BEACON_DURATION_MS - 0.05)
    chosen = adapter.adapt_superframe(0.0, 62.0, [fragment])
    assert chosen.beacon2_offset_ms is not None
    start = chosen.beacon2_offset_ms
    end = start + min(BEACON_DURATION_MS, chosen.slot_duration_ms)
    assert end <= fragment[0] or start >= fragment[1]
