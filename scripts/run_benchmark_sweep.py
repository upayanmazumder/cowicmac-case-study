"""Run the benchmark sweeps for Issue #11 and draw the evaluation figures.

The sweep covers Exp 1 to Exp 6 (``configs/exp*.yaml``). It sweeps the
ZigBee demand, the traffic profile, the ZigBee period and the random seed.
A second sweep varies the sensing window for Exp 4 on Traffic I (Fig. 16).

Each run gives one CSV row. The row holds the sweep parameters and the
:class:`~cowic.analysis.metrics.SimulationMetrics` fields. The script then
averages the seeds and draws Figs. 11, 14 and 16 with
:func:`cowic.analysis.plotter.write_figures`. To redraw the figures from an
existing CSV, run ``python -m cowic.analysis``.

Usage::

    python scripts/run_benchmark_sweep.py            # full sweep
    python scripts/run_benchmark_sweep.py --quick    # smoke run
"""

import argparse
import os
import sys
import time
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
    REQUIRED_COLUMNS,
    SENSING_SWEEP,
    write_figures,
)
from cowic.exceptions import ConfigurationError  # noqa: E402
from cowic.sim.simulation_engine import (  # noqa: E402
    SimulationConfig,
    SimulationEngine,
)
from traces.synthetic_generator import generate_trace  # noqa: E402

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

QUICK_DURATION_S = 3.0
QUICK_D_FREE_MS: tuple[float, ...] = (31.0, 62.0)
QUICK_D_SENSING_MS: tuple[float, ...] = (10.0, 50.0)

SENSING_CONFIG = "configs/exp4_cowic_full.yaml"
SENSING_TRAFFIC = "Traffic_I"
SENSING_PERIOD_MS = 1000.0

# Acceptance criterion of Issue #11 (Exp 4, high load).
ACCEPT_MIN_GZ = 0.9
ACCEPT_MAX_PDR_W = 0.0
ACCEPT_HIGH_LOAD_D_FREE_MS = 31.0
ACCEPT_HIGH_LOAD_PERIOD_MS = 1000.0


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
    """Return every run of the main sweep and the sensing sweep.

    The quick grid uses Traffic I, T_ZB = 1000 ms, one seed, two demands and
    two sensing windows. It is for smoke runs and tests.
    """
    d_free = QUICK_D_FREE_MS if quick else D_FREE_MS
    traffics = ("Traffic_I",) if quick else tuple(traffic_profiles)
    periods = (1000.0,) if quick else ZIGBEE_PERIODS_MS
    sensing = QUICK_D_SENSING_MS if quick else D_SENSING_MS
    run_seeds = tuple(seeds[:1]) if quick else tuple(seeds)
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
            SENSING_SWEEP,
            SENSING_CONFIG,
            SENSING_TRAFFIC,
            demand,
            SENSING_PERIOD_MS,
            seed,
            d_sensing_ms=window,
        )
        for window in sensing
        for demand in d_free
        for seed in run_seeds
    ]
    return grid


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
    overrides: dict[str, object] = {
        "duration_s": duration_s,
        "d_free_ms": point.d_free_ms,
        "zigbee_period_ms": point.zigbee_period_ms,
        "random_seed": point.seed,
        "wifi_trace_path": trace_for(point.traffic, duration_s, point.seed, trace_dir),
    }
    if point.d_sensing_ms is not None:
        overrides["d_sensing_ms"] = point.d_sensing_ms
    config = replace(config, **overrides)  # type: ignore[arg-type]

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


def acceptance_report(results: pd.DataFrame) -> tuple[bool, str]:
    """Check Issue #11's criterion: Exp 4 under high load (Traffic I).

    The criterion is G_z > 0.9 with zero WiFi FG drop, averaged over seeds.
    """
    rows = results[
        (results["sweep"] == MAIN_SWEEP)
        & (results["experiment_id"] == "Exp_4")
        & (results["traffic_profile"] == "Traffic_I")
    ]
    if rows.empty:
        return False, "Acceptance: no Exp 4 / Traffic I rows in the sweep."
    means = rows.groupby(["d_free_ms", "zigbee_period_ms"])[
        ["zigbee_gain_ratio", "wifi_drop_rate"]
    ].mean()
    lines = ["Acceptance (Exp 4, Traffic I): G_z > 0.9 and PDR_w = 0"]
    passed = True
    has_criterion_point = False
    for (d_free, period), row in means.iterrows():
        ok = (
            row["zigbee_gain_ratio"] > ACCEPT_MIN_GZ
            and row["wifi_drop_rate"] <= ACCEPT_MAX_PDR_W
        )
        is_criterion_point = (
            d_free == ACCEPT_HIGH_LOAD_D_FREE_MS
            and period == ACCEPT_HIGH_LOAD_PERIOD_MS
        )
        if is_criterion_point:
            has_criterion_point = True
            passed = passed and ok
        status = "PASS" if ok else ("FAIL" if is_criterion_point else "INFO")
        lines.append(
            f"  D_free={d_free:g} T_ZB={period:g}: G_z={row['zigbee_gain_ratio']:.3f}"
            f" PDR_w={row['wifi_drop_rate']:.4f} {status}"
        )
    if not has_criterion_point:
        passed = all(
            row["zigbee_gain_ratio"] > ACCEPT_MIN_GZ
            and row["wifi_drop_rate"] <= ACCEPT_MAX_PDR_W
            for _, row in means.iterrows()
        )
    lines.append("Result: PASS" if passed else "Result: FAIL")
    return passed, "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--duration", type=float, default=60.0, help="Seconds per run (60)."
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
    if args.traffic == list(DEFAULT_SWEEP_TRAFFIC):
        grid = build_grid(quick=args.quick)
    else:
        grid = build_grid(quick=args.quick, traffic_profiles=args.traffic)
    print(f"Running {len(grid)} runs of {duration_s:g} s with {args.jobs} jobs.")

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
