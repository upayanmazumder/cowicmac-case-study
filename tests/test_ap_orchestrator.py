"""Tests for the WiFi AP orchestrator state machine."""

import math

import pytest

from cowic.ctc.channel import CTCMessage, MessageType
from cowic.exceptions import ConfigurationError, ProtocolError, TraceError
from cowic.models.frame_group import FrameGroup, SchedulingDecision
from cowic.wifi.ap_orchestrator import (
    CARRYOVER_BUSY_STATUS,
    WBEACON_KEYS,
    ZBEACON_DEMAND_KEY,
    APOrchestrator,
    APState,
)
from cowic.wifi.fallback import FALLBACK_STATUS, VACATE_STATUS
from cowic.wifi.milp_scheduler import ScheduleResult
from traces.playback import frame_groups_from_frame, iter_cycles
from traces.synthetic_generator import build_trace, load_profile

FGS = [
    FrameGroup(
        fg_id=1,
        arrival_time_ms=10.0,
        duration_ms=10.0,
        deadline_ms=120.0,
        original_start_ms=10.0,
        qos_sensitive=False,
    )
]


class FakeCTC:
    def __init__(self, delivers: bool = True) -> None:
        self.delivers = delivers
        self.sent: list[CTCMessage] = []

    def transmit(self, message: CTCMessage) -> bool:
        self.sent.append(message)
        return self.delivers


class FakeScheduler:
    def __init__(self, status: str = "Optimal", error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[tuple[float, float, float, float]] = []

    def schedule(
        self,
        frame_groups: list[FrameGroup],
        d_free_ms: float,
        d_sensing_ms: float,
        t_start_ms: float,
        t_end_ms: float,
    ) -> ScheduleResult:
        self.calls.append((d_free_ms, d_sensing_ms, t_start_ms, t_end_ms))
        if self.error is not None:
            raise self.error
        return ScheduleResult(
            decisions=[
                SchedulingDecision(fg.fg_id, fg.original_start_ms + 1.0, False, 1.0)
                for fg in frame_groups
            ],
            t_free_start_ms=50.0,
            t_free_end_ms=50.0 + d_free_ms,
            t_sense_start_ms=40.0,
            t_sense_end_ms=40.0 + d_sensing_ms,
            solver_status=self.status,
            solve_time_ms=1.0,
        )


def _zbeacon(demand: float = 30.0) -> CTCMessage:
    return CTCMessage(MessageType.ZBEACON, "ZC_0", 0.0, {ZBEACON_DEMAND_KEY: demand})


def test_cycle_walks_every_state_in_order() -> None:
    ap = APOrchestrator(FakeCTC(), FakeScheduler())
    ap.process_cycle(FGS, 0.0, 100.0, _zbeacon())
    assert ap.history == [
        APState.MAINTAINING,
        APState.CTC_GATHERING,
        APState.RESCHEDULING,
        APState.CTC_NOTIFYING,
        APState.TRANSMITTING,
        APState.MAINTAINING,
    ]
    assert ap.state is APState.MAINTAINING


def test_consecutive_cycles() -> None:
    ap = APOrchestrator(FakeCTC(), FakeScheduler())
    for cycle in range(3):
        ap.process_cycle(FGS, cycle * 100.0, (cycle + 1) * 100.0, _zbeacon())
    assert len(ap.history) == 1 + 3 * 5


def test_optimal_schedule_is_used() -> None:
    scheduler = FakeScheduler()
    ap = APOrchestrator(FakeCTC(), scheduler, d_sensing_ms=10.0)
    outcome = ap.process_cycle(FGS, 0.0, 100.0, _zbeacon(30.0))

    assert scheduler.calls == [(30.0, 10.0, 0.0, 100.0)]
    assert not outcome.used_fallback
    assert outcome.schedule.solver_status == "Optimal"
    assert outcome.d_free_ms == 30.0


@pytest.mark.parametrize(
    "scheduler",
    [
        FakeScheduler(status="Infeasible"),
        FakeScheduler(error=TraceError("solver failed")),
        None,
    ],
)
def test_falls_back_to_whitespace(scheduler: FakeScheduler | None) -> None:
    ap = APOrchestrator(FakeCTC(), scheduler)
    outcome = ap.process_cycle(FGS, 0.0, 100.0, _zbeacon(30.0))
    assert outcome.used_fallback
    assert outcome.schedule.solver_status == FALLBACK_STATUS
    # Gap [20, 100] holds 30 ms, so the window is [20, 50].
    assert (outcome.schedule.t_free_start_ms, outcome.schedule.t_free_end_ms) == (
        20.0,
        50.0,
    )


def test_unexpected_scheduler_error_propagates() -> None:
    ap = APOrchestrator(FakeCTC(), FakeScheduler(error=RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        ap.process_cycle(FGS, 0.0, 100.0, _zbeacon())
    # A genuine bug still surfaces, but must not strand the AP's state.
    assert ap.state is APState.MAINTAINING


def test_ctc_transmit_failure_still_restores_maintaining() -> None:
    class RaisingCTC:
        def transmit(self, message: CTCMessage) -> bool:
            raise RuntimeError("link down")

    ap = APOrchestrator(RaisingCTC(), FakeScheduler())
    with pytest.raises(RuntimeError):
        ap.process_cycle(FGS, 0.0, 100.0, _zbeacon())
    assert ap.state is APState.MAINTAINING


def test_lost_wbeacon_restores_original_wifi_schedule() -> None:
    ap = APOrchestrator(FakeCTC(delivers=False), FakeScheduler())
    outcome = ap.process_cycle(FGS, 0.0, 100.0, _zbeacon())
    assert outcome.wbeacon is not None
    assert outcome.schedule.decisions == [
        SchedulingDecision(
            fg_id=1, scheduled_start_ms=10.0, is_postponed=False, delay_ms=0.0
        )
    ]


def test_lost_wbeacon_restore_does_not_overlap_carryover() -> None:
    """Carryover deferred by an earlier coordinated cycle is still on air
    until 30 ms. Restoring the original timing must not stack FG 1 (10 ms)
    or FG 2 (35 ms) on top of it or on top of each other."""
    carried = FrameGroup(9, 0.0, 30.0, 500.0, 0.0, False)
    fgs = [
        FrameGroup(1, 10.0, 10.0, 500.0, 10.0, False),
        FrameGroup(2, 35.0, 10.0, 500.0, 35.0, False),
    ]
    ap = APOrchestrator(FakeCTC(delivers=False), FakeScheduler())
    outcome = ap.process_cycle(fgs, 0.0, 100.0, _zbeacon(), [carried])
    starts = {d.fg_id: d.scheduled_start_ms for d in outcome.schedule.decisions}
    assert starts == {1: 30.0, 2: 40.0}
    assert all(not d.is_postponed for d in outcome.schedule.decisions)


def test_uncoordinated_cycle_skips_ctc_states_and_wbeacon() -> None:
    ctc = FakeCTC()
    ap = APOrchestrator(ctc, FakeScheduler())
    outcome = ap.process_cycle(FGS, 0.0, 100.0, None, coordination_requested=False)
    assert outcome.wbeacon is None
    assert ctc.sent == []
    assert ap.history == [
        APState.MAINTAINING,
        APState.RESCHEDULING,
        APState.TRANSMITTING,
        APState.MAINTAINING,
    ]


def test_wbeacon_announces_reserved_windows() -> None:
    ctc = FakeCTC(delivers=False)
    ap = APOrchestrator(ctc, FakeScheduler(), d_sensing_ms=10.0, ap_id="AP_7")
    outcome = ap.process_cycle(FGS, 200.0, 300.0, _zbeacon(30.0))

    assert outcome.wbeacon is not None
    assert ctc.sent == [outcome.wbeacon]
    assert not outcome.wbeacon_delivered
    wbeacon = outcome.wbeacon
    assert wbeacon.msg_type is MessageType.WBEACON
    assert wbeacon.sender_id == "AP_7"
    assert wbeacon.timestamp_ms == 200.0
    assert tuple(wbeacon.payload) == WBEACON_KEYS
    assert wbeacon.payload == {
        "t_free_start_ms": 50.0,
        "t_free_end_ms": 80.0,
        "t_sense_start_ms": 40.0,
        "t_sense_end_ms": 50.0,
    }


def test_lost_zbeacon_resets_demand_to_default() -> None:
    scheduler = FakeScheduler()
    ap = APOrchestrator(FakeCTC(), scheduler)
    ap.process_cycle(FGS, 0.0, 100.0, None)
    ap.process_cycle(FGS, 100.0, 200.0, _zbeacon(40.0))
    ap.process_cycle(FGS, 200.0, 300.0, None)
    assert [call[0] for call in scheduler.calls] == [0.0, 40.0, 0.0]


@pytest.mark.parametrize(
    "message",
    [
        CTCMessage(MessageType.WBEACON, "AP_0", 0.0, {ZBEACON_DEMAND_KEY: 1.0}),
        CTCMessage(MessageType.ZBEACON, "ZC_0", 0.0, {}),
        _zbeacon(-1.0),
        _zbeacon(math.nan),
    ],
)
def test_invalid_zbeacon_rejected(message: CTCMessage) -> None:
    ap = APOrchestrator(FakeCTC(), FakeScheduler())
    with pytest.raises(ProtocolError):
        ap.process_cycle(FGS, 0.0, 100.0, message)
    # A malformed ZBeacon must not strand the AP: it should recover to
    # MAINTAINING and accept the next cycle normally.
    assert ap.state is APState.MAINTAINING
    outcome = ap.process_cycle(FGS, 0.0, 100.0, _zbeacon())
    assert ap.state is APState.MAINTAINING
    assert outcome.schedule is not None


def test_cycle_must_start_in_maintaining() -> None:
    ap = APOrchestrator(FakeCTC(), FakeScheduler())
    ap.state = APState.RESCHEDULING
    with pytest.raises(ProtocolError):
        ap.process_cycle(FGS, 0.0, 100.0, _zbeacon())


def test_illegal_transition_rejected() -> None:
    ap = APOrchestrator(FakeCTC(), FakeScheduler())
    with pytest.raises(ProtocolError):
        ap._advance(APState.TRANSMITTING)


def test_invalid_configuration() -> None:
    with pytest.raises(ConfigurationError):
        APOrchestrator(FakeCTC(), d_sensing_ms=-1.0)
    with pytest.raises(ConfigurationError):
        APOrchestrator(FakeCTC(), d_sensing_ms=math.nan)
    with pytest.raises(ConfigurationError):
        APOrchestrator(FakeCTC(), d_sensing_ms=math.inf)
    ap = APOrchestrator(FakeCTC())
    with pytest.raises(ConfigurationError):
        ap.process_cycle(FGS, 100.0, 100.0, None)
    assert ap.state is APState.MAINTAINING


def _fg(fg_id: int, start: float, duration: float) -> FrameGroup:
    return FrameGroup(fg_id, start, duration, start + duration + 100.0, start, False)


def test_fallback_window_starts_after_carryover() -> None:
    # FG 9 started in the previous cycle and is on air until 110.
    ap = APOrchestrator(FakeCTC())
    outcome = ap.process_cycle(
        [], 100.0, 200.0, _zbeacon(30.0), carryover=[_fg(9, 95.0, 15.0)]
    )
    assert outcome.used_fallback
    assert (outcome.schedule.t_free_start_ms, outcome.schedule.t_free_end_ms) == (
        110.0,
        140.0,
    )
    assert outcome.wbeacon is not None
    assert outcome.wbeacon.timestamp_ms == 100.0


def test_scheduler_horizon_starts_after_carryover() -> None:
    scheduler = FakeScheduler()
    ap = APOrchestrator(FakeCTC(), scheduler)
    carryover = [_fg(8, 80.0, 25.0), _fg(9, 95.0, 15.0)]
    ap.process_cycle([], 100.0, 200.0, _zbeacon(30.0), carryover=carryover)
    assert scheduler.calls == [(30.0, 0.0, 110.0, 200.0)]


def test_finished_carryover_does_not_shift_horizon() -> None:
    scheduler = FakeScheduler()
    ap = APOrchestrator(FakeCTC(), scheduler)
    ap.process_cycle([], 100.0, 200.0, _zbeacon(), carryover=[_fg(9, 50.0, 10.0)])
    assert scheduler.calls[0][2] == 100.0


def test_carryover_filling_cycle_grants_no_window() -> None:
    # FG 1 ([10, 20]) is entirely swallowed by carryover FG 9's span
    # ([-10, 140]): it cannot transmit this cycle and must be postponed,
    # not silently reported as transmitted unchanged.
    scheduler = FakeScheduler()
    ap = APOrchestrator(FakeCTC(), scheduler)
    outcome = ap.process_cycle(
        FGS, 0.0, 100.0, _zbeacon(30.0), carryover=[_fg(9, -10.0, 150.0)]
    )
    assert scheduler.calls == []
    assert not outcome.used_fallback
    assert outcome.schedule.solver_status == CARRYOVER_BUSY_STATUS
    assert outcome.schedule.t_free_start_ms == outcome.schedule.t_free_end_ms
    (decision,) = outcome.schedule.decisions
    assert decision.is_postponed is True
    assert decision.scheduled_start_ms == 100.0
    assert decision.delay_ms == 90.0
    assert ap.state is APState.MAINTAINING


def test_carryover_filling_cycle_leaves_non_overlapping_fg_untouched() -> None:
    # FG 1 ([10, 20]) does not overlap a carryover FG confined to [60, 160):
    # even though carryover fills the cycle to its end, FG 1's own slot was
    # never in conflict with it and its decision stays unchanged.
    scheduler = FakeScheduler()
    ap = APOrchestrator(FakeCTC(), scheduler)
    outcome = ap.process_cycle(
        FGS, 0.0, 100.0, _zbeacon(30.0), carryover=[_fg(9, 60.0, 100.0)]
    )
    assert outcome.schedule.solver_status == CARRYOVER_BUSY_STATUS
    (decision,) = outcome.schedule.decisions
    assert decision.is_postponed is False
    assert decision.scheduled_start_ms == 10.0
    assert decision.delay_ms == 0.0


@pytest.mark.parametrize("profile", ["Traffic_I", "Traffic_II"])
def test_windows_never_overlap_live_wifi_on_real_trace(profile: str) -> None:
    fgs = frame_groups_from_frame(build_trace(load_profile(profile), 20.0))
    ap = APOrchestrator(FakeCTC(), d_sensing_ms=10.0)
    for cycle in iter_cycles(fgs, cycle_ms=1000.0):
        outcome = ap.process_cycle(
            cycle.frame_groups,
            cycle.start_ms,
            cycle.end_ms,
            _zbeacon(62.0),
            carryover=cycle.active_carryover,
        )
        start = outcome.schedule.t_sense_start_ms
        end = outcome.schedule.t_free_end_ms
        if end <= start:
            continue
        for fg in cycle.active_carryover:
            assert fg.original_start_ms + fg.duration_ms <= start + 1e-9
        by_id = {fg.fg_id: fg for fg in cycle.frame_groups}
        for d in outcome.schedule.decisions:
            if d.is_postponed:
                continue
            fg_end = d.scheduled_start_ms + by_id[d.fg_id].duration_ms
            assert fg_end <= start + 1e-9 or d.scheduled_start_ms >= end - 1e-9


def test_rigid_scheduler_failure_falls_back_to_vacate() -> None:
    """Issue #38: when the Exp 5 MILP fails, the AP keeps the full window."""
    scheduler = FakeScheduler(status="Infeasible")
    scheduler.hard_free_window = True  # type: ignore[attr-defined]
    ap = APOrchestrator(FakeCTC(), scheduler)
    outcome = ap.process_cycle(FGS, 0.0, 100.0, _zbeacon(60.0))
    assert outcome.used_fallback
    assert outcome.schedule.solver_status == VACATE_STATUS
    width = outcome.schedule.t_free_end_ms - outcome.schedule.t_free_start_ms
    assert width == pytest.approx(60.0)
