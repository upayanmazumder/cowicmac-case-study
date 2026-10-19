"""MILP scheduler for WiFi Frame Groups (Eq. 11-17, Appendix A of the base paper).

The AP selects a new start time ``s'_k`` for each Frame Group (FG) and the
placement of the ZigBee interference-free window ``[T_free_start,
T_free_end]`` and the cross-technology sensing window ``[T_sense_start,
T_sense_end]`` inside the scheduling horizon ``[t_start_ms, t_end_ms]``. The
objective trades off four costs: WiFi airtime left inside the free window,
WiFi airtime inside the (strictly protected) sensing window, scheduling
deviation from the FG's original start time, and a deadline-aware penalty
for postponing a FG to the next cycle.

This module follows the *linearized* MILP from Appendix A (Big-M method),
not the non-linear indicator formulation in Section 4.3: Eq. 11
(objective), Eq. 12 (delay), Eq. 13 (window placement), Eq. 14
(causality/QoS/postponement), Eq. 15 (inter-FG conflict avoidance), Eq. 17
(sensing window protection, hard constraint). The overlap-avoidance
constraints (Eq. 16 in the appendix) are reformulated here with an
explicit disjunctive ordering binary per FG/window pair, rather than
transcribed literally: see the inline comment at the overlap constraints
below for the derivation and why the literal one-directional Big-M text
extracted from the source PDF is not usable as-is.
"""

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

import pulp

from cowic.exceptions import ConfigurationError
from cowic.models.frame_group import FrameGroup, SchedulingDecision

# Numerical floor for the postponement-cost denominator (Eq. 2), matching
# the paper's epsilon: prevents division by zero / a negative denominator
# for FGs whose deadline has already effectively passed by t_end_ms.
EPSILON_MS = 1e-3

OPTIMAL_STATUS = "Optimal"
# A solution the solver found but did not prove optimal, e.g. after a time
# limit. PuLP reports these as LpStatusOptimal and puts the real quality in
# sol_status, so the status is renamed here to keep it visible.
UNPROVEN_STATUS = "Integer Feasible"


@dataclass(frozen=True)
class ScheduleResult:
    """Result of the MILP scheduling optimization."""

    decisions: list[SchedulingDecision]
    t_free_start_ms: float
    t_free_end_ms: float
    t_sense_start_ms: float
    t_sense_end_ms: float
    solver_status: str
    solve_time_ms: float


def _big_m(
    frame_groups: Sequence[FrameGroup],
    horizon_ms: float,
    d_free_ms: float,
    d_sensing_ms: float,
    t_start_ms: float,
    t_end_ms: float,
) -> float:
    """A constraint-loosening constant larger than any feasible time gap.

    ``horizon_ms`` alone is not enough: Eq. 14's postponement-feasibility
    constraints compare each FG's absolute ``deadline_ms``/``arrival_time_ms``
    against ``t_start_ms``/``t_end_ms``. A single "stale" FG (e.g. a
    carryover whose deadline sits well before this cycle) can otherwise
    make its own relaxed-by-M constraint still bind, forcing infeasibility
    for the whole model rather than just that one FG.
    """
    max_duration = max((fg.duration_ms for fg in frame_groups), default=0.0)
    deadline_gap = max((t_end_ms - fg.deadline_ms for fg in frame_groups), default=0.0)
    arrival_gap = max(
        (fg.arrival_time_ms - t_start_ms for fg in frame_groups), default=0.0
    )
    # A postponed FG's deviation constraint relaxes by M, so M must also
    # exceed |s'_k - s_k| when the original start lies outside the cycle.
    origin_gap = max(
        (
            max(t_start_ms - fg.original_start_ms, fg.original_start_ms - t_end_ms)
            for fg in frame_groups
        ),
        default=0.0,
    )
    stale_extra = max(0.0, deadline_gap, arrival_gap, origin_gap)
    return horizon_ms + max_duration + max(d_free_ms, d_sensing_ms) + stale_extra + 1.0


def _drop_cost(
    frame_groups: Sequence[FrameGroup],
    big_m: float,
    gamma: float,
    lambda_weight: float,
    postponement_costs: Iterable[float],
) -> float:
    """Cost of dropping one FG: more than all other objective terms together.

    Each FG adds at most ``(1 + gamma) * d_k`` overlap cost, ``lambda * M``
    deviation cost and ``mu_k`` postponement cost. Pricing one drop above the
    sum makes the solver minimize the number of drops first.
    """
    overlap = sum((1.0 + gamma) * fg.duration_ms for fg in frame_groups)
    deviation = lambda_weight * big_m * len(frame_groups)
    return 1.0 + overlap + deviation + sum(postponement_costs)


def _postponement_cost(fg: FrameGroup, t_end_ms: float, mu_base: float) -> float:
    """Dynamic postponement cost mu_k from Eq. 2: higher for urgent FGs."""
    slack = fg.deadline_ms - (t_end_ms + fg.duration_ms)
    return mu_base / max(EPSILON_MS, slack)


class MILPScheduler:
    """Solves the Frame Group rescheduling problem via PuLP.

    Args:
        gamma: Penalty weight for WiFi airtime overlapping the sensing
            window (Eq. 11, term 2).
        lambda_weight: Penalty weight for scheduling deviation from each
            FG's original start time (Eq. 11, term 3).
        mu_base: Base postponement cost scaled by urgency per Eq. 2.
        solver: A configured ``pulp.LpSolver`` instance. Defaults to the
            PuLP HiGHS backend; pass a custom solver to use another backend.
        time_limit_s: Wall-clock solver time limit, forwarded to the
            default HiGHS solver. Ignored if a custom ``solver`` is supplied.
            If the limit stops the solve with a feasible solution, the
            result has ``solver_status == UNPROVEN_STATUS``, not "Optimal".
        hard_free_window: If true, no scheduled FG may overlap the free
            window (``sum(z_free) == 0``) and an FG that cannot move clear
            is vacated to the next cycle even if that misses its deadline.
            This is the rigid "WiFi-Vacate" scheme of Exp 5. See
            :class:`HardConstraintMILPScheduler`.
    """

    def __init__(
        self,
        gamma: float = 10.0,
        lambda_weight: float = 1.0,
        mu_base: float = 100.0,
        solver: pulp.LpSolver | None = None,
        time_limit_s: float | None = 10.0,
        hard_free_window: bool = False,
    ) -> None:
        self.gamma = gamma
        self.lambda_weight = lambda_weight
        self.mu_base = mu_base
        self.solver = solver
        self.time_limit_s = time_limit_s
        self.hard_free_window = hard_free_window

    def schedule(
        self,
        frame_groups: list[FrameGroup],
        d_free_ms: float,
        d_sensing_ms: float,
        t_start_ms: float,
        t_end_ms: float,
    ) -> ScheduleResult:
        """Formulate and solve the MILP program for one scheduling cycle.

        Raises:
            ConfigurationError: if the scheduling horizon or window
                durations are invalid (``t_end_ms <= t_start_ms``, or a
                negative duration).
        """
        if t_end_ms <= t_start_ms:
            raise ConfigurationError(
                f"t_end_ms ({t_end_ms}) must be greater than t_start_ms ({t_start_ms})"
            )
        if d_free_ms < 0 or d_sensing_ms < 0:
            raise ConfigurationError("d_free_ms and d_sensing_ms must be non-negative")

        began = time.perf_counter()

        if not frame_groups:
            return self._trivial_result(
                d_free_ms, d_sensing_ms, t_start_ms, t_end_ms, began
            )

        horizon_ms = t_end_ms - t_start_ms
        big_m = _big_m(
            frame_groups, horizon_ms, d_free_ms, d_sensing_ms, t_start_ms, t_end_ms
        )

        prob = pulp.LpProblem("WiFi_FG_Rescheduling", pulp.LpMinimize)

        s_prime = {
            fg.fg_id: pulp.LpVariable(
                f"s_prime_{fg.fg_id}", lowBound=t_start_ms, upBound=t_end_ms
            )
            for fg in frame_groups
        }
        y = {
            fg.fg_id: pulp.LpVariable(f"y_{fg.fg_id}", lowBound=0)
            for fg in frame_groups
        }
        p = {
            fg.fg_id: pulp.LpVariable(f"P_{fg.fg_id}", cat="Binary")
            for fg in frame_groups
        }
        z_free = {
            fg.fg_id: pulp.LpVariable(f"z_free_{fg.fg_id}", cat="Binary")
            for fg in frame_groups
        }
        z_sense = {
            fg.fg_id: pulp.LpVariable(f"z_sense_{fg.fg_id}", cat="Binary")
            for fg in frame_groups
        }
        # Side-selector binaries for the disjunctive overlap-avoidance
        # constraints below: w=0 means "FG scheduled entirely before the
        # window", w=1 means "FG scheduled entirely after the window". Only
        # meaningful when the corresponding z is 0 (overlap not permitted).
        w_free = {
            fg.fg_id: pulp.LpVariable(f"w_free_{fg.fg_id}", cat="Binary")
            for fg in frame_groups
        }
        w_sense = {
            fg.fg_id: pulp.LpVariable(f"w_sense_{fg.fg_id}", cat="Binary")
            for fg in frame_groups
        }
        b_order = {
            (a.fg_id, b.fg_id): pulp.LpVariable(f"b_{a.fg_id}_{b.fg_id}", cat="Binary")
            for a, b in combinations(frame_groups, 2)
        }
        # Selects which window comes first when neither overlaps WiFi
        # (Eq. 3/13): b_ws=0 -> free window ends before sensing starts;
        # b_ws=1 -> sensing window ends before free starts.
        b_ws = pulp.LpVariable("b_ws", cat="Binary")

        t_free_start = pulp.LpVariable(
            "T_free_start", lowBound=t_start_ms, upBound=t_end_ms
        )
        t_sense_start = pulp.LpVariable(
            "T_sense_start", lowBound=t_start_ms, upBound=t_end_ms
        )
        t_free_end = t_free_start + d_free_ms
        t_sense_end = t_sense_start + d_sensing_ms

        mu = {
            fg.fg_id: _postponement_cost(fg, t_end_ms, self.mu_base)
            for fg in frame_groups
        }
        # Drop binaries (Issue #38). Eq. 14 is a conjunction over all FGs, so
        # one FG that can neither fit this cycle nor be postponed in time made
        # the whole cycle infeasible. D_k = 1 lets that FG go (it is reported
        # postponed past its deadline, i.e. dropped) at a cost above every
        # other term combined, so the solver drops an FG only when no
        # schedule can serve it. Hard mode already vacates without a
        # deadline check, so it needs no drop variable.
        drop = (
            {}
            if self.hard_free_window
            else {
                fg.fg_id: pulp.LpVariable(f"D_{fg.fg_id}", cat="Binary")
                for fg in frame_groups
            }
        )
        drop_cost = _drop_cost(
            frame_groups, big_m, self.gamma, self.lambda_weight, mu.values()
        )
        prob += (
            pulp.lpSum(z_free[fg.fg_id] * fg.duration_ms for fg in frame_groups)
            + self.gamma
            * pulp.lpSum(z_sense[fg.fg_id] * fg.duration_ms for fg in frame_groups)
            + self.lambda_weight * pulp.lpSum(y.values())
            + pulp.lpSum(mu[fg.fg_id] * p[fg.fg_id] for fg in frame_groups)
            + drop_cost * pulp.lpSum(drop.values())
        )

        # Window timing constraints (Eq. 3/13): the free and sensing
        # windows must lie within the horizon and remain non-overlapping.
        # The ZBeacon is transmitted inside the announced sensing window
        # (Section 3.1), which is a separate reservation from the ZigBee
        # data window, not a sub-interval carved out of it.
        prob += t_free_end <= t_end_ms
        prob += t_sense_end <= t_end_ms
        prob += t_free_end <= t_sense_start + big_m * (1 - b_ws)
        prob += t_sense_end <= t_free_start + big_m * b_ws

        for fg in frame_groups:
            fid = fg.fg_id
            s = s_prime[fid]
            d = fg.duration_ms

            # Deviation (Eq. 1 term 3: lambda * |s'_k - s_k|). Both sides are
            # needed; with only y_k >= s'_k - s_k, moving an FG earlier than
            # its original start would cost nothing. A postponed FG (P_k = 1)
            # is not sent this cycle; mu_k already prices it, so its
            # placeholder s'_k carries no deviation cost.
            prob += y[fid] >= s - fg.original_start_ms - big_m * p[fid]
            prob += y[fid] >= fg.original_start_ms - s - big_m * p[fid]

            # Causality / QoS / postponement feasibility (Eq. 14).
            prob += fg.arrival_time_ms <= s + big_m * p[fid]
            prob += s + d <= fg.deadline_ms + big_m * p[fid]
            if not self.hard_free_window:
                # Soft mode postpones only FGs that can still meet their
                # deadline next cycle. Hard mode must vacate the window at
                # any cost, so a vacated FG may miss its deadline (a drop).
                prob += t_end_ms + d <= fg.deadline_ms + big_m * (
                    1 - p[fid] + drop[fid]
                )
                prob += drop[fid] <= p[fid]  # A dropped FG is not sent now.
            prob += s + d <= t_end_ms + big_m * p[fid]

            # Overlap avoidance (Eq. 16/17 intent, corrected disjunctive
            # Big-M encoding). The paper's Appendix A text, as extracted
            # from the source PDF, gives one-directional inequalities
            # (s'_k - T_end <= M*z, T_start - (s'_k+d_k) <= M*z) that,
            # worked through algebraically, force z=1 whenever the FG does
            # NOT overlap the window and permit z=0 under overlap - the
            # opposite of the stated intent ("z indicates overlap duration
            # to minimize") and incompatible with the hard sensing
            # constraint sum(z_sense) == 0 (it would force every FG away
            # from, not into avoiding, the sensing window). This is most
            # likely a PDF-extraction artifact (the appendix math is badly
            # garbled in the source). We instead use the same disjunctive
            # ordering pattern already used for Eq. 13 and Eq. 15 above:
            # when z=0, the FG must lie entirely before OR entirely after
            # the window (selected by w); when z=1, both constraints relax
            # and the FG may overlap at the cost of z*d_k in the objective.
            # A postponed FG (P_k = 1) is not on air this cycle, so it does
            # not constrain the windows, as for Eq. 15 below.
            pk = p[fid]
            prob += s + d <= t_free_start + big_m * (w_free[fid] + z_free[fid] + pk)
            prob += s >= t_free_end - big_m * ((1 - w_free[fid]) + z_free[fid] + pk)
            prob += s + d <= t_sense_start + big_m * (w_sense[fid] + z_sense[fid] + pk)
            prob += s >= t_sense_end - big_m * ((1 - w_sense[fid]) + z_sense[fid] + pk)

        # Sensing window protection (Eq. 17): hard constraint, no exceptions.
        # Combined with the disjunction above, z_sense forced to 0 means
        # every FG is genuinely, unconditionally clear of the sensing window.
        prob += pulp.lpSum(z_sense.values()) == 0
        if self.hard_free_window:
            # Exp 5 (rigid WiFi-Vacate): the free window is protected like
            # the sensing window. Overlap is forbidden, not penalized.
            prob += pulp.lpSum(z_free.values()) == 0

        # Inter-FG conflict avoidance (Eq. 15): only applies to FGs that are
        # both scheduled this cycle (postponed FGs are exempt via P_k, P_j).
        for a, b in combinations(frame_groups, 2):
            b_var = b_order[(a.fg_id, b.fg_id)]
            sa, sb = s_prime[a.fg_id], s_prime[b.fg_id]
            pa, pb = p[a.fg_id], p[b.fg_id]
            prob += sa + a.duration_ms <= sb + big_m * (1 - b_var + pa + pb)
            prob += sb + b.duration_ms <= sa + big_m * (b_var + pa + pb)

        solver = self.solver or pulp.HiGHS(msg=False, timeLimit=self.time_limit_s)
        prob.solve(solver)
        status = pulp.LpStatus[prob.status]
        if status == OPTIMAL_STATUS and prob.sol_status != pulp.LpSolutionOptimal:
            # Stopped early (time or iteration limit) with a feasible but
            # unproven solution. It still meets every hard constraint.
            status = UNPROVEN_STATUS

        if status not in (OPTIMAL_STATUS, UNPROVEN_STATUS):
            # .value() can be None for every variable here (Infeasible, or
            # the solver hit time_limit_s before it found any solution:
            # "Not Solved").
            # Silently falling back to each FG's original_start_ms and a
            # window collapsed to t_start_ms would look like a normal,
            # successful result and could overlap the sensing window,
            # breaking the hard Eq. 17 guarantee for a caller that doesn't
            # check solver_status. Claim nothing instead: every FG is
            # postponed and no window is reserved.
            return self._no_solution_result(
                frame_groups, t_start_ms, t_end_ms, status, began
            )

        free_start_val = t_free_start.value()
        sense_start_val = t_sense_start.value()
        resolved_free_start = (
            free_start_val if free_start_val is not None else t_start_ms
        )
        resolved_sense_start = (
            sense_start_val if sense_start_val is not None else t_start_ms
        )

        decisions = [
            self._decision_from_solution(fg, s_prime[fg.fg_id], p[fg.fg_id], t_end_ms)
            for fg in frame_groups
        ]

        return ScheduleResult(
            decisions=decisions,
            t_free_start_ms=resolved_free_start,
            t_free_end_ms=resolved_free_start + d_free_ms,
            t_sense_start_ms=resolved_sense_start,
            t_sense_end_ms=resolved_sense_start + d_sensing_ms,
            solver_status=status,
            solve_time_ms=(time.perf_counter() - began) * 1000.0,
        )

    @staticmethod
    def _no_solution_result(
        frame_groups: list[FrameGroup],
        t_start_ms: float,
        t_end_ms: float,
        status: str,
        began: float,
    ) -> ScheduleResult:
        """Safe result when the solver found no usable solution.

        Every FG is reported postponed (never silently kept at its
        original, possibly sensing-window-violating position) and no
        window is reserved, so a caller that doesn't check
        ``solver_status`` still can't send WiFi traffic into a window that
        was never actually solved for.
        """
        decisions = [
            SchedulingDecision(
                fg_id=fg.fg_id,
                scheduled_start_ms=t_end_ms,
                is_postponed=True,
                delay_ms=max(0.0, t_end_ms - fg.original_start_ms),
            )
            for fg in frame_groups
        ]
        return ScheduleResult(
            decisions=decisions,
            t_free_start_ms=t_start_ms,
            t_free_end_ms=t_start_ms,
            t_sense_start_ms=t_start_ms,
            t_sense_end_ms=t_start_ms,
            solver_status=status,
            solve_time_ms=(time.perf_counter() - began) * 1000.0,
        )

    @staticmethod
    def _decision_from_solution(
        fg: FrameGroup,
        s_var: pulp.LpVariable,
        p_var: pulp.LpVariable,
        t_end_ms: float,
    ) -> SchedulingDecision:
        p_value = p_var.value()
        is_postponed = bool(round(p_value)) if p_value is not None else False
        if is_postponed:
            # s_prime is unconstrained by the postponement-feasibility
            # branch once p=1 (Eq. 14 relaxes it), so the solver is free to
            # park it anywhere; it minimizes the Eq. 12 delay term by
            # setting s_prime close to original_start_ms, which would
            # misreport a postponed FG as scheduled this cycle at ~0 delay.
            # Report it the same way every other postponement in this
            # codebase does (defer_around_window, _carryover_busy,
            # _no_solution_result): at t_end_ms.
            scheduled_start = t_end_ms
        else:
            s_value = s_var.value()
            scheduled_start = s_value if s_value is not None else fg.original_start_ms
        delay_ms = max(0.0, scheduled_start - fg.original_start_ms)
        return SchedulingDecision(
            fg_id=fg.fg_id,
            scheduled_start_ms=scheduled_start,
            is_postponed=is_postponed,
            delay_ms=delay_ms,
        )

    @staticmethod
    def _trivial_result(
        d_free_ms: float,
        d_sensing_ms: float,
        t_start_ms: float,
        t_end_ms: float,
        began: float,
    ) -> ScheduleResult:
        """Window placement when there are no Frame Groups to schedule."""
        t_free_start = t_start_ms
        t_free_end = t_free_start + d_free_ms
        t_sense_start = t_free_end
        t_sense_end = t_sense_start + d_sensing_ms
        status = "Optimal" if t_sense_end <= t_end_ms else "Infeasible"
        return ScheduleResult(
            decisions=[],
            t_free_start_ms=t_free_start,
            t_free_end_ms=t_free_end,
            t_sense_start_ms=t_sense_start,
            t_sense_end_ms=t_sense_end,
            solver_status=status,
            solve_time_ms=(time.perf_counter() - began) * 1000.0,
        )


class HardConstraintMILPScheduler(MILPScheduler):
    """Rigid MILP scheduler for Exp 5 ("WiFi-Vacate", Section 5.1).

    WiFi FGs never overlap the free window. An FG that cannot move clear of
    the window is vacated to the next cycle, even if it then misses its
    deadline. The soft :class:`MILPScheduler` only penalizes such overlap.
    """

    def __init__(
        self,
        gamma: float = 10.0,
        lambda_weight: float = 1.0,
        mu_base: float = 100.0,
        solver: pulp.LpSolver | None = None,
        time_limit_s: float | None = 10.0,
    ) -> None:
        """Create a :class:`MILPScheduler` with ``hard_free_window=True``."""
        super().__init__(
            gamma=gamma,
            lambda_weight=lambda_weight,
            mu_base=mu_base,
            solver=solver,
            time_limit_s=time_limit_s,
            hard_free_window=True,
        )
