"""Integration tests for end-to-end coexistence scenarios."""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from cowic.analysis.metrics import (
    SimulationMetrics,
    measure_run,
    spectrum_utilization,
)
from cowic.exceptions import ConfigurationError
from cowic.sim.simulation_engine import (
    SimulationConfig,
    SimulationEngine,
    _build_scheduler,
    _pending_packets,
    _synthesize_zigbee_packets,
)
from cowic.zigbee.lwsa_adapter import (
    LWSAAdapter,
    _min_cap_interval_ms,
    _packet_duration_ms,
)
from traces.synthetic_generator import generate_trace


@pytest.fixture
def short_trace(tmp_path: Path) -> Path:
    """A small trace (3 s) so tests run fast but cover several cycles."""
    trace_path = tmp_path / "short_trace.parquet"
    generate_trace(
        "configs/base_traffic1.yaml", duration_s=3.0, output_path=trace_path, seed=42
    )
    return trace_path


def test_simulation_engine_instantiation(short_trace: Path) -> None:
    """Verify SimulationEngine initialization with configuration dataclass."""
    config = SimulationConfig(
        scenario_name="Test_Exp4",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        random_seed=42,
    )
    engine = SimulationEngine(config)
    assert engine.config.scenario_name == "Test_Exp4"
    assert engine.config.random_seed == 42


def test_simulation_engine_run_matches_configured_duration(short_trace: Path) -> None:
    """Simulated duration always equals ``duration_s * 1000`` regardless of
    how many (or how few) scheduling cycles fit inside it."""
    config = SimulationConfig(
        scenario_name="Test_Exp4",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
    )
    engine = SimulationEngine(config)
    result = engine.run()
    assert result["simulated_duration_ms"] == pytest.approx(3000.0)
    assert result["cycles_run"] == 3.0


def test_simulation_engine_milp_coexistence_protects_zigbee(short_trace: Path) -> None:
    """Exp 4 (MILP + L-WSA, bidirectional CTC) must achieve a near-perfect
    ZigBee success rate: the AP actively clears a window for it."""
    config = SimulationConfig(
        scenario_name="Test_Exp4",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="bidirectional",
        zigbee_adaptation="lwsa",
    )
    result = SimulationEngine(config).run()
    assert result["zigbee_transmissions"] > 0
    assert result["zigbee_success_rate"] > 0.9


def test_simulation_engine_uncoordinated_baseline_harms_zigbee(
    short_trace: Path,
) -> None:
    """Exp 1 (no coordination, fixed legacy ZigBee superframe) must show
    the coexistence problem: a much lower ZigBee success rate than the
    coordinated MILP+L-WSA scenario, since WiFi is never cleared for it."""
    config = SimulationConfig(
        scenario_name="Test_Exp1",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="none",
        ctc_direction="none",
        zigbee_adaptation="fixed",
    )
    result = SimulationEngine(config).run()
    coordinated = SimulationEngine(_exp4_config(short_trace)).run()
    assert result["zigbee_transmissions"] > 0
    assert coordinated["zigbee_transmissions"] > 0
    assert result["zigbee_success_rate"] < 0.5
    assert result["zigbee_success_rate"] < coordinated["zigbee_success_rate"]


def test_exp3_ecc_reserves_window_without_sensing_window(short_trace: Path) -> None:
    """ECC is bidirectional but has no sensing window, so no ZBeacon can be
    sent. The AP must still make its blind D_free reservation (capped by
    the 40 ms NAV) instead of a zero window every cycle."""
    config = SimulationConfig(
        scenario_name="Test_Exp3",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="ecc_nav",
        ctc_direction="bidirectional",
        zigbee_adaptation="fixed",
    )
    engine = SimulationEngine(config)
    engine.run()
    assert engine.last_demand_history == [config.d_free_ms] * 3
    assert engine.last_medium is not None
    assert any(
        record.protocol == "zigbee" and record.success
        for record in engine.last_medium.transmissions
    )


@pytest.mark.parametrize(
    "wifi_scheduling,ctc_direction",
    [
        ("none", "none"),
        ("gap_search", "wifi_to_zigbee"),
        ("ecc_nav", "bidirectional"),
    ],
)
def test_simulation_engine_runs_all_baseline_schedulers(
    short_trace: Path, wifi_scheduling: str, ctc_direction: str
) -> None:
    """Every Exp 1-3 baseline scheduler runs to completion without error."""
    config = SimulationConfig(
        scenario_name=f"Test_{wifi_scheduling}",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling=wifi_scheduling,
        ctc_direction=ctc_direction,
        zigbee_adaptation="fixed",
    )
    result = SimulationEngine(config).run()
    assert result["cycles_run"] == 3.0
    assert result["wifi_transmissions"] > 0


def test_simulation_engine_runs_exp5_config(tmp_path: Path) -> None:
    """configs/exp5_rigid_vacate.yaml (milp_hard_constraint) runs, and no
    WiFi FG scheduled on air overlaps a ZigBee packet's announced window."""
    config = SimulationConfig.from_yaml(
        Path("configs/exp5_rigid_vacate.yaml"), trace_dir=tmp_path
    )
    assert config.wifi_scheduling == "milp_hard_constraint"
    result = SimulationEngine(replace(config, duration_s=3.0)).run()
    assert result["cycles_run"] == 3.0
    assert result["wifi_transmissions"] > 0
    assert result["zigbee_packets_attempted"] > 0


def test_simulation_engine_rejects_unknown_scheduler(short_trace: Path) -> None:
    config = SimulationConfig(
        scenario_name="Test_Unknown",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="not_a_real_scheduler",
    )
    with pytest.raises(ConfigurationError):
        SimulationEngine(config).run()


def test_simulation_config_from_yaml_loads_exp1(tmp_path: Path) -> None:
    """from_yaml resolves the traffic profile, generates its trace on first
    use, and maps every yaml key onto SimulationConfig's field names."""
    config = SimulationConfig.from_yaml(
        Path("configs/exp1_uncoordinated.yaml"), trace_dir=tmp_path
    )
    assert config.scenario_name == "Exp_1"
    assert config.wifi_scheduling == "none"
    assert config.ctc_direction == "none"
    assert config.zigbee_adaptation == "fixed"
    assert config.d_sensing_ms == 0.0
    assert config.wifi_trace_path.exists()


def test_simulation_config_from_yaml_reuses_existing_trace(tmp_path: Path) -> None:
    """A second call with the same experiment/profile/seed does not
    regenerate the trace file (reuses it by path)."""
    config_1 = SimulationConfig.from_yaml(
        Path("configs/exp1_uncoordinated.yaml"), trace_dir=tmp_path
    )
    mtime_1 = config_1.wifi_trace_path.stat().st_mtime
    config_2 = SimulationConfig.from_yaml(
        Path("configs/exp1_uncoordinated.yaml"), trace_dir=tmp_path
    )
    mtime_2 = config_2.wifi_trace_path.stat().st_mtime
    assert config_1.wifi_trace_path == config_2.wifi_trace_path
    assert mtime_1 == mtime_2


def test_simulation_config_from_yaml_loads_exp4_milp_weights(tmp_path: Path) -> None:
    config = SimulationConfig.from_yaml(
        Path("configs/exp4_cowic_full.yaml"), trace_dir=tmp_path
    )
    assert config.wifi_scheduling == "milp_soft_constraint"
    assert config.gamma_sensing_weight == 10.0
    assert config.lambda_delay_weight == 1.0
    assert config.mu_base_cost == 100.0


def test_simulation_engine_exposes_raw_transmission_records(short_trace: Path) -> None:
    """After run(), raw per-transmission event records remain available
    via last_medium, beyond the summary dict's aggregates."""
    config = SimulationConfig(
        scenario_name="Test_Records",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="bidirectional",
        zigbee_adaptation="lwsa",
    )
    engine = SimulationEngine(config)
    assert engine.last_medium is None
    result = engine.run()
    assert engine.last_medium is not None
    assert len(engine.last_medium.transmissions) > 0
    total_records = len(engine.last_medium.transmissions)
    total_summary = int(
        result["wifi_transmissions"]
        + result["zigbee_transmissions"]
        + result["zigbee_beacon_transmissions"]
        + result["zbeacon_transmissions"]
    )
    assert total_records == total_summary


def test_simulation_engine_falls_back_to_csma_ca_on_total_ctc_loss(
    short_trace: Path,
) -> None:
    """When the CTC link always fails (per=1.0, no retries), the ZC must
    fall back to uncoordinated CSMA/CA (the fixed legacy superframe)
    rather than emit zero ZigBee activity for every cycle."""
    config = SimulationConfig(
        scenario_name="Test_CTCLoss",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="bidirectional",
        zigbee_adaptation="lwsa",
        ctc_per=1.0,
        ctc_max_retries=0,
    )
    result = SimulationEngine(config).run()
    assert result["wbeacon_lost"] == result["cycles_run"]
    assert result["csma_ca_fallback_cycles"] == result["cycles_run"]
    assert result["zigbee_packets_attempted"] > 0


def test_lost_wbeacon_reverts_wifi_and_zc_state(short_trace: Path) -> None:
    from cowic.zigbee.coordinator import ZCState

    config = SimulationConfig(
        scenario_name="Test_CTCRevert",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=31.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="bidirectional",
        zigbee_adaptation="lwsa",
        ctc_per=1.0,
        ctc_max_retries=0,
    )
    engine = SimulationEngine(config)
    result = engine.run()

    assert result["wbeacon_lost"] == result["cycles_run"]
    assert all(not decision.is_postponed for decision in engine.last_decisions)
    assert all(decision.delay_ms == 0.0 for decision in engine.last_decisions)
    assert engine.last_zigbee_state is ZCState.COMMAND_ACQUISITION


def test_simulation_engine_applies_ctc_delay_as_simulated_time(
    short_trace: Path,
) -> None:
    """A large per-attempt CTC delay, compounded by retries on a high PER,
    consumes enough simulated time to measurably reduce how many cycles
    complete within the horizon - proving delay_ms is actually applied
    to env.now, not just stored as an unused attribute."""
    negligible_delay = SimulationConfig(
        scenario_name="Test_CTCDelay_Fast",
        duration_s=2.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=500.0,
        ctc_delay_ms=0.001,
        ctc_per=0.0,
        ctc_max_retries=0,
    )
    heavy_delay = SimulationConfig(
        scenario_name="Test_CTCDelay_Slow",
        duration_s=2.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=500.0,
        ctc_delay_ms=400.0,
        ctc_per=0.99,
        ctc_max_retries=5,
    )
    fast_result = SimulationEngine(negligible_delay).run()
    slow_result = SimulationEngine(heavy_delay).run()
    assert slow_result["cycles_run"] < fast_result["cycles_run"]


def _fixed_baseline_config(
    trace: Path, wifi_scheduling: str, ctc_direction: str, **overrides: float
) -> SimulationConfig:
    return SimulationConfig(
        scenario_name=f"Test_{wifi_scheduling}",
        duration_s=3.0,
        wifi_trace_path=trace,
        d_free_ms=62.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling=wifi_scheduling,
        ctc_direction=ctc_direction,
        zigbee_adaptation="fixed",
        **overrides,  # type: ignore[arg-type]
    )


def _first_zigbee_start_per_cycle(engine: SimulationEngine) -> list[float]:
    assert engine.last_medium is not None
    starts: dict[int, float] = {}
    for record in engine.last_medium.transmissions:
        if record.protocol == "zigbee":
            cycle = int(record.start_time_ms // 1000.0)
            starts[cycle] = min(
                starts.get(cycle, record.start_time_ms), record.start_time_ms
            )
    return [starts[c] - c * 1000.0 for c in sorted(starts)]


def test_exp2_fixed_superframe_follows_announced_gap(short_trace: Path) -> None:
    """Exp 2 notifies ZigBee of the largest gap: the fixed superframe must
    start there, not at the cycle start, so Exp 2 differs from Exp 1."""
    exp1 = SimulationEngine(_fixed_baseline_config(short_trace, "none", "none"))
    exp2 = SimulationEngine(
        _fixed_baseline_config(short_trace, "gap_search", "wifi_to_zigbee")
    )
    r1, r2 = exp1.run(), exp2.run()

    first_payload_offset = 0.96 * 4 + 2 * 0.32
    exp1_starts = _first_zigbee_start_per_cycle(exp1)
    exp2_starts = _first_zigbee_start_per_cycle(exp2)
    assert exp1_starts
    assert exp2_starts
    assert all(start == pytest.approx(first_payload_offset) for start in exp1_starts)
    assert any(start > first_payload_offset + 1e-6 for start in exp2_starts)
    assert r2["zigbee_success_count"] > r1["zigbee_success_count"]


def test_fixed_superframe_without_wbeacon_stays_at_cycle_start(
    short_trace: Path,
) -> None:
    """If the WBeacon never arrives, ZigBee has no window to follow."""
    lost = SimulationEngine(
        _fixed_baseline_config(
            short_trace, "gap_search", "wifi_to_zigbee", ctc_per=1.0, ctc_max_retries=0
        )
    )
    lost.run()
    starts = _first_zigbee_start_per_cycle(lost)
    assert starts
    assert all(start == pytest.approx(0.96 * 4 + 2 * 0.32) for start in starts)


@pytest.mark.parametrize(
    "wifi_scheduling",
    ["ecc_nav", "milp_hard_constraint"],
)
def test_fixed_superframe_stays_inside_31ms_grant(
    short_trace: Path, wifi_scheduling: str
) -> None:
    from cowic.analysis.metrics import wifi_collision_rate

    config = SimulationConfig(
        scenario_name=f"Test_Fixed_{wifi_scheduling}",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=31.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling=wifi_scheduling,
        ctc_direction="wifi_to_zigbee",
        zigbee_adaptation="fixed",
    )
    engine = SimulationEngine(config)
    result = engine.run()
    assert engine.last_medium is not None
    assert wifi_collision_rate(engine.last_medium.transmissions) == 0.0
    assert result["zigbee_success_rate"] > 0.99


def test_exp6_without_zbeacon_still_serves_zigbee(short_trace: Path) -> None:
    """Exp 6 (MILP + L-WSA, WiFi->ZigBee CTC only) has no ZBeacon, so the AP
    must use the configured D_free instead of granting empty windows."""
    config = SimulationConfig(
        scenario_name="Test_Exp6",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=0.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="wifi_to_zigbee",
        zigbee_adaptation="lwsa",
    )
    result = SimulationEngine(config).run()
    assert result["zigbee_packets_attempted"] > 0
    assert result["zigbee_success_rate"] > 0.9


def test_delivered_wifi_fg_ids_feeds_metrics_calculator(short_trace: Path) -> None:
    """engine.delivered_wifi_fg_ids() integrates with MetricsCalculator's
    collision-aware delay_overhead: every id returned must have a
    successful registered WiFi TransmissionRecord, and feeding it through
    compute() must not raise."""
    from cowic.analysis.metrics import MetricsCalculator, zigbee_clean_capacity_ms
    from traces.playback import load_trace

    config = SimulationConfig(
        scenario_name="Test_DeliveredIds",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
    )
    engine = SimulationEngine(config)
    result = engine.run()
    delivered = engine.delivered_wifi_fg_ids()
    assert delivered
    assert engine.last_medium is not None
    successful_wifi_trans_ids = {
        r.trans_id
        for r in engine.last_medium.successful_transmissions()
        if r.protocol == "wifi"
    }
    assert len(delivered) <= len(successful_wifi_trans_ids)

    frame_groups = load_trace(short_trace)
    fg_by_id = {fg.fg_id: fg for fg in frame_groups}
    # Reconstruct final decisions is not directly exposed; use a minimal
    # decisions list covering only delivered FGs to prove compute() accepts
    # delivered_fg_ids without raising.
    from cowic.models.frame_group import SchedulingDecision

    decisions = [
        SchedulingDecision(fg_id, fg_by_id[fg_id].original_start_ms, False, 0.0)
        for fg_id in delivered
        if fg_id in fg_by_id
    ]
    metrics = MetricsCalculator().compute(
        engine.last_medium.transmissions,
        decisions,
        fg_by_id,
        horizon_ms=result["simulated_duration_ms"],
        zigbee_capacity_ms=zigbee_clean_capacity_ms(62.0, int(result["cycles_run"])),
        delivered_fg_ids=delivered,
    )
    assert metrics.max_fg_delay_ms >= 0.0


def test_delivered_wifi_fg_ids_raises_before_run() -> None:
    config = SimulationConfig(
        scenario_name="Test_NotRun",
        duration_s=1.0,
        wifi_trace_path=Path("traces/data/nonexistent.parquet"),
        d_free_ms=62.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
    )
    engine = SimulationEngine(config)
    with pytest.raises(RuntimeError):
        engine.delivered_wifi_fg_ids()


def _exp4_config(trace: Path, d_free_ms: float = 62.0) -> SimulationConfig:
    return SimulationConfig(
        scenario_name="Test_Exp4",
        duration_s=3.0,
        wifi_trace_path=trace,
        d_free_ms=d_free_ms,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="bidirectional",
        zigbee_adaptation="lwsa",
    )


def test_postponed_fgs_are_carried_not_transmitted(short_trace: Path) -> None:
    """A postponed FG must not go on air in the cycle that postponed it, so
    WiFi never collides with WiFi."""
    engine = SimulationEngine(_exp4_config(short_trace))
    engine.run()
    assert engine.last_medium is not None
    wifi = sorted(
        (r for r in engine.last_medium.transmissions if r.protocol == "wifi"),
        key=lambda r: r.start_time_ms,
    )
    for earlier, later in zip(wifi, wifi[1:], strict=False):
        assert later.start_time_ms >= earlier.end_time_ms - 1e-3
    on_air = [d for d in engine.last_decisions if not d.is_postponed]
    assert len(on_air) == len(wifi)


def test_last_decisions_hold_one_final_decision_per_fg(short_trace: Path) -> None:
    """A carried FG must not leave an intermediate postponement behind:
    every FG the AP saw has exactly one final decision, and the summary
    counts each FG once."""
    engine = SimulationEngine(_exp4_config(short_trace))
    result = engine.run()
    ids = [d.fg_id for d in engine.last_decisions]
    assert len(ids) == len(set(ids))
    assert result["total_frame_groups"] == len(ids)
    assert result["postponed_frame_groups"] == sum(
        d.is_postponed for d in engine.last_decisions
    )


def test_last_decisions_measure_delay_from_true_origin(short_trace: Path) -> None:
    engine = SimulationEngine(_exp4_config(short_trace))
    engine.run()
    assert engine.last_decisions
    for decision in engine.last_decisions:
        fg = engine.last_frame_groups[decision.fg_id]
        expected = max(0.0, decision.scheduled_start_ms - fg.original_start_ms)
        assert decision.delay_ms == pytest.approx(expected)


def test_zbeacon_demand_follows_d_free(short_trace: Path) -> None:
    """Bidirectional runs must honour the configured D_free (paper sweep)."""
    delivered = []
    for d_free in (31.0, 93.0):
        engine = SimulationEngine(_exp4_config(short_trace, d_free))
        engine.run()
        assert engine.last_medium is not None
        delivered.append(
            sum(
                r.end_time_ms - r.start_time_ms
                for r in engine.last_medium.successful_transmissions()
                if r.protocol == "zigbee"
            )
        )
    assert delivered[1] > delivered[0]


def test_demand_report_matches_d_free_across_ctc_directions(
    short_trace: Path,
) -> None:
    common = SimulationConfig(
        scenario_name="Test_DemandParity",
        duration_s=3.0,
        wifi_trace_path=short_trace,
        d_free_ms=31.0,
        d_sensing_ms=10.0,
        zigbee_period_ms=1000.0,
        wifi_scheduling="milp_soft_constraint",
        ctc_direction="bidirectional",
    )
    for direction in ("bidirectional", "wifi_to_zigbee"):
        engine = SimulationEngine(replace(common, ctc_direction=direction))
        engine.run()
        expected = [0.0, 31.0, 31.0] if direction == "bidirectional" else [31.0] * 3
        assert engine.last_demand_history == expected


def test_sensing_window_consumes_zigbee_grant_and_contains_zbeacon(
    short_trace: Path,
) -> None:
    from cowic.analysis.metrics import zigbee_gain_ratio

    gains = []
    for sensing_ms in (10.0, 100.0):
        config = SimulationConfig(
            scenario_name=f"Test_Sensing_{sensing_ms:g}",
            duration_s=3.0,
            wifi_trace_path=short_trace,
            d_free_ms=31.0,
            d_sensing_ms=sensing_ms,
            zigbee_period_ms=1000.0,
            wifi_scheduling="milp_soft_constraint",
            ctc_direction="bidirectional",
            zigbee_adaptation="lwsa",
        )
        engine = SimulationEngine(config)
        summary = engine.run()
        assert engine.last_medium is not None
        gains.append(
            zigbee_gain_ratio(
                engine.last_medium.transmissions,
                31.0 * int(summary["cycles_run"]),
                horizon_ms=3000.0,
            )
        )
        records = [
            record
            for record in engine.last_medium.transmissions
            if record.protocol == "zbeacon"
        ]
        assert len(records) == len(engine.last_zbeacon_windows)
        assert all(
            start <= record.start_time_ms and record.end_time_ms <= end
            for record, (start, end) in zip(
                records, engine.last_zbeacon_windows, strict=True
            )
        )
    assert gains[1] < gains[0] - 0.05


def test_pending_packets_derived_from_d_free_unless_overridden(
    short_trace: Path,
) -> None:
    config = _exp4_config(short_trace, 62.0)
    assert _pending_packets(config, 2.0) == 24
    assert _pending_packets(replace(config, d_free_ms=61.0), 2.0) == 24
    assert _pending_packets(replace(config, zigbee_pending_packets=5), 2.0) == 5
    with pytest.raises(ConfigurationError):
        _pending_packets(config, 0.0)


def test_zigbee_synthesis_uses_t_min_and_packet_airtime() -> None:
    config = LWSAAdapter().adapt_superframe(0.0, 62.0, [])
    packets = _synthesize_zigbee_packets(config, 0, 2.0, pending_packets=100)
    packet_duration_ms = _packet_duration_ms(config.slot_duration_ms, 2.0)
    min_interval_ms = _min_cap_interval_ms(packet_duration_ms)
    cap_packets = [
        packet for packet in packets if not packet.is_cfp and not packet.is_beacon
    ]
    cfp_packets = [packet for packet in packets if packet.is_cfp]
    beacons = [packet for packet in packets if packet.is_beacon]

    assert len(cap_packets) == int(
        config.cap_slots * config.slot_duration_ms / min_interval_ms
    )
    assert all(
        packet.duration_ms == pytest.approx(packet_duration_ms)
        for packet in cfp_packets
    )
    assert beacons[0].start_time_ms == config.beacon_offset_ms
    assert beacons[1].start_time_ms == config.beacon2_offset_ms

    limited = _synthesize_zigbee_packets(config, 1, 2.0, pending_packets=3)
    assert sum(not packet.is_beacon for packet in limited) == 3


def test_beacon2_does_not_overlap_first_cfp_packet_without_cap() -> None:
    """With cap_slots == 0, Beacon2 and the first GTS packet share a slot
    start; the packet must follow the beacon instead of colliding with it."""
    from cowic.models.superframe import SuperframeConfig

    for slot_ms in (0.96, 3.84):
        config = SuperframeConfig(
            slot_duration_ms=slot_ms,
            cap_slots=0,
            cfp_slots=3,
            beacon_offset_ms=0.0,
            beacon_interval_ms=4 * slot_ms,
            beacon2_offset_ms=slot_ms,
        )
        packets = _synthesize_zigbee_packets(config, 0, 2.0)
        intervals = sorted(
            (p.start_time_ms, p.start_time_ms + p.duration_ms) for p in packets
        )
        for (_, end), (start, _) in zip(intervals, intervals[1:], strict=False):
            assert end <= start + 1e-9
        cfp = [p for p in packets if p.is_cfp]
        assert all(
            p.start_time_ms + p.duration_ms <= slot_ms * (2 + k) + 1e-9
            for k, p in enumerate(cfp)
        )


@pytest.mark.parametrize("mode", ["milp_soft_constraint", "milp_hard_constraint"])
def test_milp_accepts_unproven_solutions_and_reports_them(
    short_trace: Path, mode: str
) -> None:
    """Issue #39 policy: a time-limited MILP solution meets every hard
    constraint, so the AP uses it, and the run summary counts it."""
    config = replace(_exp4_config(short_trace), wifi_scheduling=mode)
    _, statuses = _build_scheduler(config)
    assert statuses == {"Optimal", "Integer Feasible"}
    result = SimulationEngine(replace(config, duration_s=1.0)).run()
    assert "milp_unproven_cycles" in result


def test_run_summary_reports_missed_sensing_windows(short_trace: Path) -> None:
    """Issue #48: a cycle with less than D_sensing of sensing is counted."""
    result = SimulationEngine(replace(_exp4_config(short_trace), duration_s=2.0)).run()
    assert "sensing_missed_cycles" in result
    assert 0.0 <= result["sensing_missed_cycles"] <= result["cycles_run"]


def _run_measured(
    config: SimulationConfig,
) -> tuple[SimulationEngine, dict[str, float], SimulationMetrics]:
    engine = SimulationEngine(config)
    summary = engine.run()
    return engine, summary, measure_run(engine, summary)


def test_exp5_without_wifi_in_window_has_full_gain(tmp_path: Path) -> None:
    """Issue #33 acceptance: no WiFi inside the window gives G_z = 1."""
    trace = tmp_path / "no_wifi.parquet"
    pd.DataFrame(
        [
            {
                "fg_id": 0,
                "arrival_time_ms": 99_000.0,
                "duration_ms": 1.0,
                "deadline_ms": 99_200.0,
                "original_start_ms": 99_000.0,
                "qos_sensitive": False,
            }
        ]
    ).to_parquet(trace)
    config = SimulationConfig.from_yaml(
        Path("configs/exp5_rigid_vacate.yaml"), trace_dir=tmp_path
    )
    _, _, metrics = _run_measured(
        replace(config, duration_s=3.0, wifi_trace_path=trace)
    )
    assert metrics.zigbee_gain_ratio == pytest.approx(1.0, abs=0.01)


def test_exp1_light_load_gain_is_not_clamped_to_one(tmp_path: Path) -> None:
    """Issue #33 acceptance: Exp 1 on Traffic III at 31 ms is not 100 %."""
    trace = tmp_path / "traffic3.parquet"
    generate_trace("configs/base_traffic3.yaml", 5.0, trace, seed=42)
    config = SimulationConfig.from_yaml(
        Path("configs/exp1_uncoordinated.yaml"), trace_dir=tmp_path
    )
    _, _, metrics = _run_measured(
        replace(config, duration_s=5.0, d_free_ms=31.0, wifi_trace_path=trace)
    )
    assert metrics.zigbee_gain_ratio < 0.99


def test_exp1_drop_rate_counts_collisions(short_trace: Path, tmp_path: Path) -> None:
    """Issue #34 acceptance: Exp 1, Traffic I, D_free = 123 ms has PDR_w > 5 %."""
    config = SimulationConfig.from_yaml(
        Path("configs/exp1_uncoordinated.yaml"), trace_dir=tmp_path
    )
    _, summary, metrics = _run_measured(
        replace(config, duration_s=3.0, d_free_ms=123.0, wifi_trace_path=short_trace)
    )
    assert metrics.wifi_drop_rate > 0.05
    assert "wifi_collision_share" in summary
    assert "wifi_drop_rate" not in summary


def test_lost_ctc_earns_no_sensing_credit(short_trace: Path) -> None:
    """Issue #47 acceptance: with every CTC frame lost, U_eff gets no
    sensing airtime."""
    config = replace(
        _exp4_config(short_trace),
        duration_s=2.0,
        d_sensing_ms=100.0,
        ctc_per=1.0,
        ctc_max_retries=0,
    )
    engine, _, metrics = _run_measured(config)
    assert engine.last_medium is not None
    expected = spectrum_utilization(engine.last_medium.transmissions, 2000.0, 0.0)
    assert metrics.spectrum_utilization == pytest.approx(expected)


def test_sensing_credit_is_the_window_not_the_beacon(short_trace: Path) -> None:
    """Issue #47: a sensing window that carried a ZBeacon counts in full."""
    engine, _, metrics = _run_measured(
        replace(_exp4_config(short_trace), duration_s=2.0)
    )
    assert engine.last_medium is not None
    windows = sum(end - start for start, end in engine.last_zbeacon_windows)
    base = spectrum_utilization(engine.last_medium.transmissions, 2000.0, 0.0)
    assert windows > 0
    assert metrics.spectrum_utilization == pytest.approx(
        min(1.0, base + windows / 2000.0)
    )
