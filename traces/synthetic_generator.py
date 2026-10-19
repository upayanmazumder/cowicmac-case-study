"""Synthetic WiFi Frame Group trace generator matching Table 5.

The base paper replays real tcpdump captures aggregated into Frame Groups (FGs).
Without those captures, this module synthesises traces that reproduce the
per-profile statistics reported in Table 5:

* FG durations and natural whitespaces are drawn from exponential
  distributions with the Table 5 means. The whitespace tail therefore follows
  ``P(W > t) = exp(-t / mean)``. This tail does not match the Sec. 5.2 Fig. 9
  values, and no distribution with the Table 5 mean of Traffic I can match
  them (Markov bound). ``reviews/project_document.md`` gives the proof and
  the chosen reading.
* Every FG receives a postponement bound from ``postpone_bounds_ms``
  (50/100/150/200 ms). QoS-sensitive FGs get the tightest bound, non-real-time
  FGs get one of the two loosest bounds, and the remainder get the second
  bound. The split between the two loosest bounds is solved so that the mean
  bound equals ``avg_delay_bound_ms``.
* The deadline is ``original_start + duration + bound``, so an FG may start at
  most ``bound`` milliseconds late.

Usage::

    python -m traces.synthetic_generator --profile Traffic_I \\
        --output traces/data/traffic1.parquet
"""

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from cowic.exceptions import ConfigurationError

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"

PROFILE_CONFIGS: dict[str, str] = {
    "Traffic_I": "base_traffic1.yaml",
    "Traffic_II": "base_traffic2.yaml",
    "Traffic_III": "base_traffic3.yaml",
    "Traffic_IV": "base_traffic4.yaml",
}

TRACE_COLUMNS: list[str] = [
    "fg_id",
    "arrival_time_ms",
    "duration_ms",
    "deadline_ms",
    "original_start_ms",
    "qos_sensitive",
    "delay_bound_ms",
]

DEFAULT_DURATION_S = 600.0  # The paper collects four 10-minute traces.
WHITESPACE_CCDF_THRESHOLD_MS = 10.0  # Sec. 5.2 reads Fig. 9 at 10 ms.


@dataclass(frozen=True)
class TrafficProfile:
    """Statistical description of one WiFi traffic trace (Table 5 row)."""

    name: str
    avg_fg_duration_ms: float
    avg_whitespace_ms: float
    qos_sensitive_ratio: float
    non_realtime_ratio: float
    avg_delay_bound_ms: float
    postpone_bounds_ms: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if self.avg_fg_duration_ms <= 0 or self.avg_whitespace_ms <= 0:
            raise ConfigurationError(
                f"{self.name}: FG duration and whitespace means must be positive"
            )
        for ratio in (self.qos_sensitive_ratio, self.non_realtime_ratio):
            if not 0.0 <= ratio <= 1.0:
                raise ConfigurationError(f"{self.name}: ratio {ratio} not in [0, 1]")
        if self.qos_sensitive_ratio + self.non_realtime_ratio > 1.0:
            raise ConfigurationError(
                f"{self.name}: QoS-sensitive and non-real-time ratios exceed 1"
            )
        if list(self.postpone_bounds_ms) != sorted(set(self.postpone_bounds_ms)):
            raise ConfigurationError(
                f"{self.name}: postpone bounds must be strictly increasing"
            )
        if self.postpone_bounds_ms[0] <= 0:
            raise ConfigurationError(f"{self.name}: postpone bounds must be positive")
        # Validates that avg_delay_bound_ms is reachable with these ratios.
        self.category_probabilities()

    @property
    def occupancy(self) -> float:
        """Expected fraction of airtime occupied by WiFi FGs."""
        return self.avg_fg_duration_ms / (
            self.avg_fg_duration_ms + self.avg_whitespace_ms
        )

    def category_probabilities(self) -> tuple[float, float, float, float]:
        """Probability of each postponement bound, in ``postpone_bounds_ms`` order.

        Raises:
            ConfigurationError: If no split of the non-real-time FGs between the
                two loosest bounds yields ``avg_delay_bound_ms``.
        """
        b_qos, b_mid, b_low, b_high = self.postpone_bounds_ms
        p_qos = self.qos_sensitive_ratio
        p_nrt = self.non_realtime_ratio
        p_mid = 1.0 - p_qos - p_nrt
        if p_nrt == 0.0:
            mean = p_qos * b_qos + p_mid * b_mid
            if not math.isclose(mean, self.avg_delay_bound_ms, abs_tol=1e-6):
                raise ConfigurationError(
                    f"{self.name}: avg_delay_bound_ms unreachable without "
                    "non-real-time FGs"
                )
            return (p_qos, p_mid, 0.0, 0.0)
        residual = self.avg_delay_bound_ms - p_qos * b_qos - p_mid * b_mid
        share_high = (residual / p_nrt - b_low) / (b_high - b_low)
        if not 0.0 <= share_high <= 1.0:
            raise ConfigurationError(
                f"{self.name}: avg_delay_bound_ms={self.avg_delay_bound_ms} cannot "
                "be reached with the given ratios and postpone bounds"
            )
        return (p_qos, p_mid, p_nrt * (1.0 - share_high), p_nrt * share_high)

    @classmethod
    def from_yaml(cls, path: Path) -> "TrafficProfile":
        """Load a profile from a ``configs/base_traffic*.yaml`` file."""
        try:
            raw = yaml.safe_load(path.read_text())
        except OSError as exc:
            raise ConfigurationError(f"Cannot read traffic profile {path}") from exc
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"{path}: malformed YAML") from exc
        try:
            bounds = tuple(float(b) for b in raw["postpone_bounds_ms"])
            if len(bounds) != 4:
                raise ConfigurationError(
                    f"{path}: postpone_bounds_ms needs exactly 4 values"
                )
            return cls(
                name=str(raw["name"]),
                avg_fg_duration_ms=float(raw["avg_fg_duration_ms"]),
                avg_whitespace_ms=float(raw["avg_whitespace_ms"]),
                qos_sensitive_ratio=float(raw["qos_sensitive_ratio"]),
                non_realtime_ratio=float(raw["non_realtime_ratio"]),
                avg_delay_bound_ms=float(raw["avg_delay_bound_ms"]),
                postpone_bounds_ms=(bounds[0], bounds[1], bounds[2], bounds[3]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError(f"{path}: invalid traffic profile") from exc


def load_profile(profile: str, config_dir: Path = CONFIG_DIR) -> TrafficProfile:
    """Resolve a profile name (e.g. ``Traffic_I``) or YAML path to a profile."""
    if profile in PROFILE_CONFIGS:
        return TrafficProfile.from_yaml(config_dir / PROFILE_CONFIGS[profile])
    path = Path(profile)
    if path.suffix in {".yaml", ".yml"}:
        return TrafficProfile.from_yaml(path)
    known = ", ".join(PROFILE_CONFIGS)
    raise ConfigurationError(f"Unknown traffic profile {profile!r}; use {known}")


def build_trace(
    profile: TrafficProfile, duration_s: float, seed: int = 42
) -> pd.DataFrame:
    """Synthesise a Frame Group trace as a DataFrame with ``TRACE_COLUMNS``.

    FGs are serialised on one channel: each FG is preceded by a whitespace gap,
    and only FGs that finish within ``duration_s`` are kept.
    """
    if duration_s <= 0:
        raise ConfigurationError(f"duration_s must be positive, got {duration_s}")
    horizon_ms = duration_s * 1000.0
    rng = np.random.default_rng(seed)

    # Draw a generous batch, then extend if the horizon is not yet covered.
    expected = horizon_ms / (profile.avg_fg_duration_ms + profile.avg_whitespace_ms)
    batch = int(expected * 1.2) + 64
    durations = np.empty(0)
    gaps = np.empty(0)
    while durations.sum() + gaps.sum() < horizon_ms:
        durations = np.concatenate(
            [durations, rng.exponential(profile.avg_fg_duration_ms, batch)]
        )
        gaps = np.concatenate([gaps, rng.exponential(profile.avg_whitespace_ms, batch)])

    starts = np.cumsum(gaps) + np.cumsum(durations) - durations
    keep = starts + durations <= horizon_ms
    starts, durations = starts[keep], durations[keep]

    bounds = rng.choice(
        np.asarray(profile.postpone_bounds_ms),
        size=len(starts),
        p=np.asarray(profile.category_probabilities()),
    )
    return pd.DataFrame(
        {
            "fg_id": np.arange(len(starts), dtype=np.int64),
            # Trace FGs are transmitted as soon as they become ready.
            "arrival_time_ms": starts,
            "duration_ms": durations,
            "deadline_ms": starts + durations + bounds,
            "original_start_ms": starts,
            "qos_sensitive": bounds == profile.postpone_bounds_ms[0],
            "delay_bound_ms": bounds,
        },
        columns=TRACE_COLUMNS,
    )


def summarize_trace(trace: pd.DataFrame, duration_s: float) -> dict[str, float]:
    """Compute the Table 5 statistics of a trace, for validation.

    ``whitespace_ccdf_10ms`` is the fraction of natural whitespaces longer than
    ``WHITESPACE_CCDF_THRESHOLD_MS``, the Fig. 9 statistic of Sec. 5.2.
    """
    starts = trace["original_start_ms"].to_numpy()
    ends = starts + trace["duration_ms"].to_numpy()
    gaps = starts - np.concatenate([[0.0], ends[:-1]])
    bound_max = trace["delay_bound_ms"].max()
    return {
        "frame_groups": float(len(trace)),
        "avg_fg_duration_ms": float(trace["duration_ms"].mean()),
        "avg_whitespace_ms": float(gaps.mean()),
        "whitespace_ccdf_10ms": float((gaps > WHITESPACE_CCDF_THRESHOLD_MS).mean()),
        "qos_sensitive_ratio": float(trace["qos_sensitive"].mean()),
        "non_realtime_ratio": float((trace["delay_bound_ms"] > 100.0).mean()),
        "avg_delay_bound_ms": float(trace["delay_bound_ms"].mean()),
        "max_delay_bound_ms": float(bound_max),
        "occupancy": float(trace["duration_ms"].sum() / (duration_s * 1000.0)),
    }


def generate_trace(
    traffic_profile: str,
    duration_s: float,
    output_path: Path,
    seed: int = 42,
) -> Path:
    """Generate Frame Groups matching Table 5 and write them as parquet."""
    profile = load_profile(traffic_profile)
    trace = build_trace(profile, duration_s, seed)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    trace.to_parquet(output_path, index=False)
    return output_path


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--profile",
        required=True,
        help=f"One of {', '.join(PROFILE_CONFIGS)} or a profile YAML path",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    path = generate_trace(args.profile, args.duration_s, args.output, args.seed)
    stats = summarize_trace(pd.read_parquet(path), args.duration_s)
    print(f"Wrote {path}")
    for key, value in stats.items():
        print(f"  {key}: {value:.3f}")


if __name__ == "__main__":
    main()
