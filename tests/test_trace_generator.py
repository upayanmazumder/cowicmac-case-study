"""Tests for synthetic trace generation and playback."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cowic.exceptions import ConfigurationError, TraceError
from cowic.models.frame_group import FrameGroup
from traces.playback import frame_groups_from_frame, iter_cycles, load_trace
from traces.synthetic_generator import (
    PROFILE_CONFIGS,
    WHITESPACE_CCDF_THRESHOLD_MS,
    TrafficProfile,
    build_trace,
    generate_trace,
    load_profile,
    main,
    summarize_trace,
)

DURATION_S = 600.0

# Sec. 5.2 text values of Fig. 9: P(whitespace > 10 ms). Traffic III is not quoted.
FIG9_CCDF_10MS: dict[str, float] = {
    "Traffic_I": 0.792,
    "Traffic_II": 0.659,
    "Traffic_IV": 0.041,
}


def _profile(**overrides: object) -> TrafficProfile:
    values: dict[str, object] = {
        "name": "test",
        "avg_fg_duration_ms": 20.0,
        "avg_whitespace_ms": 10.0,
        "qos_sensitive_ratio": 0.2,
        "non_realtime_ratio": 0.5,
        "avg_delay_bound_ms": 130.0,
        "postpone_bounds_ms": (50.0, 100.0, 150.0, 200.0),
    }
    values.update(overrides)
    return TrafficProfile(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", list(PROFILE_CONFIGS))
def test_trace_matches_table5_statistics(name: str) -> None:
    profile = load_profile(name)
    stats = summarize_trace(build_trace(profile, DURATION_S), DURATION_S)

    assert stats["avg_fg_duration_ms"] == pytest.approx(
        profile.avg_fg_duration_ms, rel=0.06
    )
    assert stats["avg_whitespace_ms"] == pytest.approx(
        profile.avg_whitespace_ms, rel=0.06
    )
    assert stats["qos_sensitive_ratio"] == pytest.approx(
        profile.qos_sensitive_ratio, abs=0.03
    )
    assert stats["non_realtime_ratio"] == pytest.approx(
        profile.non_realtime_ratio, abs=0.03
    )
    assert stats["avg_delay_bound_ms"] == pytest.approx(
        profile.avg_delay_bound_ms, abs=4.0
    )
    assert stats["occupancy"] == pytest.approx(profile.occupancy, abs=0.04)


@pytest.mark.parametrize("name", list(PROFILE_CONFIGS))
def test_whitespace_ccdf_matches_exponential_tail(name: str) -> None:
    # Seed 42, 600 s: at least 3,600 gaps per profile, so the binomial standard
    # error is at most 0.0043. The tolerance of 0.015 is about 3.5 sigma.
    profile = load_profile(name)
    stats = summarize_trace(build_trace(profile, DURATION_S, seed=42), DURATION_S)
    expected = np.exp(-WHITESPACE_CCDF_THRESHOLD_MS / profile.avg_whitespace_ms)
    assert stats["whitespace_ccdf_10ms"] == pytest.approx(expected, abs=0.015)


def test_fig9_tail_is_infeasible_for_traffic_i_mean() -> None:
    # Markov: P(W > 10) <= E[W] / 10 for any non-negative W. Fig. 9 exceeds this
    # bound for the Table 5 Traffic I mean, so no generator can match both.
    bounds = {
        name: load_profile(name).avg_whitespace_ms / WHITESPACE_CCDF_THRESHOLD_MS
        for name in FIG9_CCDF_10MS
    }
    assert FIG9_CCDF_10MS["Traffic_I"] > bounds["Traffic_I"]
    assert FIG9_CCDF_10MS["Traffic_II"] <= bounds["Traffic_II"]
    assert FIG9_CCDF_10MS["Traffic_IV"] <= bounds["Traffic_IV"]


def test_traffic_i_is_heaviest_load() -> None:
    occupancy = [load_profile(name).occupancy for name in PROFILE_CONFIGS]
    assert occupancy == sorted(occupancy, reverse=True)
    assert occupancy[0] > 0.85


def test_category_probabilities_reproduce_mean_bound() -> None:
    profile = load_profile("Traffic_I")
    probs = profile.category_probabilities()
    assert sum(probs) == pytest.approx(1.0)
    mean = float(np.dot(probs, profile.postpone_bounds_ms))
    assert mean == pytest.approx(profile.avg_delay_bound_ms)


def test_same_seed_is_deterministic() -> None:
    profile = load_profile("Traffic_II")
    pd.testing.assert_frame_equal(
        build_trace(profile, 30.0, seed=42), build_trace(profile, 30.0, seed=42)
    )
    assert not build_trace(profile, 30.0, seed=1).equals(
        build_trace(profile, 30.0, seed=2)
    )


def test_trace_is_serialised_and_within_horizon() -> None:
    trace = build_trace(load_profile("Traffic_I"), 60.0)
    starts = trace["original_start_ms"].to_numpy()
    ends = starts + trace["duration_ms"].to_numpy()

    assert (starts[1:] >= ends[:-1]).all()
    assert starts[0] >= 0.0
    assert ends[-1] <= 60_000.0
    assert (trace["duration_ms"] > 0).all()
    np.testing.assert_allclose(
        trace["deadline_ms"], ends + trace["delay_bound_ms"].to_numpy()
    )
    assert (trace["qos_sensitive"] == (trace["delay_bound_ms"] == 50.0)).all()


@pytest.mark.parametrize(
    "overrides",
    [
        {"avg_fg_duration_ms": 0.0},
        {"qos_sensitive_ratio": 1.5},
        {"qos_sensitive_ratio": 0.6, "non_realtime_ratio": 0.6},
        {"postpone_bounds_ms": (100.0, 50.0, 150.0, 200.0)},
        {"avg_delay_bound_ms": 500.0},
        {"non_realtime_ratio": 0.0, "avg_delay_bound_ms": 130.0},
    ],
)
def test_invalid_profile_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError):
        _profile(**overrides)


def test_profile_without_non_realtime_traffic() -> None:
    profile = _profile(non_realtime_ratio=0.0, avg_delay_bound_ms=90.0)
    assert profile.category_probabilities() == (0.2, 0.8, 0.0, 0.0)


def test_unknown_profile_and_bad_duration() -> None:
    with pytest.raises(ConfigurationError):
        load_profile("Traffic_V")
    with pytest.raises(ConfigurationError):
        build_trace(load_profile("Traffic_I"), 0.0)


def test_profile_yaml_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_profile(str(tmp_path / "missing.yaml"))
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\npostpone_bounds_ms: [50, 100]\n")
    with pytest.raises(ConfigurationError):
        load_profile(str(bad))
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("name: x\npostpone_bounds_ms: [50, 100, 150, 200\n")
    with pytest.raises(ConfigurationError):
        load_profile(str(malformed))


def test_generate_and_load_round_trip(tmp_path: Path) -> None:
    path = generate_trace("Traffic_III", 20.0, tmp_path / "sub" / "t.parquet")
    fgs = load_trace(path)
    expected = build_trace(load_profile("Traffic_III"), 20.0)

    assert len(fgs) == len(expected)
    assert all(isinstance(fg, FrameGroup) for fg in fgs)
    assert fgs[0].original_start_ms == pytest.approx(
        expected["original_start_ms"].iloc[0]
    )


def test_load_csv_trace(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    build_trace(load_profile("Traffic_IV"), 20.0).to_csv(path, index=False)
    assert len(load_trace(path)) > 0


def test_load_trace_errors(tmp_path: Path) -> None:
    with pytest.raises(TraceError):
        load_trace(tmp_path / "missing.parquet")
    other = tmp_path / "t.json"
    other.write_text("{}")
    with pytest.raises(TraceError):
        load_trace(other)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "fg_id": 0,
        "arrival_time_ms": 0.0,
        "duration_ms": 5.0,
        "deadline_ms": 60.0,
        "original_start_ms": 0.0,
        "qos_sensitive": True,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "rows",
    [
        [{k: v for k, v in _row().items() if k != "deadline_ms"}],
        [_row(), _row()],
        [_row(duration_ms=0.0)],
        [_row(arrival_time_ms=1.0)],
        [_row(deadline_ms=2.0)],
        [_row(deadline_ms=float("nan"))],
        [_row(original_start_ms=float("nan"))],
        [_row(duration_ms=float("inf"))],
    ],
)
def test_malformed_trace_rejected(rows: list[dict[str, object]]) -> None:
    with pytest.raises(TraceError):
        frame_groups_from_frame(pd.DataFrame(rows))


def test_frame_groups_sorted_by_start() -> None:
    frame = pd.DataFrame(
        [
            _row(fg_id=1, arrival_time_ms=10.0, original_start_ms=10.0, deadline_ms=99),
            _row(fg_id=2),
        ]
    )
    assert [fg.fg_id for fg in frame_groups_from_frame(frame)] == [2, 1]


def test_iter_cycles_partitions_trace() -> None:
    fgs = frame_groups_from_frame(build_trace(load_profile("Traffic_I"), 10.0))
    cycles = list(iter_cycles(fgs, cycle_ms=1000.0))

    assert [c.index for c in cycles] == list(range(len(cycles)))
    assert sum(len(c.frame_groups) for c in cycles) == len(fgs)
    for cycle in cycles:
        assert cycle.end_ms - cycle.start_ms == 1000.0
        for fg in cycle.frame_groups:
            assert cycle.start_ms <= fg.original_start_ms < cycle.end_ms


def test_iter_cycles_empty_and_horizon() -> None:
    assert list(iter_cycles([], cycle_ms=100.0)) == []
    cycles = list(iter_cycles([], cycle_ms=100.0, horizon_ms=250.0))
    assert [c.start_ms for c in cycles] == [0.0, 100.0, 200.0]
    with pytest.raises(ConfigurationError):
        list(iter_cycles([], cycle_ms=0.0))


def test_iter_cycles_active_carryover_crosses_boundary() -> None:
    # FG 1 starts in cycle 0 ([0, 100)) but runs until 110, into cycle 1.
    straddling = FrameGroup(
        fg_id=1,
        arrival_time_ms=0.0,
        duration_ms=15.0,
        deadline_ms=200.0,
        original_start_ms=95.0,
        qos_sensitive=False,
    )
    inside_next = FrameGroup(
        fg_id=2,
        arrival_time_ms=100.0,
        duration_ms=5.0,
        deadline_ms=200.0,
        original_start_ms=120.0,
        qos_sensitive=False,
    )
    cycles = list(
        iter_cycles([straddling, inside_next], cycle_ms=100.0, horizon_ms=201.0)
    )

    assert cycles[0].active_carryover == ()
    assert cycles[0].frame_groups == (straddling,)
    # FG 1 is owned by cycle 0 but still active at cycle 1's start (100 < 110).
    assert cycles[1].active_carryover == (straddling,)
    assert cycles[1].frame_groups == (inside_next,)
    # By cycle 2 (start 200), FG 1 (ends 110) is no longer active.
    assert cycles[2].active_carryover == ()


def test_iter_cycles_carryover_spans_multiple_cycles() -> None:
    # A 250ms FG starting at 10 is still active through cycles 1, 2, and 3.
    long_fg = FrameGroup(
        fg_id=1,
        arrival_time_ms=0.0,
        duration_ms=250.0,
        deadline_ms=400.0,
        original_start_ms=10.0,
        qos_sensitive=False,
    )
    cycles = list(iter_cycles([long_fg], cycle_ms=100.0, horizon_ms=400.0))
    assert [c.active_carryover for c in cycles] == [(), (long_fg,), (long_fg,), ()]


def test_cli_writes_trace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "cli.parquet"
    main(["--profile", "Traffic_II", "--output", str(out), "--duration-s", "5"])
    assert out.exists()
    assert "avg_fg_duration_ms" in capsys.readouterr().out
