"""Publication figure generator reproducing base paper evaluations.

Reproduces the base paper's Section 5.3 benchmark figures from a results
CSV (one row per simulation run, as produced by a benchmark sweep over
:class:`~cowic.sim.simulation_engine.SimulationConfig` /
:class:`~cowic.analysis.metrics.MetricsCalculator`):

* **Figs. 11-14** ("Performance Evaluation ... under High/Low-Load WiFi
  Traffic ... with T_ZB = ..."): six metric panels against a ``d_free_ms``
  sweep, one line per experiment (Exp 1-6).
* **Fig. 15** ("Performance Evaluation ... under Varying WiFi Channel
  Occupancy Levels"): the same six panels against the WiFi occupancy of
  each traffic trace, at T_ZB = 1000 ms and D_free = 63 ms.
* **Figs. 16/17** ("Performance Evaluation ... with Various D_sensing
  Settings under WiFi Traffic I/III"): the same six panels against a
  ``d_sensing_ms`` sweep (Exp 4-1..4-4).

Expected CSV columns: ``experiment_id`` (e.g. ``"Exp_1"``), ``d_free_ms``,
``d_sensing_ms``, and the six :class:`~cowic.analysis.metrics.SimulationMetrics`
fields (``zigbee_gain_ratio``, ``wifi_drop_rate``, ``spectrum_utilization``,
``wifi_jitter_ms``, ``moved_fg_count``, ``max_fg_delay_ms``). Extra columns
(e.g. ``traffic_profile``, ``wifi_collision_rate``) are ignored. The Fig. 15
data also needs the ``wifi_occupancy`` column.

:func:`write_figures` draws every figure from a full sweep results table
(one row per run). ``python -m cowic.analysis`` calls it on a results CSV.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # Headless rendering; no display backend required.

import matplotlib.pyplot as plt
import pandas as pd

from cowic.exceptions import ConfigurationError

# (column name, paper symbol/axis label), in the order Section 5.1 lists
# the six performance metrics.
METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("zigbee_gain_ratio", "$G_z$ (ZigBee Gain Ratio)"),
    ("wifi_drop_rate", "$PDR_w$ (WiFi FG Drop Rate)"),
    ("spectrum_utilization", "$U_{eff}$ (Spectrum Utilization)"),
    ("wifi_jitter_ms", "WiFi Latency Jitter (ms)"),
    ("moved_fg_count", "Moved WiFi FG Count"),
    ("max_fg_delay_ms", "Max FG Delay (ms)"),
)

REQUIRED_COLUMNS: tuple[str, ...] = ("experiment_id",) + tuple(
    col for col, _ in METRIC_COLUMNS
)

# Values of the ``sweep`` column written by ``scripts/run_benchmark_sweep.py``.
MAIN_SWEEP = "main"  # D_free sweep per experiment (Figs. 11-14).
OCCUPANCY_SWEEP = "occupancy"  # One run per traffic profile (Fig. 15).
SENSING_SWEEP = "sensing"  # D_sensing sweep for Exp 4 (Figs. 16 and 17).

# Busy airtime over run time of the WiFi trace of a run (x-axis of Fig. 15).
OCCUPANCY_COLUMN = "wifi_occupancy"

# Paper figure for each (traffic profile, T_ZB in ms) of a D_free sweep.
# Other combinations get a neutral file name.
D_FREE_FIGURES: dict[tuple[str, float], str] = {
    ("Traffic_I", 1000.0): "fig11",
    ("Traffic_I", 2000.0): "fig12",
    ("Traffic_I", 5000.0): "fig13",
    ("Traffic_III", 1000.0): "fig14",
}
# Paper figure for each (T_ZB, D_free) in ms of the occupancy sweep.
OCCUPANCY_FIGURES: dict[tuple[float, float], str] = {(1000.0, 63.0): "fig15"}
# Paper figure for each (traffic profile, T_ZB in ms) of the D_sensing sweep.
SENSING_FIGURES: dict[tuple[str, float], str] = {
    ("Traffic_I", 1000.0): "fig16",
    ("Traffic_III", 1000.0): "fig17",
}

# Columns that identify one scenario; every other numeric column is a result.
_SCENARIO_KEYS: tuple[str, ...] = (
    "sweep",
    "experiment_id",
    "traffic_profile",
    "d_free_ms",
    "d_sensing_ms",
    "zigbee_period_ms",
)


class ResultPlotter:
    """Generates six-panel metric figures matching Figs. 11 to 17."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def plot_benchmark(self, results_csv: Path) -> Path:
        """Generate the Fig. 11-style benchmark comparison plot.

        Assumes ``results_csv`` already covers one traffic profile / T_ZB
        combination, varying ``d_free_ms`` across experiments; equivalent
        to calling :meth:`plot_d_free_sweep` with a generic title.
        """
        return self.plot_d_free_sweep(
            results_csv,
            title="CoWiC-MAC Benchmark Comparison",
            filename="fig11_benchmark",
        )

    def plot_d_free_sweep(
        self,
        results_csv: Path,
        title: str,
        filename: str,
        x_column: str = "d_free_ms",
        x_label: str = "$D_{free}$ (ms)",
    ) -> Path:
        """Fig. 11/14 style: six metric panels against a sweep column,
        one line per ``experiment_id`` (Exp 1-6).
        """
        df = self._load(results_csv)
        return self._plot_grid(
            df,
            x_column=x_column,
            x_label=x_label,
            series_column="experiment_id",
            title=title,
            filename=filename,
        )

    def plot_sensing_sensitivity(
        self,
        results_csv: Path,
        title: str,
        filename: str,
        x_column: str = "d_sensing_ms",
        x_label: str = "$D_{sensing}$ (ms)",
    ) -> Path:
        """Fig. 16/17 style: six metric panels against a ``d_sensing_ms``
        sweep (Exp 4-1..4-4), one line per ``experiment_id``.
        """
        df = self._load(results_csv)
        return self._plot_grid(
            df,
            x_column=x_column,
            x_label=x_label,
            series_column="experiment_id",
            title=title,
            filename=filename,
        )

    def plot_occupancy_sweep(
        self,
        results_csv: Path,
        title: str,
        filename: str,
        x_column: str = OCCUPANCY_COLUMN,
        x_label: str = "WiFi Channel Occupancy",
    ) -> Path:
        """Fig. 15 style: six metric panels against the WiFi occupancy of
        each traffic trace, one line per ``experiment_id``.
        """
        df = self._load(results_csv)
        return self._plot_grid(
            df,
            x_column=x_column,
            x_label=x_label,
            series_column="experiment_id",
            title=title,
            filename=filename,
        )

    def _load(self, results_csv: Path) -> pd.DataFrame:
        if not results_csv.exists():
            raise ConfigurationError(f"Results CSV not found: {results_csv}")
        try:
            df = pd.read_csv(results_csv)
        except pd.errors.EmptyDataError as exc:
            raise ConfigurationError(f"{results_csv}: no rows to plot") from exc
        missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise ConfigurationError(f"{results_csv}: missing columns {missing}")
        if df.empty:
            raise ConfigurationError(f"{results_csv}: no rows to plot")
        if df["experiment_id"].isna().any():
            raise ConfigurationError(
                f"{results_csv}: rows with a missing experiment_id would be "
                f"silently dropped by plotting"
            )
        return df

    def _plot_grid(
        self,
        df: pd.DataFrame,
        x_column: str,
        x_label: str,
        series_column: str,
        title: str,
        filename: str,
    ) -> Path:
        if x_column not in df.columns:
            raise ConfigurationError(f"Missing sweep column {x_column!r}")

        fig, axes = plt.subplots(2, 3, figsize=(15, 8))
        for (metric_col, metric_label), ax in zip(
            METRIC_COLUMNS, axes.flat, strict=True
        ):
            for series_value, group in df.groupby(series_column, sort=True):
                ordered = group.sort_values(x_column)
                ax.plot(
                    ordered[x_column],
                    ordered[metric_col],
                    marker="o",
                    label=str(series_value),
                )
            ax.set_xlabel(x_label)
            ax.set_ylabel(metric_label)
            ax.grid(True, alpha=0.3)

        handles, labels = axes.flat[0].get_legend_handles_labels()
        fig.suptitle(title, fontsize=13, y=0.995)
        fig.legend(
            handles,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 0.955),
            ncol=len(labels),
            fontsize=9,
        )
        fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.91))

        output_path = self.output_dir / f"{filename}.png"
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return output_path


def average_seeds(results: pd.DataFrame) -> pd.DataFrame:
    """Average the metrics over seeds, for one row per scenario.

    Raises:
        ConfigurationError: If a scenario column (``_SCENARIO_KEYS``) has a
            missing value: pandas' ``groupby`` silently drops that row
            instead of erroring, which would average over fewer seeds
            than the run actually used without any sign of it.
    """
    keys = list(_SCENARIO_KEYS)
    null_keys = [
        key for key in keys if key in results.columns and results[key].isna().any()
    ]
    if null_keys:
        raise ConfigurationError(
            f"rows with a missing {sorted(null_keys)} would be silently "
            f"dropped by averaging"
        )
    numeric = [c for c in results.columns if c not in keys and c != "seed"]
    return results.groupby(keys, as_index=False)[numeric].mean()


def write_figures(results: pd.DataFrame, figures_dir: Path) -> list[Path]:
    """Draw the D_free, occupancy and D_sensing figures of the sweep.

    Each scenario of the paper gets its figure number (Figs. 11 to 17, see
    ``D_FREE_FIGURES``, ``OCCUPANCY_FIGURES`` and ``SENSING_FIGURES``). Other
    scenarios get a neutral file name without a figure number.

    ``results`` holds one row per run, as written by the benchmark sweep.
    The seed-averaged data behind each figure is saved next to it under
    ``figures_dir / "data"``.

    Raises:
        ConfigurationError: If ``results`` lacks a scenario or metric column,
            or has occupancy rows without ``wifi_occupancy``.
    """
    missing = [
        c for c in (*_SCENARIO_KEYS, *REQUIRED_COLUMNS) if c not in results.columns
    ]
    if missing:
        raise ConfigurationError(f"Results lack columns {sorted(set(missing))}")
    plotter = ResultPlotter(figures_dir)
    means = average_seeds(results)
    data_dir = figures_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    main = means[means["sweep"] == MAIN_SWEEP]
    for (traffic, period), group in main.groupby(
        ["traffic_profile", "zigbee_period_ms"]
    ):
        fig = D_FREE_FIGURES.get((traffic, period), "dfree")
        name = f"{fig}_{traffic}_tzb{int(period)}"
        csv_path = data_dir / f"{name}.csv"
        group.to_csv(csv_path, index=False)
        paths.append(
            plotter.plot_d_free_sweep(
                csv_path,
                title=f"{traffic}, $T_{{ZB}}$ = {int(period)} ms",
                filename=name,
            )
        )

    occupancy = means[means["sweep"] == OCCUPANCY_SWEEP]
    if not occupancy.empty and (
        OCCUPANCY_COLUMN not in occupancy.columns
        or occupancy[OCCUPANCY_COLUMN].isna().any()
    ):
        raise ConfigurationError(f"Occupancy rows lack column {OCCUPANCY_COLUMN!r}")
    for (period, d_free), group in occupancy.groupby(["zigbee_period_ms", "d_free_ms"]):
        fig = OCCUPANCY_FIGURES.get((period, d_free), "occupancy")
        prefix = "occupancy" if fig == "occupancy" else f"{fig}_occupancy"
        name = f"{prefix}_tzb{int(period)}_dfree{d_free:g}"
        csv_path = data_dir / f"{name}.csv"
        group.to_csv(csv_path, index=False)
        paths.append(
            plotter.plot_occupancy_sweep(
                csv_path,
                title=(
                    f"Varying WiFi occupancy, $T_{{ZB}}$ = {int(period)} ms, "
                    f"$D_{{free}}$ = {d_free:g} ms"
                ),
                filename=name,
            )
        )

    sensing = means[means["sweep"] == SENSING_SWEEP].copy()
    # One line per demand for Exp 4, as Exp 4-1 to Exp 4-4 in Figs. 16 and 17.
    sensing["experiment_id"] = sensing["d_free_ms"].map(
        lambda d: f"Exp_4 (D_free={d:g} ms)"
    )
    for (traffic, period), group in sensing.groupby(
        ["traffic_profile", "zigbee_period_ms"]
    ):
        fig = SENSING_FIGURES.get((traffic, period), "sensing")
        prefix = "sensing" if fig == "sensing" else f"{fig}_sensing"
        name = f"{prefix}_{traffic}_tzb{int(period)}"
        csv_path = data_dir / f"{name}.csv"
        group.to_csv(csv_path, index=False)
        paths.append(
            plotter.plot_sensing_sensitivity(
                csv_path,
                title=(
                    f"Exp 4, {traffic}, $T_{{ZB}}$ = {int(period)} ms, "
                    f"various $D_{{sensing}}$"
                ),
                filename=name,
            )
        )
    return paths
