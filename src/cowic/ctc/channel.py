"""Cross-Technology Communication (CTC) signaling channel.

Models the bidirectional out-of-band control link between the WiFi AP and
the ZigBee Coordinator: W2Z (WiCast, AP -> ZC WBeacon) and Z2W (SymBee, ZC
-> AP ZBeacon) per Section 4.2. Delivery of one attempt is an independent
Bernoulli trial, parameterized by a packet error rate (``per``).
``delay_ms`` is a fixed one-way transit time per attempt, exposed for
callers (e.g. a SimPy process) to apply as their own ``env.timeout`` -
this class does not advance simulated time itself, since ``transmit`` is
a synchronous, non-generator call.

``max_retries`` models the handshake's timeout-and-retry behavior
(Section 4.2): on a dropped attempt, the sender retries up to
``max_retries`` additional times before giving up. If every attempt is
lost, :meth:`transmit` returns ``False`` and the caller (the AP or the
ZC) is expected to fall back to uncoordinated CSMA/CA for that cycle
rather than block waiting for coordination that never arrives.
"""

import math
import random
from dataclasses import dataclass
from enum import Enum, auto

from cowic.exceptions import ConfigurationError


class MessageType(Enum):
    """Types of cross-technology coordination frames."""

    WBEACON = auto()
    ZBEACON = auto()


@dataclass(frozen=True)
class CTCMessage:
    """Encapsulates control data sent across heterogeneous radios."""

    msg_type: MessageType
    sender_id: str
    timestamp_ms: float
    payload: dict[str, float]


class CTCChannel:
    """Simulates bidirectional W2Z (WiCast) and Z2W (SymBee) signaling.

    Each individual attempt is an independent Bernoulli trial: it
    succeeds with probability ``1 - per``. ``rng`` is injectable for
    reproducible, seeded simulation runs; it defaults to a fresh,
    unseeded :class:`random.Random`.
    """

    def __init__(
        self,
        per: float = 0.0,
        delay_ms: float = 0.1,
        max_retries: int = 2,
        rng: random.Random | None = None,
    ) -> None:
        if not math.isfinite(per) or not (0.0 <= per <= 1.0):
            raise ConfigurationError(f"per must be within [0, 1], got {per}")
        if not math.isfinite(delay_ms) or delay_ms < 0:
            raise ConfigurationError(
                f"delay_ms must be finite and non-negative, got {delay_ms}"
            )
        if max_retries < 0:
            raise ConfigurationError(
                f"max_retries must be non-negative, got {max_retries}"
            )
        self.per = per
        self.delay_ms = delay_ms
        self.max_retries = max_retries
        self._rng = rng if rng is not None else random.Random()
        self.sent_count = 0
        self.dropped_count = 0
        self.retry_count = 0
        self.last_attempts = 0

    def transmit(self, message: CTCMessage) -> bool:
        """Deliver a CTC signaling frame, retrying on loss up to
        ``max_retries`` times before timing out (Section 4.2 handshake
        timeout). ``sent_count`` counts messages: one per call, however
        many attempts it takes (``retry_count`` counts the extra attempts).
        Only a fully exhausted handshake counts against ``dropped_count``.
        ``last_attempts`` records how many attempts this call used, so a
        caller advancing simulated time (e.g. a SimPy process) can apply
        ``last_attempts * delay_ms`` rather than a flat one-attempt delay.

        Returns:
            ``True`` if any attempt was delivered, ``False`` if every
            attempt (including retries) was lost - the caller should
            fall back to uncoordinated CSMA/CA for this cycle.
        """
        self.sent_count += 1
        for attempt in range(self.max_retries + 1):
            if self._rng.random() >= self.per:
                self.retry_count += attempt
                self.last_attempts = attempt + 1
                return True
        self.retry_count += self.max_retries
        self.last_attempts = self.max_retries + 1
        self.dropped_count += 1
        return False

    def reset(self) -> None:
        """Clear delivery counters, for reuse across scenarios."""
        self.sent_count = 0
        self.dropped_count = 0
        self.retry_count = 0
        self.last_attempts = 0
