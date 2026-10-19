"""Run the benchmark sweeps for Issue #11 and draw the evaluation figures.

The sweep covers Exp 1 to Exp 6 (``configs/exp*.yaml``). It has three stages:

* The main stage sweeps the ZigBee demand, the traffic profile, the ZigBee
  period and the random seed (Figs. 11 to 14).
* The occupancy stage runs every experiment at T_ZB = 1000 ms and
  D_free = 63 ms on all four traffic profiles (Fig. 15). Each row records the
  WiFi occupancy of the trace that the run used.
* The sensing stage varies the sensing window for Exp 4 on Traffic I
  (Fig. 16) and Traffic III (Fig. 17).

Each run gives one CSV row. The row holds the sweep parameters and the
:class:`~cowic.analysis.metrics.SimulationMetrics` fields. The script then
averages the seeds and draws the figures with
:func:`cowic.analysis.plotter.write_figures`. To redraw the figures from an
existing CSV, run ``python -m cowic.analysis``.

Usage::

    python scripts/run_benchmark_sweep.py            # full sweep, 600 s runs
    python scripts/run_benchmark_sweep.py --quick    # smoke run
"""

import argparse
import os
import sys
import time
import warnings
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # Makes the top-level "traces" importable.

from cowic.analysis.metrics import measure_run  # noqa: E402
from cowic.analysis.plotter import (  # noqa: E402
    MAIN_SWEEP,
    OCCUPANCY_COLUMN,
    OCCUPANCY_SWEEP,
    REQUIRED_COLUMNS,
    SENSING_SWEEP,
    write_figures,
)
from cowic.exceptions import ConfigurationError  # noqa: E402
from cowic.sim.simulation_engine import (  # noqa: E402
    SimulationConfig,
    SimulationEngine,
)
from traces.synthetic_generator import generate_trace, summarize_trace  # noqa: E402

EXPERIMENT_CONFIGS: tuple[str, ...] = (
    "configs/exp1_uncoordinated.yaml",
    "configs/exp2_opportunistic.yaml",
    "configs/exp3_ecc.yaml",
    "configs/exp4_cowic_full.yaml",
    "configs/exp5_rigid_vacate.yaml",
    "configs/exp6_overhead_free.yaml",
)
TRAFFIC_PROFILES: dict[str, str] = {
    "Traffic_I": "configs/base_traffic1.yaml",
    "Traffic_II": "configs/base_traffic2.yaml",
    "Traffic_III": "configs/base_traffic3.yaml",
    "Traffic_IV": "configs/base_traffic4.yaml",
}
DEFAULT_SWEEP_TRAFFIC: tuple[str, ...] = ("Traffic_I", "Traffic_III")
D_FREE_MS: tuple[float, ...] = (31.0, 62.0, 93.0, 123.0)
ZIGBEE_PERIODS_MS: tuple[float, ...] = (1000.0, 2000.0, 5000.0)
D_SENSING_MS: tuple[float, ...] = (10.0, 20.0, 50.0, 100.0)
SEEDS: tuple[int, ...] = (42, 43, 44, 45, 46)

# Fig. 10 shows the WiFi traces "over a 600-second interval".
DEFAULT_DURATION_S = 600.0
# Fewer cycles than this per run give a noisy mean, so the sweep warns.
MIN_CYCLES_PER_POINT = 20

QUICK_DURATION_S = 3.0
QUICK_D_FREE_MS: tuple[float, ...] = (31.0, 62.0)
QUICK_D_SENSING_MS: tuple[float, ...] = (10.0, 50.0)

SENSING_CONFIG = "configs/exp4_cowic_full.yaml"
# Fig. 16 uses Traffic I and Fig. 17 uses Traffic III (Sec. 5.3.2).
SENSING_TRAFFICS: tuple[str, ...] = ("Traffic_I", "Traffic_III")
SENSING_PERIOD_MS = 1000.0

# Fig. 15: "evaluations under T_ZB = 1000 ms and D_free = 63 ms across the full
# range of WiFi datasets" (Sec. 5.3.2).
OCCUPANCY_D_FREE_MS = 63.0
OCCUPANCY_PERIOD_MS = 1000.0
OCCUPANCY_TRAFFIC: tuple[str, ...] = tuple(TRAFFIC_PROFILES)

# Absolute tolerance for each paper point in the acceptance check.
ACCEPT_TOLERANCE: dict[str, float] = {
    "zigbee_gain_ratio": 0.02,
    "wifi_drop_rate": 0.005,
}
# Every paper point of Sec. 5.3.1 is on Traffic I with T_ZB = 1000 ms (Fig. 11).
ACCEPT_TRAFFIC = "Traffic_I"
ACCEPT_PERIOD_MS = 1000.0


@dataclass(frozen=True)
class PaperPoint:
    """One value that Sec. 5.3.1 of the paper states for Fig. 11.

    The paper value lies in ``[low, high]``. A single value has
    ``low == high``.
    """

    experiment_id: str
    metric: str
    d_free_ms: float
    low: float
    high: float


def _points(
    experiments: Sequence[str],
    metric: str,
    d_free: Sequence[float],
    low: float,
    high: float,
) -> list[PaperPoint]:
    return [
        PaperPoint(exp, metric, demand, low, high)
        for exp in experiments
        for demand in d_free
    ]


_GZ = "zigbee_gain_ratio"
_PDR = "wifi_drop_rate"
PAPER_POINTS: tuple[PaperPoint, ...] = (
    # "The proposed CoWiC-MAC (Exp 4 and Exp 6) achieves the most balanced and
    # robust performance. At D_free = 31 ms, both configurations achieve
    # G_z > 98.6%."
    *_points(("Exp_4", "Exp_6"), _GZ, (31.0,), 0.986, 1.0),
    # "As demand increases to D_free = 62 ms, the gain remains high at 96.3%,
    # demonstrating strong adaptability under heavy WiFi load."
    *_points(("Exp_4", "Exp_6"), _GZ, (62.0,), 0.963, 0.963),
    # "Even at D_free = 123 ms, G_z remains stable between 93.4% and 94.7%,
    # while consistently achieving zero WiFi FG loss"
    *_points(("Exp_4", "Exp_6"), _GZ, (123.0,), 0.934, 0.947),
    *_points(("Exp_4", "Exp_6"), _PDR, (31.0, 62.0, 123.0), 0.0, 0.0),
    # "Exp 5 enforces rigid reservations to fully satisfy ZigBee demand,
    # maintaining G_z = 100%."
    *_points(("Exp_5",), _GZ, (31.0, 62.0, 123.0), 1.0, 1.0),
    # "At D_free = 31 ms, Exp 3 and Exp 5 exhibit similar performance, both
    # achieving near-complete ZigBee transmission with less than 1% WiFi FG
    # loss."
    *_points(("Exp_5",), _PDR, (31.0,), 0.0, 0.01),
    # "This comes at a cost: at D_free = 123 ms, PDR_w reaches 2.8%"
    *_points(("Exp_5",), _PDR, (123.0,), 0.028, 0.028),
)


@dataclass(frozen=True)
class SweepPoint:
    """One simulation run of the sweep."""

    sweep: str
    config_path: str
    traffic: str
    d_free_ms: float
    zigbee_period_ms: float
    seed: int
    d_sensing_ms: float | None = None  # None keeps the value in the config.


def build_grid(
    quick: bool = False,
    experiments: Sequence[str] = EXPERIMENT_CONFIGS,
    traffic_profiles: Sequence[str] = DEFAULT_SWEEP_TRAFFIC,
    seeds: Sequence[int] = SEEDS,
) -> list[SweepPoint]:
    """Return every run of the main, occupancy and sensing stages.

    ``traffic_profiles`` selects the profiles of the main stage only. The
    occupancy stage always uses all four profiles (Fig. 15). The sensing stage
    always uses Traffic I and Traffic III (Figs. 16 and 17).

    The quick grid uses T_ZB = 1000 ms, one seed, two demands and two sensing
    windows. Its main stage uses Traffic I only and its occupancy stage uses
    Exp 4 only. It is for smoke runs and tests.
    """
    d_free = QUICK_D_FREE_MS if quick else D_FREE_MS
    traffics = ("Traffic_I",) if quick else tuple(traffic_profiles)
    periods = (1000.0,) if quick else ZIGBEE_PERIODS_MS
    sensing = QUICK_D_SENSING_MS if quick else D_SENSING_MS
    run_seeds = tuple(seeds[:1]) if quick else tuple(seeds)
    occupancy_experiments = (SENSING_CONFIG,) if quick else tuple(experiments)
    grid = [
        SweepPoint(MAIN_SWEEP, config, traffic, demand, period, seed)
        for config in experiments
        for traffic in traffics
        for period in periods
        for demand in d_free
        for seed in run_seeds
    ]
    grid += [
        SweepPoint(
            OCCUPANCY_SWEEP,
            config,
            traffic,
            OCCUPANCY_D_FREE_MS,
            OCCUPANCY_PERIOD_MS,
            seed,
        )
        for config in occupancy_experiments
        for traffic in OCCUPANCY_TRAFFIC
        for seed in run_seeds
    ]
    grid += [
        SweepPoint(
            SENSING_SWEEP,
            SENSING_CONFIG,
            traffic,
            demand,
            SENSING_PERIOD_MS,
            seed,
            d_sensing_ms=window,
        )
        for traffic in SENSING_TRAFFICS
        for window in sensing
        for demand in d_free
        for seed in run_seeds
    ]
    return grid


def cycles_per_run(point: SweepPoint, duration_s: float) -> int:
    """Return the number of full ZigBee cycles that one run of ``point`` has."""
    return int(duration_s * 1000.0 // point.zigbee_period_ms)


def warn_short_runs(grid: Iterable[SweepPoint], duration_s: float) -> int:
    """Warn if a run has fewer than ``MIN_CYCLES_PER_POINT`` cycles.

    Returns the smallest number of cycles per run in ``grid`` (0 if empty).
    """
    cycles = [cycles_per_run(point, duration_s) for point in grid]
    fewest = min(cycles, default=0)
    if cycles and fewest < MIN_CYCLES_PER_POINT:
        warnings.warn(
            f"{duration_s:g} s runs give only {fewest} cycles for some grid "
            f"points (fewer than {MIN_CYCLES_PER_POINT}). Their means are noisy.",
            stacklevel=2,
        )
    return fewest


def trace_for(traffic: str, duration_s: float, seed: int, trace_dir: Path) -> Path:
    """Return the cached trace for a traffic profile, duration and seed."""
    if traffic not in TRAFFIC_PROFILES:
        raise ConfigurationError(f"Unknown traffic profile {traffic!r}")
    path = trace_dir / f"{traffic}_{duration_s:g}s_{seed}.parquet"
    if not path.exists():
        generate_trace(TRAFFIC_PROFILES[traffic], duration_s, path, seed)
    return path


def run_point(
    point: SweepPoint, duration_s: float, trace_dir: Path
) -> dict[str, float | str]:
    """Run one sweep point and return its CSV row."""
    config = SimulationConfig.from_yaml(REPO_ROOT / point.config_path, trace_dir)
    trace_path = trace_for(point.traffic, duration_s, point.seed, trace_dir)
    config = replace(
        config,
        duration_s=duration_s,
        d_free_ms=point.d_free_ms,
        zigbee_period_ms=point.zigbee_period_ms,
        random_seed=point.seed,
        wifi_trace_path=trace_path,
        d_sensing_ms=(
            config.d_sensing_ms if point.d_sensing_ms is None else point.d_sensing_ms
        ),
    )
    # Busy airtime over run time of the trace that this run replays (Fig. 15).
    occupancy = summarize_trace(pd.read_parquet(trace_path), duration_s)["occupancy"]

    engine = SimulationEngine(config)
    began = time.perf_counter()
    summary = engine.run()
    elapsed_s = time.perf_counter() - began
    cycles = int(summary["cycles_run"])
    metrics = measure_run(engine, summary)
    row: dict[str, float | str] = {
        "sweep": point.sweep,
        "experiment_id": config.scenario_name,
        "traffic_profile": point.traffic,
        "d_free_ms": config.d_free_ms,
        "d_sensing_ms": config.d_sensing_ms,
        "zigbee_period_ms": config.zigbee_period_ms,
        "seed": point.seed,
        "duration_s": duration_s,
        "cycles_run": float(cycles),
        "fallback_cycles": summary["fallback_cycles"],
        "milp_unproven_cycles": summary["milp_unproven_cycles"],
        "sensing_missed_cycles": summary["sensing_missed_cycles"],
        "dropped_frame_groups": summary["dropped_frame_groups"],
        "runtime_s": elapsed_s,
        OCCUPANCY_COLUMN: occupancy,
    }
    row.update(asdict(metrics))
    return row


def run_sweep(
    grid: Iterable[SweepPoint],
    duration_s: float,
    trace_dir: Path,
    jobs: int = 1,
    progress: Callable[[int, int, SweepPoint], None] | None = None,
) -> pd.DataFrame:
    """Run every point and return one row per run.

    With ``jobs > 1`` the runs execute in parallel processes. The rows keep
    the order of ``grid`` either way.
    """
    points = list(grid)
    trace_dir.mkdir(parents=True, exist_ok=True)
    # Make the traces first, so parallel workers do not write the same file.
    for traffic, seed in sorted({(p.traffic, p.seed) for p in points}):
        trace_for(traffic, duration_s, seed, trace_dir)
    for config_path in sorted({p.config_path for p in points}):
        SimulationConfig.from_yaml(REPO_ROOT / config_path, trace_dir)

    rows: list[dict[str, float | str]] = []
    if jobs <= 1:
        for index, point in enumerate(points, start=1):
            rows.append(run_point(point, duration_s, trace_dir))
            if progress is not None:
                progress(index, len(points), point)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(run_point, point, duration_s, trace_dir) for point in points
            ]
            for index, (point, future) in enumerate(
                zip(points, futures, strict=True), start=1
            ):
                rows.append(future.result())
                if progress is not None:
                    progress(index, len(points), point)
    return pd.DataFrame(rows)


_METRIC_LABELS: dict[str, str] = {_GZ: "G_z", _PDR: "PDR_w"}


def acceptance_report(results: pd.DataFrame) -> tuple[bool, str]:
    """Compare the sweep with every G_z and PDR_w value of Sec. 5.3.1.

    The check uses the main-stage rows on Traffic I with T_ZB = 1000 ms
    (Fig. 11) and averages them over seeds. A point passes when the mean lies
    in the paper range widened by ``ACCEPT_TOLERANCE``. A point without rows
    in the sweep is SKIP. The result is PASS only when no point fails and at
    least one point is checked.
    """
    rows = results[
        (results["sweep"] == MAIN_SWEEP)
        & (results["traffic_profile"] == ACCEPT_TRAFFIC)
        & (results["zigbee_period_ms"] == ACCEPT_PERIOD_MS)
    ]
    means = rows.groupby(["experiment_id", "d_free_ms"])[[_GZ, _PDR]].mean()
    tolerances = ", ".join(
        f"{_METRIC_LABELS[metric]} +/- {tol:g}"
        for metric, tol in ACCEPT_TOLERANCE.items()
    )
    lines = [
        f"Acceptance vs paper Sec. 5.3.1 ({ACCEPT_TRAFFIC}, "
        f"T_ZB={ACCEPT_PERIOD_MS:g} ms, tolerance {tolerances}):"
    ]
    checked = failed = 0
    for point in PAPER_POINTS:
        label = (
            f"  {point.experiment_id} {_METRIC_LABELS[point.metric]} "
            f"D_free={point.d_free_ms:g}: paper "
        )
        label += (
            f"{point.low:.3f}"
            if point.low == point.high
            else f"{point.low:.3f}-{point.high:.3f}"
        )
        key = (point.experiment_id, point.d_free_ms)
        if key not in means.index:
            lines.append(f"{label}, sim -    SKIP (not in sweep)")
            continue
        value = float(means.loc[key, point.metric])
        tol = ACCEPT_TOLERANCE[point.metric]
        ok = point.low - tol <= value <= point.high + tol
        checked += 1
        failed += not ok
        lines.append(f"{label}, sim {value:.3f} {'PASS' if ok else 'FAIL'}")
    passed = checked > 0 and failed == 0
    if checked == 0:
        lines.append("No paper point is in the sweep.")
    lines.append(
        f"Result: {'PASS' if passed else 'FAIL'} "
        f"({checked - failed}/{checked} checked points pass, "
        f"{len(PAPER_POINTS) - checked} skipped)"
    )
    return passed, "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION_S,
        help=f"Seconds per run ({DEFAULT_DURATION_S:g}, as the paper traces).",
    )
    parser.add_argument(
        "--quick", action="store_true", help="Small grid and 3 s runs (smoke run)."
    )
    parser.add_argument(
        "--traffic",
        nargs="+",
        choices=list(TRAFFIC_PROFILES),
        default=list(DEFAULT_SWEEP_TRAFFIC),
        help="Traffic profiles to sweep (default: Traffic_I, Traffic_III).",
    )
    parser.add_argument(
        "--output-csv", type=Path, default=Path("results/benchmark_results.csv")
    )
    parser.add_argument("--figures-dir", type=Path, default=Path("figures"))
    parser.add_argument(
        "--trace-dir", type=Path, default=Path("results/traces"), help="Trace cache."
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=os.cpu_count() or 1,
        help="Parallel runs (default: number of CPUs).",
    )
    parser.add_argument(
        "--seeds",
        type=int,
        default=len(SEEDS),
        help=f"Use the first N seeds of {SEEDS} (default: all).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if the acceptance check fails.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the sweep, write the CSV and figures, and report acceptance."""
    args = parse_args(argv)
    duration_s = QUICK_DURATION_S if args.quick else args.duration
    if duration_s <= 0:
        raise ConfigurationError(f"--duration must be positive, got {duration_s}")
    if not 1 <= args.seeds <= len(SEEDS):
        raise ConfigurationError(
            f"--seeds must be between 1 and {len(SEEDS)}, got {args.seeds}"
        )
    grid = build_grid(
        quick=args.quick,
        traffic_profiles=args.traffic,
        seeds=SEEDS[: args.seeds],
    )
    print(f"Running {len(grid)} runs of {duration_s:g} s with {args.jobs} jobs.")
    warn_short_runs(grid, duration_s)

    def progress(done: int, total: int, point: SweepPoint) -> None:
        print(f"  [{done}/{total}] {Path(point.config_path).stem} {point.traffic}")

    results = run_sweep(grid, duration_s, args.trace_dir, args.jobs, progress)
    missing = [c for c in REQUIRED_COLUMNS if c not in results.columns]
    if missing:
        raise ConfigurationError(f"Results lack plotter columns {missing}")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(args.output_csv, index=False)
    print(f"Wrote {len(results)} rows to {args.output_csv}")

    for path in write_figures(results, args.figures_dir):
        print(f"Wrote {path}")

    passed, report = acceptance_report(results)
    print(report)
    return 1 if args.strict and not passed else 0


if __name__ == "__main__":
    raise SystemExit(main())
