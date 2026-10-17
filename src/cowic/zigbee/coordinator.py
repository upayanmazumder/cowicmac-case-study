"""ZigBee Coordinator state machine and demand manager.

Implements the ZC side of the four-step connection-establishment handshake
(Section 4.2) and the coordinated FSM (Section 4.5.1, Fig. 7b): the ZC
monitors for a WBeacon, reshapes its superframe via :class:`LWSAAdapter`
once one arrives, and reports its own traffic demand back to the AP.
"""

import math
from enum import Enum, auto

from cowic.ctc.channel import CTCMessage, MessageType
from cowic.exceptions import ConfigurationError, ProtocolError
from cowic.models.superframe import SuperframeConfig
from cowic.wifi.ap_orchestrator import WBEACON_KEYS, ZBEACON_DEMAND_KEY
from cowic.zigbee.lwsa_adapter import (
    BACKOFF_SLOT_DURATION_MS,
    LWSAAdapter,
)


class ZCState(Enum):
    """Operational states of the ZigBee Coordinator."""

    IDLE = auto()
    CTC_REPORTING = auto()
    COMMAND_ACQUISITION = auto()
    TRAFFIC_ADJUSTMENT = auto()
    SUPERFRAME_ACTIVE = auto()


class ZigBeeCoordinator:
    """Coordinates ZigBee nodes and interfaces with the WiFi AP via CTC.

    ``lwsa`` is injected so a test double or a differently-weighted
    :class:`LWSAAdapter` can be substituted without changing this class.
    """

    def __init__(
        self,
        zc_id: str = "ZC_0",
        lwsa: LWSAAdapter | None = None,
        bytes_per_packet: int = 32,
    ) -> None:
        self.zc_id = zc_id
        self.lwsa = lwsa or LWSAAdapter()
        self.bytes_per_packet = bytes_per_packet
        self.state = ZCState.IDLE
        self.superframe: SuperframeConfig | None = None

    def generate_demand_report(
        self, pending_packets: int, demand_limit_ms: float | None = None
    ) -> float:
        """Calculate requested airtime duration based on pending queue.

        Each packet's requested airtime includes two backoff slots and the
        configured payload duration. ``demand_limit_ms`` caps a synthetic
        queue sized to fill one experiment's configured free window.

        Raises:
            ConfigurationError: if ``pending_packets`` is negative.
        """
        if pending_packets < 0:
            raise ConfigurationError(
                f"pending_packets must be non-negative, got {pending_packets}"
            )
        if demand_limit_ms is not None and (
            not math.isfinite(demand_limit_ms) or demand_limit_ms < 0
        ):
            raise ConfigurationError(
                "demand_limit_ms must be finite and non-negative, "
                f"got {demand_limit_ms}"
            )
        self.state = ZCState.CTC_REPORTING
        packet_interval_ms = (
            2 * BACKOFF_SLOT_DURATION_MS + self.lwsa.desired_packet_duration_ms
        )
        demand_ms = pending_packets * packet_interval_ms
        if demand_limit_ms is not None:
            demand_ms = min(demand_ms, demand_limit_ms)
        self.state = ZCState.IDLE
        return demand_ms

    def build_zbeacon(
        self,
        pending_packets: int,
        timestamp_ms: float,
        demand_limit_ms: float | None = None,
    ) -> CTCMessage:
        """Construct the ZBeacon reporting this cycle's demand (Section 4.2)."""
        demand_ms = self.generate_demand_report(pending_packets, demand_limit_ms)
        return CTCMessage(
            msg_type=MessageType.ZBEACON,
            sender_id=self.zc_id,
            timestamp_ms=timestamp_ms,
            payload={ZBEACON_DEMAND_KEY: demand_ms},
        )

    def process_wbeacon(
        self,
        wbeacon: CTCMessage,
        residual_wifi_fragments: list[tuple[float, float]],
    ) -> SuperframeConfig:
        """Reshape the superframe to fit the AP-granted window (Fig. 7b).

        Transitions ``COMMAND_ACQUISITION -> TRAFFIC_ADJUSTMENT ->
        SUPERFRAME_ACTIVE``, invoking :meth:`LWSAAdapter.adapt_superframe`
        with the AP-granted ``[t_free_start_ms, t_free_end_ms]`` window and
        the ZC's own sensed ``residual_wifi_fragments`` (Section 4.4: the
        ZC senses the window directly rather than receiving fragments over
        the bandwidth-limited CTC link).

        A zero-length window (``t_free_end_ms <= t_free_start_ms``) is a
        legitimate "no airtime granted this cycle" outcome (e.g. the AP's
        schedule was infeasible, or carryover consumed the whole cycle):
        it yields a zero-activity superframe rather than an error.

        Raises:
            ProtocolError: if ``wbeacon`` is not a well-formed WBeacon.
        """
        window_start_ms, window_end_ms = _read_window(wbeacon)

        self.state = ZCState.COMMAND_ACQUISITION
        self.state = ZCState.TRAFFIC_ADJUSTMENT
        if window_end_ms <= window_start_ms:
            self.superframe = SuperframeConfig(
                slot_duration_ms=0.0,
                cap_slots=0,
                cfp_slots=0,
                beacon_offset_ms=window_start_ms,
                beacon_interval_ms=0.0,
            )
        else:
            self.superframe = self.lwsa.adapt_superframe(
                window_start_ms, window_end_ms, residual_wifi_fragments
            )

        self.state = ZCState.SUPERFRAME_ACTIVE
        return self.superframe

    def on_wbeacon_lost(self) -> None:
        """Return to command acquisition after a missed WBeacon timeout."""
        self.superframe = None
        self.state = ZCState.COMMAND_ACQUISITION


def _read_window(wbeacon: CTCMessage) -> tuple[float, float]:
    if wbeacon.msg_type is not MessageType.WBEACON:
        raise ProtocolError(f"Expected a WBeacon, got {wbeacon.msg_type.name}")
    missing = [key for key in WBEACON_KEYS if key not in wbeacon.payload]
    if missing:
        raise ProtocolError(f"WBeacon payload missing keys: {missing}")
    start_ms = wbeacon.payload["t_free_start_ms"]
    end_ms = wbeacon.payload["t_free_end_ms"]
    try:
        is_finite = math.isfinite(start_ms) and math.isfinite(end_ms)
        precedes_start = end_ms < start_ms
    except TypeError as exc:
        raise ProtocolError(
            f"WBeacon window boundaries must be numeric, "
            f"got start={start_ms!r}, end={end_ms!r}"
        ) from exc
    if not is_finite:
        raise ProtocolError(
            f"WBeacon window must be finite, got start={start_ms}, end={end_ms}"
        )
    if precedes_start:
        raise ProtocolError(
            f"WBeacon free window end ({end_ms}) cannot precede start ({start_ms})"
        )
    return start_ms, end_ms
