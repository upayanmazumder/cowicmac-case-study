"""ZigBee superframe configuration data structures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SuperframeConfig:
    """Configuration parameters for an IEEE 802.15.4 superframe."""

    slot_duration_ms: float
    cap_slots: int
    cfp_slots: int
    beacon_offset_ms: float
    beacon_interval_ms: float
    beacon2_offset_ms: float | None = None


@dataclass(frozen=True)
class ZigBeePacket:
    """Represents an individual ZigBee transmission."""

    packet_id: int
    node_id: int
    start_time_ms: float
    duration_ms: float
    payload_bytes: int
    is_cfp: bool
    is_beacon: bool = False
