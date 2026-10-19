"""Compare simulated G_z and PDR_w with the values of paper Sec. 5.3.1.

Issue #53 item 4. These runs are slow, so they carry the ``slow`` marker and
run with ``pytest -m slow`` (a separate CI job). Each point uses one 30 s run
of Traffic I (seed 42, T_ZB = 1000 ms, 30 cycles) and the tolerance of
``scripts.run_benchmark_sweep.ACCEPT_TOLERANCE``.

Points that this model does not reproduce are ``xfail`` entries (strict unless
listed in ``BORDERLINE``) with
the measured cause. If a later change brings such a point inside the
tolerance, the test fails as XPASS. Then move the point out of
``KNOWN_DEVIATIONS`` and update Review 3 §9.
"""

import os
from pathlib import Path

import pandas as pd
import pytest

from cowic.analysis.plotter import MAIN_SWEEP
from scripts import run_benchmark_sweep as sweep

pytestmark = pytest.mark.slow

DURATION_S = 30.0
SEED = 42
_GAIN = (
    "Traffic I is about 88 % busy. The soft MILP keeps WiFi in part of the "
    "window when deferring it costs more (lambda = 1, mu_base = 100: the paper "
    "gives no values). At 123 ms the cycle needs about 101 % of its airtime. "
    "Exp 4 also has no demand report in its first cycle (1 of 30)."
)
_LOSS = (
    "An FG postponed near a cycle end can not fit the start of the next "
    "cycle. Eq. 14 checks one cycle, and the AP does not see later arrivals. "
    "mu_base = 10000 removes these drops but lowers Exp 4 G_z at 31 ms to "
    "0.71 (Issue #60)."
)
_RIGID = (
    "Exp 5 vacates every FG from the window. At 88 % load more FGs then miss "
    "their deadline than the paper reports."
)
# (experiment_id, metric, d_free_ms) -> measured reason for the deviation.
KNOWN_DEVIATIONS: dict[tuple[str, str, float], str] = {
    **{
        (exp, "zigbee_gain_ratio", d_free): _GAIN
        for exp in ("Exp_4", "Exp_6")
        for d_free in (31.0, 62.0, 123.0)
    },
    **{
        (exp, "wifi_drop_rate", d_free): _LOSS
        for exp in ("Exp_4", "Exp_6")
        for d_free in (31.0, 62.0, 123.0)
    },
    ("Exp_5", "wifi_drop_rate", 31.0): _RIGID,
    ("Exp_5", "wifi_drop_rate", 123.0): _RIGID,
}

# Within 0.015 of the tolerance edge at 30 s and inside it at 60 s
# (reference sweep): not strict, so a faster or slower solver can not fail CI.
BORDERLINE: frozenset[tuple[str, str, float]] = frozenset(
    {("Exp_6", "zigbee_gain_ratio", 31.0), ("Exp_5", "wifi_drop_rate", 31.0)}
)


def _config_for(experiment_id: str) -> str:
    number = experiment_id.split("_")[1]
    (config,) = [c for c in sweep.EXPERIMENT_CONFIGS if f"exp{number}_" in c]
    return config


@pytest.fixture(scope="module")
def means(tmp_path_factory: pytest.TempPathFactory) -> pd.DataFrame:
    """Seed-42 means for every (experiment, D_free) that a paper point uses."""
    keys = sorted({(p.experiment_id, p.d_free_ms) for p in sweep.PAPER_POINTS})
    grid = [
        sweep.SweepPoint(
            MAIN_SWEEP,
            _config_for(experiment_id),
            sweep.ACCEPT_TRAFFIC,
            d_free,
            sweep.ACCEPT_PERIOD_MS,
            SEED,
        )
        for experiment_id, d_free in keys
    ]
    trace_dir = tmp_path_factory.mktemp("paper_traces")
    results = sweep.run_sweep(grid, DURATION_S, Path(trace_dir), os.cpu_count() or 1)
    return results.groupby(["experiment_id", "d_free_ms"])[
        ["zigbee_gain_ratio", "wifi_drop_rate"]
    ].mean()


def _params() -> list[object]:
    params: list[object] = []
    for point in sweep.PAPER_POINTS:
        key = (point.experiment_id, point.metric, point.d_free_ms)
        name = f"{point.experiment_id}-{point.metric}-{point.d_free_ms:g}"
        reason = KNOWN_DEVIATIONS.get(key)
        # Near-threshold points can flip with solver timing on other machines.
        strict = key not in BORDERLINE
        marks = [pytest.mark.xfail(strict=strict, reason=reason)] if reason else []
        params.append(pytest.param(point, id=name, marks=marks))
    return params


@pytest.mark.parametrize("point", _params())
def test_matches_paper_point(means: pd.DataFrame, point: sweep.PaperPoint) -> None:
    value = float(means.loc[(point.experiment_id, point.d_free_ms), point.metric])
    tol = sweep.ACCEPT_TOLERANCE[point.metric]
    assert point.low - tol <= value <= point.high + tol, (
        f"{point.experiment_id} {point.metric} at D_free={point.d_free_ms:g}: "
        f"simulated {value:.4f}, paper {point.low:.3f}-{point.high:.3f}"
    )
