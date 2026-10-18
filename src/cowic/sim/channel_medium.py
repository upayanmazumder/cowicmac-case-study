"""Shared 2.4 GHz channel medium with collision detection.

The base paper assumes a single shared channel: any temporal overlap between
a WiFi Frame Group and a ZigBee transmission corrupts both ("mutual
corruption", Section 5.1). This module generalizes that rule to any pair of
registered bursts, since two overlapping WiFi FGs (e.g. from a scheduling
bug) or two overlapping ZigBee slots are equally invalid on a single-channel
half-duplex medium.

Collisions are retroactive: when a new transmission is registered, every
previously stored transmission that overlaps it is flipped from success to
failure, and the new transmission itself is recorded as failed. A
transmission that collided with an already-failed transmission remains
failed; it is never flipped back to success.

Because collisions are retroactive, the record returned by
:meth:`ChannelMedium.register_transmission` is a snapshot taken at
registration time. Read final outcomes with :meth:`ChannelMedium.record`,
:meth:`ChannelMedium.successful_transmissions` or
:meth:`ChannelMedium.failed_transmissions` once all bursts are registered.

Overlaps shorter than ``OVERLAP_TOLERANCE_MS`` are ignored, so solver
round-off (e.g. a MILP placing an FG end a fraction of a microsecond past a
window start) is not reported as a collision.
"""

import math
from dataclasses import dataclass, replace

from cowic.exceptions import ConfigurationError

# Overlaps up to 1 us are treated as touching. MILP solver round-off reaches ~1 us
# (seen on Traffic I), while one IEEE 802.15.4 symbol lasts 16 us, so no
# physically meaningful collision is hidden.
OVERLAP_TOLERANCE_MS = 1e-3


@dataclass(frozen=True)
class TransmissionRecord:
    """Record of an airtime burst on the shared medium."""

    trans_id: str
    protocol: str
    start_time_ms: float
    end_time_ms: float
    success: bool


def _intervals_overlap(
    start_a: float, end_a: float, start_b: float, end_b: float
) -> bool:
    """Return True if two half-open intervals ``[start, end)`` overlap.

    Overlaps no longer than ``OVERLAP_TOLERANCE_MS`` count as touching.
    """
    return (
        start_a < end_b - OVERLAP_TOLERANCE_MS
        and start_b < end_a - OVERLAP_TOLERANCE_MS
    )


class ChannelMedium:
    """Detects temporal overlaps between concurrent transmissions.

    Transmissions are stored in registration order in ``self.transmissions``
    (``record()``'s id lookup relies on this order staying stable). Call
    :meth:`register_transmission` once per burst as the simulation advances;
    registration order does not need to match start time order (e.g. a short
    ZigBee packet registered after a longer WiFi FG that started earlier is
    still checked against it). :meth:`successful_transmissions` and
    :meth:`failed_transmissions` return their results sorted by
    ``start_time_ms`` regardless of registration order.
    """

    def __init__(self) -> None:
        self.transmissions: list[TransmissionRecord] = []
        self._index_by_id: dict[str, int] = {}

    def register_transmission(
        self,
        trans_id: str,
        protocol: str,
        start_ms: float,
        duration_ms: float,
    ) -> TransmissionRecord:
        """Register a packet burst and evaluate its collision state.

        Returns a snapshot of the new :class:`TransmissionRecord` as of
        registration. Any previously registered transmission that overlaps
        this burst is retroactively marked as failed in ``self.transmissions``,
        so a later burst can also flip this one; use :meth:`record` for the
        current outcome.

        Raises:
            ConfigurationError: if ``start_ms`` or ``duration_ms`` is not
                finite, ``duration_ms`` is not positive, or ``trans_id`` was
                already registered.
        """
        if not (math.isfinite(start_ms) and math.isfinite(duration_ms)):
            raise ConfigurationError(
                f"start_ms and duration_ms must be finite, got "
                f"{start_ms} and {duration_ms}"
            )
        if duration_ms <= 0:
            raise ConfigurationError(f"duration_ms must be positive, got {duration_ms}")
        if trans_id in self._index_by_id:
            raise ConfigurationError(f"trans_id {trans_id!r} already registered")

        end_ms = start_ms + duration_ms
        collided = False
        for index, existing in enumerate(self.transmissions):
            if _intervals_overlap(
                existing.start_time_ms, existing.end_time_ms, start_ms, end_ms
            ):
                collided = True
                if existing.success:
                    self.transmissions[index] = replace(existing, success=False)

        record = TransmissionRecord(
            trans_id=trans_id,
            protocol=protocol,
            start_time_ms=start_ms,
            end_time_ms=end_ms,
            success=not collided,
        )
        self._index_by_id[trans_id] = len(self.transmissions)
        self.transmissions.append(record)
        return record

    def record(self, trans_id: str) -> TransmissionRecord:
        """Return the current record for ``trans_id``, including later flips.

        Raises:
            KeyError: if ``trans_id`` was never registered.
        """
        return self.transmissions[self._index_by_id[trans_id]]

    def successful_transmissions(self) -> list[TransmissionRecord]:
        """Collision-free transmissions registered so far, start-time order."""
        return sorted(
            (r for r in self.transmissions if r.success),
            key=lambda r: r.start_time_ms,
        )

    def failed_transmissions(self) -> list[TransmissionRecord]:
        """Corrupted (colliding) transmissions registered so far, start-time order."""
        return sorted(
            (r for r in self.transmissions if not r.success),
            key=lambda r: r.start_time_ms,
        )

    def reset(self) -> None:
        """Clear all registered transmissions, for reuse across scenarios."""
        self.transmissions.clear()
        self._index_by_id.clear()
