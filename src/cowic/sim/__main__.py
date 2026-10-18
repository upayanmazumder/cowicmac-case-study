"""Run one experiment config and print its Section 5.1 metrics.

Usage::

    python -m cowic.sim --config configs/exp4_cowic_full.yaml
    python -m cowic.sim --config configs/exp1_uncoordinated.yaml --duration 10 --json
"""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from cowic.analysis.metrics import measure_run
from cowic.exceptions import CoWiCError
from cowic.sim.simulation_engine import (
    TRACE_DATA_DIR,
    SimulationConfig,
    SimulationEngine,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m cowic.sim", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="Experiment YAML (configs/)."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Simulated seconds; overrides the config's duration_s.",
    )
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=TRACE_DATA_DIR,
        help="Where the generated WiFi trace is cached (default: traces/data).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print one JSON object instead of text."
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the experiment. Returns 0 on success, 2 on a configuration error."""
    args = parse_args(argv)
    if not args.config.is_file():
        print(f"error: config not found: {args.config}", file=sys.stderr)
        return 2
    try:
        config = SimulationConfig.from_yaml(args.config, args.trace_dir, args.duration)
        engine = SimulationEngine(config)
        summary = engine.run()
        metrics = measure_run(engine, summary)
    except CoWiCError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = {
        "experiment_id": config.scenario_name,
        "duration_s": config.duration_s,
        "d_free_ms": config.d_free_ms,
        "d_sensing_ms": config.d_sensing_ms,
        "zigbee_period_ms": config.zigbee_period_ms,
        "cycles_run": int(summary["cycles_run"]),
        "fallback_cycles": int(summary["fallback_cycles"]),
        **asdict(metrics),
    }
    if args.json:
        print(json.dumps(result))
    else:
        width = max(len(key) for key in result)
        for key, value in result.items():
            text = f"{value:.4f}" if isinstance(value, float) else str(value)
            print(f"{key:<{width}}  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
