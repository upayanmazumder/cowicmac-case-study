"""Redraw the evaluation figures from a benchmark results CSV.

Usage::

    python -m cowic.analysis --results-csv results/benchmark_results.csv \
        --output-dir figures/

The CSV is the one ``scripts/run_benchmark_sweep.py`` writes. The figures
and their seed-averaged data match the ones the sweep draws.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from cowic.analysis.plotter import write_figures
from cowic.exceptions import CoWiCError


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="python -m cowic.analysis", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--results-csv",
        type=Path,
        default=Path("results/benchmark_results.csv"),
        help="Sweep results (default: results/benchmark_results.csv).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("figures"),
        help="Where the PNG figures go (default: figures).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Draw the figures. Returns 0 on success, 1 if the CSV holds no sweep
    rows to plot, and 2 if the CSV is missing or malformed."""
    args = parse_args(argv)
    if not args.results_csv.is_file():
        print(f"error: results CSV not found: {args.results_csv}", file=sys.stderr)
        return 2
    try:
        results = pd.read_csv(args.results_csv)
        paths = write_figures(results, args.output_dir)
    except (CoWiCError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
        print(f"error: {args.results_csv}: {exc}", file=sys.stderr)
        return 2
    if not paths:
        print(f"error: {args.results_csv} has no sweep rows to plot", file=sys.stderr)
        return 1
    for path in paths:
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
