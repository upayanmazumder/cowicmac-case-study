"""Tests for ResultPlotter (base paper Figs. 11 to 17)."""

from pathlib import Path

import pandas as pd
import pytest

from cowic.analysis.plotter import (
    MAIN_SWEEP,
    METRIC_COLUMNS,
    OCCUPANCY_COLUMN,
    OCCUPANCY_SWEEP,
    SENSING_SWEEP,
    ResultPlotter,
    average_seeds,
    write_figures,
)
from cowic.exceptions import ConfigurationError


def _benchmark_rows(
    experiments: list[str], x_column: str, x_values: list[float]
) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for exp in experiments:
        for i, x_value in enumerate(x_values):
            rows.append(
                {
                    "experiment_id": exp,
                    "d_free_ms": 62.0,
                    "d_sensing_ms": 10.0,
                    x_column: x_value,
                    "zigbee_gain_ratio": 0.5 + 0.05 * i,
                    "wifi_drop_rate": 0.1 - 0.01 * i,
                    "spectrum_utilization": 0.7 + 0.02 * i,
                    "wifi_jitter_ms": 2.0 + 0.1 * i,
                    "moved_fg_count": 5 + i,
                    "max_fg_delay_ms": 40.0 + i,
                }
            )
    return rows


@pytest.fixture
def benchmark_csv(tmp_path: Path) -> Path:
    """Sample Fig. 11-style benchmark CSV: Exp 1-6 across a D_free sweep."""
    rows = _benchmark_rows(
        [f"Exp_{n}" for n in range(1, 7)], "d_free_ms", [31.0, 62.0, 123.0]
    )
    csv_path = tmp_path / "benchmark.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


@pytest.fixture
def sensing_sweep_csv(tmp_path: Path) -> Path:
    """Sample Fig. 16-style CSV: Exp 4-1..4-4 across a D_sensing sweep."""
    rows = _benchmark_rows(
        [f"Exp_4-{n}" for n in range(1, 5)], "d_sensing_ms", [10.0, 20.0, 50.0, 100.0]
    )
    csv_path = tmp_path / "sensing.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


def test_plot_benchmark_saves_png_under_output_dir(
    tmp_path: Path, benchmark_csv: Path
) -> None:
    figures_dir = tmp_path / "figures"
    plotter = ResultPlotter(figures_dir)
    output_path = plotter.plot_benchmark(benchmark_csv)
    assert output_path.exists()
    assert output_path.suffix == ".png"
    assert output_path.parent == figures_dir
    assert output_path.stat().st_size > 0


def test_plot_benchmark_creates_output_dir_if_missing(
    tmp_path: Path, benchmark_csv: Path
) -> None:
    figures_dir = tmp_path / "nested" / "figures"
    assert not figures_dir.exists()
    ResultPlotter(figures_dir)
    assert figures_dir.exists()


def test_plot_d_free_sweep_fig11_and_fig14_use_distinct_filenames(
    tmp_path: Path, benchmark_csv: Path
) -> None:
    plotter = ResultPlotter(tmp_path / "figures")
    fig11 = plotter.plot_d_free_sweep(
        benchmark_csv,
        title="Traffic I, T_ZB=1000ms",
        filename="fig11_traffic1",
    )
    fig14 = plotter.plot_d_free_sweep(
        benchmark_csv,
        title="Traffic III, T_ZB=1000ms",
        filename="fig14_traffic3",
    )
    assert fig11 != fig14
    assert fig11.exists()
    assert fig14.exists()


def test_plot_sensing_sensitivity_fig16(
    tmp_path: Path, sensing_sweep_csv: Path
) -> None:
    plotter = ResultPlotter(tmp_path / "figures")
    output_path = plotter.plot_sensing_sensitivity(
        sensing_sweep_csv,
        title="Sensing Window Sensitivity, Traffic I",
        filename="fig16_sensing",
    )
    assert output_path.exists()
    assert output_path.name == "fig16_sensing.png"


def test_plot_benchmark_rejects_missing_csv(tmp_path: Path) -> None:
    plotter = ResultPlotter(tmp_path / "figures")
    with pytest.raises(ConfigurationError):
        plotter.plot_benchmark(tmp_path / "does_not_exist.csv")


def test_plot_benchmark_rejects_missing_required_columns(tmp_path: Path) -> None:
    incomplete_csv = tmp_path / "incomplete.csv"
    pd.DataFrame({"experiment_id": ["Exp_1"], "d_free_ms": [62.0]}).to_csv(
        incomplete_csv, index=False
    )
    plotter = ResultPlotter(tmp_path / "figures")
    with pytest.raises(ConfigurationError):
        plotter.plot_benchmark(incomplete_csv)


def test_plot_benchmark_rejects_empty_csv(tmp_path: Path) -> None:
    empty_csv = tmp_path / "empty.csv"
    columns = ["experiment_id"] + [col for col, _ in METRIC_COLUMNS] + ["d_free_ms"]
    pd.DataFrame(columns=columns).to_csv(empty_csv, index=False)
    plotter = ResultPlotter(tmp_path / "figures")
    with pytest.raises(ConfigurationError):
        plotter.plot_benchmark(empty_csv)


def test_plot_d_free_sweep_rejects_missing_sweep_column(
    tmp_path: Path, benchmark_csv: Path
) -> None:
    plotter = ResultPlotter(tmp_path / "figures")
    with pytest.raises(ConfigurationError):
        plotter.plot_d_free_sweep(
            benchmark_csv,
            title="Bad sweep",
            filename="fig_bad",
            x_column="not_a_real_column",
        )


def test_metric_columns_match_simulation_metrics_fields() -> None:
    """The six plotted metrics must stay in sync with SimulationMetrics."""
    from cowic.analysis.metrics import SimulationMetrics

    metric_field_names = {col for col, _ in METRIC_COLUMNS}
    simulation_metrics_fields = set(SimulationMetrics.__dataclass_fields__)
    assert metric_field_names <= simulation_metrics_fields
    assert len(METRIC_COLUMNS) == 6


def test_plot_benchmark_rejects_zero_byte_csv(tmp_path: Path) -> None:
    """A completely empty (zero-byte) CSV raises pandas.errors.EmptyDataError
    internally; this must surface as ConfigurationError, not escape raw."""
    zero_byte_csv = tmp_path / "zero_byte.csv"
    zero_byte_csv.write_text("")
    plotter = ResultPlotter(tmp_path / "figures")
    with pytest.raises(ConfigurationError):
        plotter.plot_benchmark(zero_byte_csv)


def test_plot_benchmark_rejects_missing_experiment_id(tmp_path: Path) -> None:
    """A row with a blank experiment_id must not be silently dropped by
    groupby during plotting; it must raise instead."""
    rows = _benchmark_rows(["Exp_1", "Exp_2"], "d_free_ms", [31.0, 62.0])
    rows[0]["experiment_id"] = float("nan")
    csv_path = tmp_path / "missing_id.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    plotter = ResultPlotter(tmp_path / "figures")
    with pytest.raises(ConfigurationError):
        plotter.plot_benchmark(csv_path)


def sweep_results() -> pd.DataFrame:
    """Rows as written by the benchmark sweep: two seeds per scenario."""
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


def test_average_seeds_collapses_seed_rows() -> None:
    means = average_seeds(sweep_results())
    main = means[means["sweep"] == MAIN_SWEEP]
    assert len(main) == 4  # 2 experiments x 2 demands.
    exp4 = main[main["experiment_id"] == "Exp_4"]
    assert exp4["zigbee_gain_ratio"].tolist() == pytest.approx([0.96, 0.96])


def test_write_figures_draws_fig11_and_fig16(tmp_path: Path) -> None:
    paths = write_figures(sweep_results(), tmp_path)
    names = sorted(p.name for p in paths)
    assert names == [
        "fig11_Traffic_I_tzb1000.png",
        "fig16_sensing_Traffic_I_tzb1000.png",
    ]
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
    assert (tmp_path / "data" / "fig16_sensing_Traffic_I_tzb1000.csv").exists()


def _all_figure_results() -> pd.DataFrame:
    """Sweep rows for every figure stage: main, occupancy and sensing."""
    base = sweep_results()
    rows = [row for row in base.to_dict("records") if row["sweep"] == MAIN_SWEEP]
    template = rows[0]
    for traffic, period in (
        ("Traffic_I", 2000.0),
        ("Traffic_III", 1000.0),
        ("Traffic_II", 1000.0),
    ):
        rows.append(
            {**template, "traffic_profile": traffic, "zigbee_period_ms": period}
        )
    for occupancy, traffic in (
        (0.9, "Traffic_I"),
        (0.7, "Traffic_II"),
        (0.3, "Traffic_III"),
        (0.15, "Traffic_IV"),
    ):
        for exp in ("Exp_2", "Exp_4"):
            rows.append(
                {
                    **template,
                    "sweep": OCCUPANCY_SWEEP,
                    "experiment_id": exp,
                    "traffic_profile": traffic,
                    "d_free_ms": 63.0,
                    OCCUPANCY_COLUMN: occupancy,
                }
            )
    for traffic in ("Traffic_I", "Traffic_III"):
        for window in (10.0, 50.0):
            rows.append(
                {
                    **template,
                    "sweep": SENSING_SWEEP,
                    "experiment_id": "Exp_4",
                    "traffic_profile": traffic,
                    "d_sensing_ms": window,
                }
            )
    return pd.DataFrame(rows)


def test_write_figures_names_every_paper_figure(tmp_path: Path) -> None:
    paths = write_figures(_all_figure_results(), tmp_path)
    assert sorted(p.name for p in paths) == [
        "dfree_Traffic_II_tzb1000.png",
        "fig11_Traffic_I_tzb1000.png",
        "fig12_Traffic_I_tzb2000.png",
        "fig14_Traffic_III_tzb1000.png",
        "fig15_occupancy_tzb1000_dfree63.png",
        "fig16_sensing_Traffic_I_tzb1000.png",
        "fig17_sensing_Traffic_III_tzb1000.png",
    ]
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
    fig15 = pd.read_csv(tmp_path / "data" / "fig15_occupancy_tzb1000_dfree63.csv")
    assert len(fig15) == 8  # 2 experiments x 4 traffic profiles.
    assert sorted(fig15[OCCUPANCY_COLUMN].unique()) == [0.15, 0.3, 0.7, 0.9]


def test_write_figures_rejects_occupancy_rows_without_occupancy(
    tmp_path: Path,
) -> None:
    results = _all_figure_results()
    results.loc[results["sweep"] == OCCUPANCY_SWEEP, OCCUPANCY_COLUMN] = None
    with pytest.raises(ConfigurationError, match=OCCUPANCY_COLUMN):
        write_figures(results, tmp_path)


def test_plot_occupancy_sweep(tmp_path: Path) -> None:
    rows = _benchmark_rows(["Exp_2", "Exp_4"], OCCUPANCY_COLUMN, [0.2, 0.5, 0.9])
    csv_path = tmp_path / "occupancy.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    path = ResultPlotter(tmp_path / "figures").plot_occupancy_sweep(
        csv_path, title="Fig. 15", filename="fig15_occupancy"
    )
    assert path.name == "fig15_occupancy.png"
    assert path.stat().st_size > 0


def test_write_figures_rejects_rows_without_scenario_columns(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="traffic_profile"):
        write_figures(sweep_results().drop(columns=["traffic_profile"]), tmp_path)


def test_average_seeds_rejects_missing_scenario_value() -> None:
    """A null in a scenario column (e.g. ``traffic_profile``) must not be
    silently dropped by ``groupby``: that would average over fewer seeds
    than the run actually used with no sign of it (one seed's figure/CSV
    would quietly come from less data than another's)."""
    results = sweep_results()
    results.loc[results.index[0], "traffic_profile"] = None
    with pytest.raises(ConfigurationError, match="traffic_profile"):
        average_seeds(results)


def test_write_figures_rejects_rows_with_missing_scenario_value(
    tmp_path: Path,
) -> None:
    results = sweep_results()
    results.loc[results.index[0], "d_free_ms"] = None
    with pytest.raises(ConfigurationError, match="d_free_ms"):
        write_figures(results, tmp_path)
