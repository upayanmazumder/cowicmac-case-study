"""Tests for the Issue #11 benchmark sweep runner."""

from pathlib import Path

import pandas as pd
import pytest

from cowic.analysis.plotter import MAIN_SWEEP, REQUIRED_COLUMNS, SENSING_SWEEP
from cowic.exceptions import ConfigurationError
from scripts import run_benchmark_sweep as sweep

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


def test_full_grid_matches_issue_11() -> None:
    grid = sweep.build_grid()
    main = [p for p in grid if p.sweep == MAIN_SWEEP]
    sensing = [p for p in grid if p.sweep == SENSING_SWEEP]
    # 6 experiments x 2 traffic x 3 T_ZB x 4 D_free x 5 seeds.
    assert len(main) == 6 * 2 * 3 * 4 * 5
    # Exp 4 on Traffic I: 4 D_sensing x 4 D_free x 5 seeds.
    assert len(sensing) == 4 * 4 * 5
    assert {p.d_free_ms for p in main} == {31.0, 62.0, 93.0, 123.0}
    assert {p.zigbee_period_ms for p in main} == {1000.0, 2000.0, 5000.0}
    assert {p.traffic for p in main} == {"Traffic_I", "Traffic_III"}
    assert len({p.seed for p in main}) == 5
    assert {p.d_sensing_ms for p in sensing} == {10.0, 20.0, 50.0, 100.0}
    assert {p.config_path for p in sensing} == {sweep.SENSING_CONFIG}


def test_quick_grid_is_small() -> None:
    grid = sweep.build_grid(quick=True)
    assert len(grid) == 6 * 2 + 2 * 2
    assert {p.traffic for p in grid} == {"Traffic_I"}
    assert len({p.seed for p in grid}) == 1


def test_grid_supports_all_four_traffic_profiles() -> None:
    all_traffics = tuple(sweep.TRAFFIC_PROFILES)
    assert set(all_traffics) == {"Traffic_I", "Traffic_II", "Traffic_III", "Traffic_IV"}
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
    assert 0.0 <= float(row["zigbee_gain_ratio"]) <= 1.0


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


def _fake_results() -> pd.DataFrame:
    rows = []
    for exp, gz in (("Exp_1", 0.05), ("Exp_4", 0.95)):
        for d_free in (31.0, 62.0):
            for seed, noise in ((42, 0.0), (43, 0.02)):
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
                        "wifi_drop_rate": 0.0,
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


def test_acceptance_report() -> None:
    results = _fake_results()
    passed, report = sweep.acceptance_report(results)
    assert passed and "Result: PASS" in report

    failing = results.copy()
    failing.loc[failing["experiment_id"] == "Exp_4", "wifi_drop_rate"] = 0.01
    passed, report = sweep.acceptance_report(failing)
    assert not passed and "FAIL" in report

    passed, _ = sweep.acceptance_report(results[results["experiment_id"] == "Exp_1"])
    assert not passed


def test_main_writes_csv_and_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sweep, "build_grid", lambda quick=False: FAST_POINTS)
    csv_path = tmp_path / "results.csv"
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
    assert code == 1  # No Exp 4 rows, so the strict acceptance check fails.
    assert "no Exp 4 / Traffic I rows" in out
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
