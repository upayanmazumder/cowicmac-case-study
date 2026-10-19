"""Metrics computation for coexistence evaluation (base paper, Section 5.1).

Every metric is computed from simulation ground truth, so it does not depend
on how a run was driven:

* ``transmissions``: :class:`~cowic.sim.channel_medium.TransmissionRecord`
  objects from the shared channel (``SimulationEngine.last_medium``), whose
  ``success`` flags already reflect every collision.
* ``decisions`` and ``frame_groups``: the WiFi scheduling decisions of every
  cycle and the FGs they refer to.

Definitions:

* **G_z**: delivered ZigBee payload over the theoretical maximum payload
  under ideal interference-free conditions. The engine computes both per
  cycle (``SimulationEngine.last_zigbee_delivered_payload_ms`` and
  ``last_zigbee_ideal_payload_ms``): payload is ``t_pk`` minus the PHY header
  (Eq. 7), the ideal is the same ZigBee model in the same window with no
  WiFi, and only packets inside the reported demand count.
  :func:`zigbee_gain_ratio` is the older airtime proxy over
  :func:`zigbee_clean_capacity_ms`, kept for callers without those inputs.
* **PDR_w**: WiFi FG airtime lost over all requested FG airtime. An FG is
  lost when it is postponed past its deadline, or, if ``delivered_fg_ids``
  is given, when it was transmitted but destroyed on the medium.
  ``wifi_collision_rate`` is kept as a diagnostic.
* **U_eff**: successful WiFi airtime, successful ZigBee airtime and control
  (sensing) airtime as a fraction of the simulated horizon. Sensing airtime
  counts only windows in which a ZBeacon was actually sent.
* **Delay overhead**: jitter (population standard deviation of the delay of
  delivered FGs), the number of FGs moved from their original start, and the
  largest delay of a delivered FG.
"""

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cowic.exceptions import ConfigurationError
from cowic.models.frame_group import FrameGroup, SchedulingDecision
from cowic.sim.channel_medium import TransmissionRecord

if TYPE_CHECKING:
    from cowic.sim.simulation_engine import SimulationEngine

WIFI = "wifi"
ZIGBEE = "zigbee"
ZIGBEE_BEACON = "zigbee_beacon"
ZBEACON = "zbeacon"
MOVED_TOLERANCE_MS = 1e-6  # Delays below this are solver round-off, not moves.


@dataclass(frozen=True)
class SimulationMetrics:
    """Summary of performance indicators from a simulation run."""

    zigbee_gain_ratio: float
    wifi_drop_rate: float
    spectrum_utilization: float
    wifi_jitter_ms: float
    moved_fg_count: int
    max_fg_delay_ms: float
    wifi_collision_rate: float


def zigbee_clean_capacity_ms(d_free_ms: float, cycles: int) -> float:
    """ZigBee airtime a clean medium would carry: ``D_free`` every cycle."""
    _check_non_negative(d_free_ms=d_free_ms)
    if cycles < 0:
        raise ConfigurationError(f"cycles must be non-negative, got {cycles}")
    return d_free_ms * cycles


def zigbee_gain_ratio(
    transmissions: Iterable[TransmissionRecord],
    zigbee_capacity_ms: float,
    horizon_ms: float | None = None,
) -> float:
    """G_z: successful ZigBee airtime over clean-medium capacity, at most 1.

    ``horizon_ms``, if given, clips each record to ``[0, horizon_ms]``
    first, matching :func:`spectrum_utilization`'s windowing.
    """
    _check_non_negative(zigbee_capacity_ms=zigbee_capacity_ms)
    delivered = _airtime(
        transmissions, ZIGBEE, successful_only=True, horizon_ms=horizon_ms
    )
    if zigbee_capacity_ms == 0:
        return 0.0
    return min(1.0, delivered / zigbee_capacity_ms)


def zigbee_payload_gain(delivered_payload_ms: float, ideal_payload_ms: float) -> float:
    """G_z: delivered ZigBee payload over the ideal clean-window payload.

    Raises:
        ConfigurationError: On negative input, or delivered above ideal
            (the engine caps each cycle at its ideal, so this signals a bug).
    """
    _check_non_negative(
        delivered_payload_ms=delivered_payload_ms, ideal_payload_ms=ideal_payload_ms
    )
    if delivered_payload_ms > ideal_payload_ms * (1 + 1e-9) + 1e-9:
        raise ConfigurationError(
            f"Delivered ZigBee payload {delivered_payload_ms} exceeds the ideal "
            f"{ideal_payload_ms}"
        )
    return delivered_payload_ms / ideal_payload_ms if ideal_payload_ms else 0.0


def wifi_drop_rate(
    decisions: Iterable[SchedulingDecision],
    frame_groups: Mapping[int, FrameGroup],
    delivered_fg_ids: set[int] | None = None,
) -> float:
    """PDR_w: airtime of lost FGs over all requested FG airtime.

    An FG is lost if it was postponed past its deadline. With
    ``delivered_fg_ids``, an FG that was transmitted (its final decision is
    not a postponement) but is not in the set was destroyed on the medium
    and is lost too (Issue #34). An FG still waiting at the end of the run
    is not lost.

    Each ``fg_id`` is counted once, using its last (final) decision in
    ``decisions`` - a caller that supplies more than one entry per FG
    (e.g. a full per-cycle decision history rather than one final outcome
    per FG) would otherwise have that FG's airtime added to both
    ``requested`` and, if applicable, ``dropped`` once per entry.
    """
    final_by_id: dict[int, SchedulingDecision] = {}
    for decision in decisions:
        final_by_id[decision.fg_id] = decision
    requested = 0.0
    dropped = 0.0
    for decision in final_by_id.values():
        fg = frame_groups[decision.fg_id]
        requested += fg.duration_ms
        collided = (
            delivered_fg_ids is not None
            and not decision.is_postponed
            and decision.fg_id not in delivered_fg_ids
        )
        if collided or _is_dropped(decision, fg):
            dropped += fg.duration_ms
    return dropped / requested if requested else 0.0


def wifi_collision_rate(
    transmissions: Iterable[TransmissionRecord], horizon_ms: float | None = None
) -> float:
    """Share of transmitted WiFi airtime corrupted by a collision.

    ``horizon_ms``, if given, clips each record to ``[0, horizon_ms]``
    first, matching :func:`spectrum_utilization`'s windowing so airtime
    outside the simulated run is not double-counted against it.
    """
    records = [r for r in transmissions if r.protocol == WIFI]
    total = _airtime(records, WIFI, successful_only=False, horizon_ms=horizon_ms)
    if not total:
        return 0.0
    successful = _airtime(records, WIFI, successful_only=True, horizon_ms=horizon_ms)
    return 1.0 - successful / total


def spectrum_utilization(
    transmissions: Iterable[TransmissionRecord],
    horizon_ms: float,
    control_airtime_ms: float = 0.0,
) -> float:
    """U_eff: useful airtime (successful WiFi + ZigBee + control) over horizon.

    Airtime outside ``[0, horizon_ms]`` is ignored, and the result is capped
    at 1 in case the caller's control airtime overlaps data airtime.
    """
    if not math.isfinite(horizon_ms) or horizon_ms <= 0:
        raise ConfigurationError(f"horizon_ms must be positive, got {horizon_ms}")
    _check_non_negative(control_airtime_ms=control_airtime_ms)
    useful = control_airtime_ms
    for record in transmissions:
        if record.success and record.protocol in (WIFI, ZIGBEE, ZIGBEE_BEACON):
            start = max(record.start_time_ms, 0.0)
            end = min(record.end_time_ms, horizon_ms)
            useful += max(0.0, end - start)
    return min(1.0, useful / horizon_ms)


def delay_overhead(
    decisions: Iterable[SchedulingDecision],
    frame_groups: Mapping[int, FrameGroup],
    delivered_fg_ids: set[int] | None = None,
) -> tuple[float, int, float]:
    """Return ``(jitter_ms, moved_fg_count, max_fg_delay_ms)``.

    Jitter and maximum delay cover delivered FGs only; a dropped FG has no
    delivery delay. Every FG moved from its original start counts as moved:
    postponed, later, or earlier. ``delay_ms`` is never negative, so an FG
    moved earlier is detected from its scheduled start instead.

    ``delivered_fg_ids``, if given, refines "delivered" beyond the deadline
    heuristic in ``_is_dropped``: an FG scheduled on time but lost to a
    channel collision (tracked separately via ``wifi_collision_rate``, not
    a scheduling drop) is excluded too. Pass the set of ``fg_id``s with a
    successful registered WiFi transmission; omit it to fall back to the
    deadline-only heuristic when per-FG delivery data isn't available.
    """
    delays: list[float] = []
    moved = 0
    for decision in decisions:
        fg = frame_groups[decision.fg_id]
        shift = abs(decision.scheduled_start_ms - fg.original_start_ms)
        if decision.is_postponed or shift > MOVED_TOLERANCE_MS:
            moved += 1
        dropped = _is_dropped(decision, fg)
        if delivered_fg_ids is not None:
            dropped = dropped or decision.fg_id not in delivered_fg_ids
        if not dropped:
            delays.append(max(0.0, decision.delay_ms))
    jitter = statistics.pstdev(delays) if len(delays) > 1 else 0.0
    return jitter, moved, max(delays, default=0.0)


class MetricsCalculator:
    """Calculates throughput, drop rate, utilization, and delay statistics."""

    def compute(
        self,
        transmissions: Sequence[TransmissionRecord],
        decisions: Sequence[SchedulingDecision],
        frame_groups: Mapping[int, FrameGroup] | Iterable[FrameGroup],
        horizon_ms: float,
        zigbee_capacity_ms: float,
        control_airtime_ms: float = 0.0,
        delivered_fg_ids: set[int] | None = None,
        zigbee_delivered_payload_ms: float | None = None,
    ) -> SimulationMetrics:
        """Derive the Section 5.1 metrics for one simulation run.

        Args:
            transmissions: Final channel records (``ChannelMedium.transmissions``).
            decisions: WiFi scheduling decisions from every cycle.
            frame_groups: The FGs those decisions refer to, by id or as a list.
            horizon_ms: Simulated duration.
            zigbee_capacity_ms: G_z denominator. With
                ``zigbee_delivered_payload_ms``: the ideal clean-window
                payload. Without it: clean-medium airtime for the older
                airtime proxy (:func:`zigbee_gain_ratio`).
            control_airtime_ms: Sensing/control airtime counted in U_eff.
            delivered_fg_ids: ``fg_id``s with a successful registered WiFi
                transmission, for a collision-aware ``delay_overhead``
                (see its docstring). Omit if unavailable; delay/jitter
                then fall back to the deadline-only "dropped" heuristic.
                Also counts collision-destroyed FGs in PDR_w.
            zigbee_delivered_payload_ms: Delivered ZigBee payload. If given,
                G_z is :func:`zigbee_payload_gain` (Sec. 5.1).

        Raises:
            ConfigurationError: On a non-positive horizon or negative airtime.
            KeyError: If a decision refers to an unknown FG.
        """
        by_id = (
            dict(frame_groups)
            if isinstance(frame_groups, Mapping)
            else {fg.fg_id: fg for fg in frame_groups}
        )
        jitter, moved, max_delay = delay_overhead(decisions, by_id, delivered_fg_ids)
        if zigbee_delivered_payload_ms is None:
            gain = zigbee_gain_ratio(transmissions, zigbee_capacity_ms, horizon_ms)
        else:
            gain = zigbee_payload_gain(zigbee_delivered_payload_ms, zigbee_capacity_ms)
        return SimulationMetrics(
            zigbee_gain_ratio=gain,
            wifi_drop_rate=wifi_drop_rate(decisions, by_id, delivered_fg_ids),
            spectrum_utilization=spectrum_utilization(
                transmissions, horizon_ms, control_airtime_ms
            ),
            wifi_jitter_ms=jitter,
            moved_fg_count=moved,
            max_fg_delay_ms=max_delay,
            wifi_collision_rate=wifi_collision_rate(transmissions, horizon_ms),
        )


def measure_run(
    engine: "SimulationEngine", summary: Mapping[str, float]
) -> SimulationMetrics:
    """Section 5.1 metrics of the last :meth:`SimulationEngine.run`.

    ``summary`` is the dict that ``run()`` returned. G_z uses the engine's
    payload totals. Control airtime for U_eff is the length of every sensing
    window in which a ZBeacon was actually sent (Issue #47), clipped to the
    horizon.

    Raises:
        RuntimeError: If ``engine`` has not run yet.
    """
    medium = engine.last_medium
    if medium is None:
        raise RuntimeError("measure_run() called before SimulationEngine.run()")
    config = engine.config
    horizon_ms = config.duration_s * 1000.0
    sensing_ms = sum(
        max(0.0, min(end, horizon_ms) - max(start, 0.0))
        for start, end in engine.last_zbeacon_windows
    )
    return MetricsCalculator().compute(
        medium.transmissions,
        engine.last_decisions,
        engine.last_frame_groups,
        horizon_ms=horizon_ms,
        zigbee_capacity_ms=engine.last_zigbee_ideal_payload_ms,
        control_airtime_ms=sensing_ms,
        delivered_fg_ids=engine.delivered_wifi_fg_ids(),
        zigbee_delivered_payload_ms=engine.last_zigbee_delivered_payload_ms,
    )


def _is_dropped(decision: SchedulingDecision, fg: FrameGroup) -> bool:
    return (
        decision.is_postponed
        and decision.scheduled_start_ms + fg.duration_ms > fg.deadline_ms
    )


def _airtime(
    transmissions: Iterable[TransmissionRecord],
    protocol: str,
    successful_only: bool,
    horizon_ms: float | None = None,
) -> float:
    total = 0.0
    for r in transmissions:
        if r.protocol != protocol or (not r.success and successful_only):
            continue
        start, end = r.start_time_ms, r.end_time_ms
        if horizon_ms is not None:
            start, end = max(start, 0.0), min(end, horizon_ms)
        total += max(0.0, end - start)
    return total


def _check_non_negative(**values: float) -> None:
    for name, value in values.items():
        if not math.isfinite(value) or value < 0:
            raise ConfigurationError(f"{name} must be finite and >= 0, got {value}")
