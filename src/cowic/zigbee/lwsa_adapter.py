"""Lightweight Window-Scaling Algorithm (L-WSA) for superframe adaptation.

Implements Section 4.4 / Eq. 3-10 of the base paper: the ZigBee Coordinator
(ZC) selects a superframe timeslot duration ``t_SfSlot``, CAP slot count
``i``, CFP slot count ``j``, and beacon offset ``t_offset`` that maximize
total transmission efficiency within a granted interference-free window
``[dur_start_ms, dur_end_ms]``, navigating fragmented residual WiFi activity
``T^WiFi`` inside that window.

The superframe lays out ``1 + i + j`` equal-width ``t_SfSlot`` grid slots
starting at ``t_offset``: one beacon slot, then ``i`` CAP slots, then ``j``
CFP slots (Eq. 10). A CAP slot is usable if its first ``t_min`` (the minimum
CAP transmission opportunities are counted at ``t_min`` stride across the
CAP region; each opportunity must be clear of WiFi. A CFP slot (a fixed GTS
allocation) must be clear for its *entire* ``t_SfSlot`` width (Eq. 9).

Although the joint search space is a Mixed-Integer Nonlinear Program, it is
tightly bounded by IEEE 802.15.4's standard-defined parameter space
(``t_SfSlot`` from a discrete doubling sequence, ``0 <= j <= 7``,
``i + j <= 15``) and a handful of beacon-offset candidates, so it reduces to
a low-dimensional discrete search solvable by enumeration (Section 4.4).

``SuperframeConfig.cap_slots``/``cfp_slots`` report one *contiguous*
allocation, matching IEEE 802.15.4's actual superframe structure (there is
no mechanism to structurally "skip" an individual slot mid-sequence while
keeping later slots active). ``_best_slot_split`` does shrink the
allocation's *tail* when trailing slots are known in advance to overlap
WiFi (no reason to reserve dead airtime there), but a WiFi fragment landing
inside the middle of an otherwise-useful run still keeps that slot
reserved: at runtime, the ZigBee node's own CSMA/CA backoff (for CAP) or
a missed GTS opportunity (for CFP) absorbs the collision, exactly as a
real IEEE 802.15.4 node would behave. This mirrors the base paper's own
``m``/``n`` *counting* model (Eq. 9) rather than a per-slot exclusion list.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from cowic.models.superframe import SuperframeConfig

# IEEE 802.15.4-2020 timing constants for the 2.4 GHz O-QPSK PHY (250 kbps,
# 4 bits/symbol => 62500 symbols/s => 16 us/symbol).
SYMBOL_DURATION_MS = 0.016
BASE_SLOT_DURATION_MS = SYMBOL_DURATION_MS * 60  # aBaseSlotDuration: 60 symbols
BACKOFF_SLOT_DURATION_MS = SYMBOL_DURATION_MS * 20  # aUnitBackoffPeriod: 20 symbols
TURNAROUND_TIME_MS = SYMBOL_DURATION_MS * 12  # aTurnaroundTime: 12 symbols
ACK_DURATION_MS = SYMBOL_DURATION_MS * 22  # ACK frame: 22 symbols
MAX_PACKET_DURATION_MS = SYMBOL_DURATION_MS * 2 * (127 + 6)  # PSDU + SHR + PHR
PHY_HEADER_DURATION_MS = SYMBOL_DURATION_MS * 2 * 6  # SHR (5 B) + PHR (1 B)

MAX_CFP_SLOTS = 7  # 0 <= j <= 7 (Eq. 10)
MAX_ACTIVE_SLOTS = 15  # i + j <= 15 (Eq. 10)
MAX_SUPERFRAME_ORDER = 14  # IEEE 802.15.4 permits SO in [0, 14].


@dataclass(frozen=True)
class _Candidate:
    """One evaluated (t_SfSlot, i, j, t_offset) point in the search space."""

    slot_duration_ms: float
    cap_slots: int
    cfp_slots: int
    beacon_offset_ms: float
    efficiency: float


def _packet_duration_ms(
    slot_duration_ms: float, desired_packet_duration_ms: float
) -> float:
    """``t_pk`` (Eq. 8): desired packet time, capped by hardware and slot.

    The application's intended payload time is first bounded by the
    hardware-defined threshold (``MAX_PACKET_DURATION_MS``, from
    ``aMaxPHYPacketSize``), then by the chosen slot's own capacity.
    """
    hardware_capped_ms = min(desired_packet_duration_ms, MAX_PACKET_DURATION_MS)
    capped = min(
        slot_duration_ms,
        hardware_capped_ms + TURNAROUND_TIME_MS + ACK_DURATION_MS,
    )
    return capped - TURNAROUND_TIME_MS - ACK_DURATION_MS


def _min_cap_interval_ms(packet_duration_ms: float) -> float:
    """``t_min`` (Eq. 8): minimum CAP transmission interval."""
    return 2.0 * BACKOFF_SLOT_DURATION_MS + packet_duration_ms


def _per_packet_efficiency(packet_duration_ms: float, interval_ms: float) -> float:
    """Eq. 7 efficiency; subtract PHY headers, not Eq. 8's removed TA/ACK."""
    return max(0.0, packet_duration_ms - PHY_HEADER_DURATION_MS) / interval_ms


def _count_clear_slots(
    slot_count: int,
    first_slot_start_ms: float,
    slot_grid_width_ms: float,
    occupancy_width_ms: float,
    residual_wifi_fragments: Sequence[tuple[float, float]],
) -> int:
    """Count grid slots whose first ``occupancy_width_ms`` is WiFi-clear.

    Mirrors Eq. 9's indicator sum: slot ``k`` occupies
    ``[first_slot_start_ms + k*slot_grid_width_ms, ... + occupancy_width_ms)``
    and counts only if that interval overlaps no fragment in
    ``residual_wifi_fragments``.
    """
    clear = 0
    for k in range(slot_count):
        slot_start = first_slot_start_ms + k * slot_grid_width_ms
        slot_end = slot_start + occupancy_width_ms
        if not any(
            slot_start < frag_end and frag_start < slot_end
            for frag_start, frag_end in residual_wifi_fragments
        ):
            clear += 1
    return clear


def _count_cap_transmissions(
    cap_duration_ms: float,
    min_interval_ms: float,
    residual_wifi_fragments: Sequence[tuple[float, float]] = (),
    cap_start_ms: float = 0.0,
) -> int:
    """Count Eq. 9 CAP attempts at ``t_min`` stride across the CAP region."""
    attempts = math.floor(cap_duration_ms / min_interval_ms + 1e-12)
    return _count_clear_slots(
        attempts,
        cap_start_ms,
        min_interval_ms,
        min_interval_ms,
        residual_wifi_fragments,
    )


class LWSAAdapter:
    """Executes the discrete search over superframe configurations."""

    def __init__(
        self,
        alpha: float = 1.2,
        desired_packet_duration_ms: float = 2.0,
    ) -> None:
        """
        Args:
            alpha: Weight factor prioritizing CFP (contention-free, Eq. 6)
                efficiency over CAP efficiency.
            desired_packet_duration_ms: The ZC's intended per-packet
                transmission time before any slot-width capping (Eq. 8);
                defaults to a short ZigBee data frame.
        """
        self.alpha = alpha
        self.desired_packet_duration_ms = desired_packet_duration_ms

    def adapt_superframe(
        self,
        dur_start_ms: float,
        dur_end_ms: float,
        residual_wifi_fragments: list[tuple[float, float]],
    ) -> SuperframeConfig:
        """Search and select the optimal superframe configuration.

        Args:
            dur_start_ms: Start of the AP-granted interference-free window.
            dur_end_ms: End of the AP-granted interference-free window.
            residual_wifi_fragments: Sensed ``(start_ms, end_ms)`` intervals
                of unavoidable WiFi activity inside the window (Section 4.4).

        Returns:
            The feasible candidate maximizing total transmission efficiency
            (Eq. 6). If no non-trivial candidate is feasible (the window is
            too short for even a beacon slot), returns a zero-activity
            configuration anchored at ``dur_start_ms``.

        Raises:
            ValueError: if ``dur_end_ms <= dur_start_ms``.
        """
        if dur_end_ms <= dur_start_ms:
            raise ValueError(
                f"dur_end_ms ({dur_end_ms}) must be greater than "
                f"dur_start_ms ({dur_start_ms})"
            )
        window_ms = dur_end_ms - dur_start_ms

        best: _Candidate | None = None
        for slot_duration_ms in self._slot_duration_candidates(window_ms):
            packet_duration_ms = _packet_duration_ms(
                slot_duration_ms, self.desired_packet_duration_ms
            )
            if packet_duration_ms <= 0:
                continue
            min_cap_interval_ms = _min_cap_interval_ms(packet_duration_ms)
            eta_cap = _per_packet_efficiency(packet_duration_ms, min_cap_interval_ms)
            eta_cfp = _per_packet_efficiency(packet_duration_ms, slot_duration_ms)

            for beacon_offset_ms in self._offset_candidates(
                dur_start_ms, dur_end_ms, residual_wifi_fragments
            ):
                remaining_ms = dur_end_ms - beacon_offset_ms
                max_active = int(remaining_ms / slot_duration_ms) - 1
                if max_active < 0:
                    continue
                max_active = min(max_active, MAX_ACTIVE_SLOTS)

                candidate = self._best_slot_split(
                    slot_duration_ms,
                    beacon_offset_ms,
                    max_active,
                    min_cap_interval_ms,
                    eta_cap,
                    eta_cfp,
                    residual_wifi_fragments,
                )
                candidate_span = (
                    (1 + candidate.cap_slots + candidate.cfp_slots)
                    * candidate.slot_duration_ms
                    if candidate is not None
                    else 0.0
                )
                best_span = (
                    (1 + best.cap_slots + best.cfp_slots) * best.slot_duration_ms
                    if best is not None
                    else 0.0
                )
                if candidate is not None and (
                    best is None
                    or candidate.efficiency > best.efficiency
                    or (
                        candidate.efficiency == best.efficiency
                        and candidate_span > best_span
                    )
                ):
                    best = candidate

        if best is None or best.efficiency <= 0.0:
            return SuperframeConfig(
                slot_duration_ms=self._slot_duration_candidates(window_ms)[0],
                cap_slots=0,
                cfp_slots=0,
                beacon_offset_ms=dur_start_ms,
                beacon_interval_ms=window_ms,
                beacon2_offset_ms=None,
            )

        return SuperframeConfig(
            slot_duration_ms=best.slot_duration_ms,
            cap_slots=best.cap_slots,
            cfp_slots=best.cfp_slots,
            beacon_offset_ms=best.beacon_offset_ms,
            beacon_interval_ms=window_ms,
            beacon2_offset_ms=best.beacon_offset_ms + best.slot_duration_ms,
        )

    def _best_slot_split(
        self,
        slot_duration_ms: float,
        beacon_offset_ms: float,
        max_active: int,
        min_cap_interval_ms: float,
        eta_cap: float,
        eta_cfp: float,
        residual_wifi_fragments: Sequence[tuple[float, float]],
    ) -> _Candidate | None:
        """Best feasible ``(i, j)`` split for one ``(t_SfSlot, t_offset)`` pair."""
        if (
            _count_clear_slots(
                1,
                beacon_offset_ms,
                slot_duration_ms,
                slot_duration_ms,
                residual_wifi_fragments,
            )
            == 0
        ):
            return None
        cap_region_start_ms = beacon_offset_ms + slot_duration_ms
        best: _Candidate | None = None
        for j in range(0, min(MAX_CFP_SLOTS, max_active) + 1):
            max_i = min(max_active - j, MAX_ACTIVE_SLOTS - j)
            for i in range(0, max_i + 1):
                m = _count_cap_transmissions(
                    i * slot_duration_ms,
                    min_cap_interval_ms,
                    residual_wifi_fragments,
                    cap_region_start_ms,
                )
                cfp_region_start_ms = cap_region_start_ms + i * slot_duration_ms
                n = _count_clear_slots(
                    j,
                    cfp_region_start_ms,
                    slot_duration_ms,
                    slot_duration_ms,
                    residual_wifi_fragments,
                )
                efficiency = (
                    m * min_cap_interval_ms * eta_cap
                    + self.alpha * n * slot_duration_ms * eta_cfp
                )
                is_better = (
                    best is None
                    or efficiency > best.efficiency
                    or (
                        efficiency == best.efficiency
                        and i + j < best.cap_slots + best.cfp_slots
                    )
                )
                if is_better:
                    best = _Candidate(
                        slot_duration_ms=slot_duration_ms,
                        cap_slots=i,
                        cfp_slots=j,
                        beacon_offset_ms=beacon_offset_ms,
                        efficiency=efficiency,
                    )
        return best

    @staticmethod
    def _slot_duration_candidates(window_ms: float) -> list[float]:
        """``t_SfSlot`` candidates: ``aBaseSlotDuration * 2^SO`` (Eq. 10)."""
        candidates: list[float] = []
        for order in range(MAX_SUPERFRAME_ORDER + 1):
            duration = BASE_SLOT_DURATION_MS * (2**order)
            if duration > window_ms and candidates:
                break
            candidates.append(duration)
        return candidates or [BASE_SLOT_DURATION_MS]

    @staticmethod
    def _offset_candidates(
        dur_start_ms: float,
        dur_end_ms: float,
        residual_wifi_fragments: Sequence[tuple[float, float]],
    ) -> list[float]:
        """Beacon offset candidates: the window start, and the end of each
        WiFi fragment overlapping the window and finishing strictly
        inside it - the only points where shifting the beacon later can
        avoid early interference (Section 4.4: "the beacon offset is
        first adjusted to avoid early interference")."""
        candidates = [dur_start_ms]
        for frag_start, frag_end in sorted(residual_wifi_fragments):
            if frag_start < dur_end_ms and dur_start_ms < frag_end < dur_end_ms:
                candidates.append(frag_end)
        return sorted(set(candidates))
