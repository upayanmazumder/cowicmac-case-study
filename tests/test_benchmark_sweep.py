"""Tests for the Issue #11 benchmark sweep runner."""

from pathlib import Path

import pandas as pd
import pytest

from cowic.analysis.plotter import (
    MAIN_SWEEP,
    OCCUPANCY_COLUMN,
    OCCUPANCY_SWEEP,
    REQUIRED_COLUMNS,
    SENSING_SWEEP,
)
from cowic.exceptions import ConfigurationError
from scripts import run_benchmark_sweep as sweep

REPO_ROOT = Path(__file__).resolve().parent.parent
ALL_TRAFFIC = {"Traffic_I", "Traffic_II", "Traffic_III", "Traffic_IV"}

FAST_POINTS = [
    sweep.SweepPoint(
        MAIN_SWEEP,
        "configs/exp1_uncoordinated.yaml",
        "Traffic_I",
        31.0,
        1000.0,
        42,
    ),
    sweep.SweepPoint(
        MAIN_SWEEP,
        "configs/exp2_opportunistic.yaml",
        "Traffic_III",
        62.0,
        1000.0,
        43,
    ),
]


def test_full_grid_covers_paper_figures() -> None:
    grid = sweep.build_grid()
    main = [p for p in grid if p.sweep == MAIN_SWEEP]
    occupancy = [p for p in grid if p.sweep == OCCUPANCY_SWEEP]
    sensing = [p for p in grid if p.sweep == SENSING_SWEEP]
    seeds = set(sweep.SEEDS)
    experiments = set(sweep.EXPERIMENT_CONFIGS)
    # Figs. 11-14: every experiment, demand, T_ZB and seed on Traffic I and III.
    assert {
        (p.config_path, p.traffic, p.zigbee_period_ms, p.d_free_ms, p.seed)
        for p in main
    } >= {
        (config, traffic, period, demand, seed)
        for config in experiments
        for traffic in ("Traffic_I", "Traffic_III")
        for period in (1000.0, 2000.0, 5000.0)
        for demand in (31.0, 62.0, 93.0, 123.0)
        for seed in seeds
    }
    # Fig. 15: T_ZB = 1000 ms and D_free = 63 ms on all four profiles.
    assert {
        (p.config_path, p.traffic, p.seed)
        for p in occupancy
        if p.zigbee_period_ms == 1000.0 and p.d_free_ms == 63.0
    } == {
        (config, traffic, seed)
        for config in experiments
        for traffic in ALL_TRAFFIC
        for seed in seeds
    }
    # Figs. 16 and 17: Exp 4 with every D_sensing on Traffic I and III.
    assert {p.config_path for p in sensing} == {sweep.SENSING_CONFIG}
    assert {p.zigbee_period_ms for p in sensing} == {1000.0}
    assert {(p.traffic, p.d_sensing_ms, p.seed) for p in sensing} == {
        (traffic, window, seed)
        for traffic in ("Traffic_I", "Traffic_III")
        for window in (10.0, 20.0, 50.0, 100.0)
        for seed in seeds
    }


def test_quick_grid_is_small() -> None:
    grid = sweep.build_grid(quick=True)
    assert len(grid) <= 24
    assert {p.sweep for p in grid} == {MAIN_SWEEP, OCCUPANCY_SWEEP, SENSING_SWEEP}
    assert {p.traffic for p in grid if p.sweep == MAIN_SWEEP} == {"Traffic_I"}
    assert {p.traffic for p in grid if p.sweep == OCCUPANCY_SWEEP} == ALL_TRAFFIC
    assert {p.traffic for p in grid if p.sweep == SENSING_SWEEP} == {
        "Traffic_I",
        "Traffic_III",
    }
    assert len({p.seed for p in grid}) == 1


def test_warn_short_runs() -> None:
    grid = sweep.build_grid()
    with pytest.warns(UserWarning, match="fewer than 20"):
        assert sweep.warn_short_runs(grid, 60.0) == 12  # T_ZB = 5000 ms.
    assert sweep.warn_short_runs(grid, sweep.DEFAULT_DURATION_S) == 120


def test_default_duration_matches_paper_traces() -> None:
    assert sweep.parse_args([]).duration == 600.0


def test_grid_supports_all_four_traffic_profiles() -> None:
    all_traffics = tuple(sweep.TRAFFIC_PROFILES)
    grid = sweep.build_grid(traffic_profiles=all_traffics)
    main = [p for p in grid if p.sweep == MAIN_SWEEP]
    assert {p.traffic for p in main} == set(all_traffics)


def test_run_point_returns_plotter_ready_row(tmp_path: Path) -> None:
    row = sweep.run_point(FAST_POINTS[0], duration_s=2.0, trace_dir=tmp_path)
    for column in REQUIRED_COLUMNS:
        assert column in row
    assert row["experiment_id"] == "Exp_1"
    assert row["traffic_profile"] == "Traffic_I"
    assert row["d_free_ms"] == 31.0
    assert row["seed"] == 42
    assert row["cycles_run"] == 2.0
    assert row["milp_unproven_cycles"] == 0.0  # Issue #39: reported per run.
    assert 0.0 <= float(row["zigbee_gain_ratio"]) <= 1.0
    assert 0.0 < float(row[OCCUPANCY_COLUMN]) <= 1.0


def test_run_point_applies_sensing_override(tmp_path: Path) -> None:
    point = sweep.SweepPoint(
        SENSING_SWEEP,
        "configs/exp1_uncoordinated.yaml",
        "Traffic_I",
        31.0,
        1000.0,
        42,
        d_sensing_ms=20.0,
    )
    row = sweep.run_point(point, duration_s=1.0, trace_dir=tmp_path)
    assert row["d_sensing_ms"] == 20.0


def test_traffic_and_seed_select_the_trace(tmp_path: Path) -> None:
    a = sweep.trace_for("Traffic_I", 2.0, 42, tmp_path)
    b = sweep.trace_for("Traffic_III", 2.0, 42, tmp_path)
    c = sweep.trace_for("Traffic_I", 2.0, 43, tmp_path)
    assert len({a, b, c}) == 3
    assert sweep.trace_for("Traffic_I", 2.0, 42, tmp_path) == a
    with pytest.raises(ConfigurationError):
        sweep.trace_for("Traffic_V", 2.0, 42, tmp_path)


@pytest.mark.parametrize("jobs", [1, 2])
def test_run_sweep_keeps_grid_order(tmp_path: Path, jobs: int) -> None:
    seen: list[int] = []
    results = sweep.run_sweep(
        FAST_POINTS,
        1.0,
        tmp_path,
        jobs=jobs,
        progress=lambda done, total, point: seen.append(done),
    )
    assert list(results["experiment_id"]) == ["Exp_1", "Exp_2"]
    assert seen == [1, 2]


# Values that Sec. 5.3.1 states for Fig. 11, one value per paper point.
PAPER_VALUES: dict[tuple[str, float], tuple[float, float]] = {
    ("Exp_4", 31.0): (0.99, 0.0),
    ("Exp_4", 62.0): (0.963, 0.0),
    ("Exp_4", 123.0): (0.94, 0.0),
    ("Exp_5", 31.0): (1.0, 0.005),
    ("Exp_5", 123.0): (1.0, 0.028),
    ("Exp_6", 31.0): (0.99, 0.0),
}


def _fake_results() -> pd.DataFrame:
    rows = []
    for (exp, d_free), (gz, pdr) in PAPER_VALUES.items():
        for seed, noise in ((42, -0.005), (43, 0.005)):
            rows.append(
                {
                    "sweep": MAIN_SWEEP,
                    "experiment_id": exp,
                    "traffic_profile": "Traffic_I",
                    "d_free_ms": d_free,
                    "d_sensing_ms": 10.0,
                    "zigbee_period_ms": 1000.0,
                    "seed": seed,
                    "zigbee_gain_ratio": gz + noise,
                    "wifi_drop_rate": pdr,
                    "spectrum_utilization": 0.8,
                    "wifi_jitter_ms": 1.0,
                    "moved_fg_count": 3.0,
                    "max_fg_delay_ms": 10.0,
                    "wifi_collision_rate": 0.0,
                }
            )
    for window in (10.0, 50.0):
        rows.append({**rows[-1], "sweep": SENSING_SWEEP, "d_sensing_ms": window})
    return pd.DataFrame(rows)


def test_paper_points_quote_sec_5_3_values() -> None:
    points = {(p.experiment_id, p.metric, p.d_free_ms) for p in sweep.PAPER_POINTS}
    assert ("Exp_4", "zigbee_gain_ratio", 62.0) in points
    assert ("Exp_5", "wifi_drop_rate", 123.0) in points
    assert all(p.low <= p.high for p in sweep.PAPER_POINTS)


def test_acceptance_report_passes_paper_values() -> None:
    passed, report = sweep.acceptance_report(_fake_results())
    assert passed, report
    assert "Exp_4 G_z D_free=62: paper 0.963, sim 0.963 PASS" in report
    assert "Exp_6 G_z D_free=62: paper 0.963, sim -    SKIP" in report
    assert "Result: PASS" in report


@pytest.mark.parametrize(
    ("exp", "d_free", "metric", "value"),
    [
        ("Exp_4", 62.0, "zigbee_gain_ratio", 0.692),  # Issue #49 gap.
        ("Exp_4", 31.0, "wifi_drop_rate", 0.01),
        ("Exp_5", 123.0, "wifi_drop_rate", 0.0),
        ("Exp_5", 31.0, "zigbee_gain_ratio", 0.453),
    ],
)
def test_acceptance_report_fails_any_point(
    exp: str, d_free: float, metric: str, value: float
) -> None:
    results = _fake_results()
    rows = (
        (results["sweep"] == MAIN_SWEEP)
        & (results["experiment_id"] == exp)
        & (results["d_free_ms"] == d_free)
    )
    results.loc[rows, metric] = value
    passed, report = sweep.acceptance_report(results)
    assert not passed
    assert "FAIL" in report and "Result: FAIL" in report


def test_acceptance_report_needs_a_paper_point() -> None:
    results = _fake_results()
    passed, report = sweep.acceptance_report(
        results[results["traffic_profile"] == "Traffic_III"]
    )
    assert not passed and "No paper point is in the sweep." in report


def test_main_writes_csv_and_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "build_grid", lambda **_: FAST_POINTS)
    csv_path = tmp_path / "results.csv"
    with pytest.warns(UserWarning, match="only 1 cycles"):
        code = sweep.main(
            [
                "--duration",
                "1",
                "--output-csv",
                str(csv_path),
                "--figures-dir",
                str(tmp_path / "figs"),
                "--trace-dir",
                str(tmp_path / "traces"),
                "--jobs",
                "1",
                "--strict",
            ]
        )
    out = capsys.readouterr().out
    assert code == 1  # No paper point, so the strict acceptance check fails.
    assert "No paper point is in the sweep." in out
    results = pd.read_csv(csv_path)
    assert len(results) == 2
    assert set(REQUIRED_COLUMNS) <= set(results.columns)
    assert sorted(p.name for p in (tmp_path / "figs").glob("*.png")) == [
        "fig11_Traffic_I_tzb1000.png",
        "fig14_Traffic_III_tzb1000.png",
    ]


def test_main_rejects_bad_duration() -> None:
    with pytest.raises(ConfigurationError):
        sweep.main(["--duration", "0"])
