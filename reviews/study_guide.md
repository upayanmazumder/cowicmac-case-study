# CoWiC-MAC Study Guide

Computer Networks case study, Review 3. For the three team members. Read the case study first, then use this guide to prepare for questions.

## 1. The project in five sentences

WiFi and ZigBee share the 2.4 GHz band. Real WiFi is stronger and accesses the channel more aggressively, so ZigBee suffers more from overlaps. (In our simulation any overlap marks both frames as failed.) In CoWiC-MAC the ZigBee coordinator (ZC) tells the WiFi access point (AP) how much airtime it needs. The AP moves some WiFi frame groups (FGs) in time and opens one free window. The AP tells the ZC where the window is, and the ZC fits its superframe into it. We built this as a software simulation and compared it with the base paper [1].

## 2. Concepts you must be able to explain

For each concept: say what it is, then say where it appears in our project.

| Concept | Explain it like this | Where it appears |
| --- | --- | --- |
| CSMA/CA | A node listens before it sends. If the channel is busy it waits a random backoff. | ZigBee in the CAP. Exp 1 uses it with no help. |
| CCA (clear channel assessment) | The listen step of CSMA/CA. A node measures energy on the channel. | L-WSA counts only attempts that find a clear channel. The engine defers an attempt that overlaps WiFi. |
| Why WiFi beats ZigBee | WiFi transmits at higher power and accesses the channel more aggressively. WiFi traffic is also bursty, so ZigBee finds only short gaps. | Section 1 of the paper. This is the problem we solve. |
| Whitespace | An idle gap between WiFi transmissions. | Exp 2 uses gaps. The fallback scheduler extends the largest gap. |
| Beacon-enabled 802.15.4 | The ZC sends a beacon that starts a superframe of up to 16 slots. | `src/cowic/zigbee/lwsa_adapter.py` |
| CAP and CFP | CAP: nodes contend with slotted CSMA/CA. CFP: nodes use guaranteed time slots (GTS) and do not contend. | L-WSA chooses how many CAP slots (i) and CFP slots (j). |
| Superframe order SO | Slot length is 0.96 ms × 2^SO. SO is 0 to 14. | L-WSA searches over it. |
| 802.11ax TWT and Trigger Frames | The AP decides when each station transmits. | This control is why the AP can move FGs. |
| NAV and CTS-to-self | A WiFi frame that tells other WiFi nodes to stay silent for a time. Legacy NAV is limited to 40 ms. | Exp 3 (ECC). It explains why Exp 3 stops at 40 ms. |
| CTC | Two radios of different standards exchange data without a gateway. WiFi to ZigBee: WiCast emulates a ZigBee waveform. ZigBee to WiFi: SymBee uses phase shifts, at 1/8 of the ZigBee rate. | `src/cowic/ctc/channel.py`. We model it as messages with an error rate. |
| WBeacon and ZBeacon | WBeacon: AP to ZC, carries the windows. ZBeacon: ZC to AP, carries the demand. | The handshake in the case study, Sec. 3.2. |
| Sensing window | A short time (10 ms in Exp 4) when the AP listens for the ZBeacon. No WiFi may transmit in it. | Hard constraint in the MILP. |
| MILP and Big-M | An optimization problem with integer variables and linear constraints. Big-M uses a binary variable and a large number to switch a constraint on or off. | `src/cowic/wifi/milp_scheduler.py` |
| Fallback | If the MILP gives no usable answer, use the simple gap method. After a CTC failure both sides return to CSMA/CA. | `src/cowic/wifi/fallback.py` |
| Discrete-event simulation | Time jumps from one event to the next. | SimPy engine, `src/cowic/sim/simulation_engine.py` |

## 3. One cycle, step by step

1. The AP broadcasts WBeacon 1 with the beacon interval and the position of the sensing window. The ZC listens by default and reads it.
2. The ZC sends a ZBeacon in the sensing window. It carries the airtime demand D_free.
3. The AP collects the FGs of the cycle and solves the MILP.
4. The MILP gives a new start time for each FG, a flag for each FG that waits for the next cycle, and the positions of the free window and the sensing window.
5. The AP sends WBeacon 2 with the window positions.
6. The ZC runs L-WSA. It picks slot length, CAP slots, CFP slots and beacon offset. It avoids the WiFi fragments that stay inside the window.
7. Both sides transmit. The medium marks any pair of overlapping transmissions as failed.
8. The metrics module reads the records and computes G_z, PDR_w, U_eff, jitter, moved FGs and maximum delay.

## 4. Numbers to remember

| Item | Value |
| --- | --- |
| Sensing window (Exp 4) | 10 ms |
| D_free sweep | 31, 62, 93, 123 ms |
| T_ZB sweep | 1000, 2000, 5000 ms |
| ZigBee slots | 16 per superframe, j ≤ 7, i + j ≤ 15 |
| ZigBee slot length | 0.96 ms × 2^SO |
| Legacy NAV limit (Exp 3) | 40 ms |
| WiFi postponement bounds (paper) | 50, 100, 150, 200 ms |
| MILP weights we use | γ = 10, λ = 1, μ_base = 100 (the paper gives no values) |
| CFP weight in L-WSA | α = 1.2 |
| Traffic I occupancy | 88 % (Traffic II 73 %, III 32 %, IV 14 %) |
| Tests | 331 pass, 98.55 % coverage |
| Reference run | 200 runs, 60 s each, seed 42 |

## 5. What the results say

Say these points in this order.

1. **Coordination works.** On Traffic I, Exp 1 delivers about 12 % of the demand. Exp 4 delivers 94 % at D_free = 31 ms and 87 % at 62 ms.
2. **Each part matters.** Exp 3 stops at 0.67 because of the 40 ms NAV limit. Exp 5 reaches 1.00 but drops up to 8.1 % of WiFi airtime at 123 ms. Exp 4 and 6 keep the WiFi loss between 1 % and 5 % in our run.
3. **Load matters.** As occupancy rises from 14 % to 88 %, Exp 1 falls from 0.87 to 0.12. Exp 4 falls from 0.98 to 0.89.
4. **Sensing costs airtime on busy traffic.** G_z of Exp 4 falls from 0.94 to 0.62 when D_sensing grows from 10 ms to 100 ms on Traffic I. It changes little on Traffic III (0.98 at D_free = 31 ms, 0.93 to 0.95 at 123 ms).
5. **We do not match the paper at high demand.** At D_free = 123 ms we get 0.63, the paper gets 0.93 to 0.95. Say why: the windows plus the load need about 101 % of the cycle, the paper gives no MILP weights, and our traces are synthetic.

Do not claim that we reproduce "zero WiFi loss". Our Exp 4 loses 1.3 % to 4.0 %.

## 6. Questions you may get

**Why does ZigBee lose more than WiFi when they overlap?**
WiFi transmits at higher power and accesses the channel more aggressively, so a WiFi frame overwhelms a ZigBee frame (paper Sec. 1). The paper cites ZigBee packet loss above 60 % and a WiFi throughput loss of 20 %. Our simulation uses the paper's simple rule: an overlap marks both frames as failed. ZigBee still loses more, because WiFi occupies most of the channel time.

**Why does ZigBee fail under heavy WiFi traffic even with CSMA/CA?**
At 88 % occupancy the gaps are short (5.3 ms mean in Traffic I). A ZigBee packet needs backoff slots, the packet and the ACK. Many gaps are too short, and a packet that starts in a gap can still be hit by the next WiFi frame.

**What does the AP gain from this? Does WiFi not lose?**
WiFi loses a little. It delays some FGs, and a small share misses its deadline. The objective function limits this: it penalizes how far an FG moves and how likely a postponed FG is to miss its deadline.

**What is a Frame Group?**
A set of WiFi transmissions that are close in time. The AP moves FGs as units, because packet-level scheduling is too large a problem. Aggregation keeps the number of FGs per cycle small (paper Sec. 4.3).

**Why a separate sensing window?**
The AP switches to spectrum sensing mode in this window to detect the ZBeacon. The paper reserves a fixed sensing window for reliable demand reporting (Sec. 4.3). We make the constraint hard: no WiFi FG may overlap it, because a lost ZBeacon breaks the loop.

**Why a MILP and not a simple rule?**
The AP must choose several things together: which FGs move, how far, which ones wait, and where both windows go. The objective has four competing costs. A MILP seeks an optimal schedule for a small number of FGs. The solver can stop at its time limit before it proves optimality (432 cycles in the reference run). The paper also gives a simple gap-based alternative, and we use it as a fallback.

**Explain Big-M.**
Take the rule "FG k must end before the window starts, or start after the window ends". A binary variable w picks one side. For each side we write a constraint with M × (something that is 0 or 1) added to the loose side. When the side is not chosen, M makes the constraint always true.

**Why is the paper's Eq. 16 not used as written?**
Read literally from the PDF text, the equation forces the overlap indicator to 1 for an FG that does not overlap. That contradicts the rule that nothing may overlap the sensing window. We use an equivalent form with a side-selecting binary. We could not check the original typeset equation, so this may come from the PDF text and not from the paper.

**What does L-WSA optimize, and why can it run on a ZigBee device?**
It maximizes the efficiency η_total over slot length, CAP slots, CFP slots and beacon offset. The ranges are small and fixed by the standard, so it checks every candidate.

**What are residual WiFi fragments?**
WiFi frames that the AP could not move out of the free window. The ZC senses them. It counts only the clear CAP attempts and GTS slots, and it can shift the superframe by a beacon offset to skip an early fragment.

**What happens if a CTC message is lost?**
The messages repeat every cycle. If the AP gets no ZBeacon, it assumes no ZigBee demand. If the ZC gets no WBeacon, it listens again. If the handshake fails, both sides use CSMA/CA. The paper calls this backward compatible (Sec. 4.5.2).

**Why did you use a simulation and not real radios?**
The paper also used simulation. It states that a USRP-based test cannot give the microsecond timing the protocol needs (Sec. 5.1). Our simulation models the MAC layer only.

**How is a collision decided?**
Every transmission is stored with a start time and a duration. If two overlap in time, both are marked failed. This is the paper's mutual-corruption rule. Overlaps shorter than 1 µs are ignored because of solver round-off.

**Define G_z and PDR_w.**
G_z is delivered ZigBee payload divided by the payload that the same demand would deliver in a clean window. PDR_w is the WiFi airtime that missed a deadline or collided, divided by the requested WiFi airtime.

**What is different between Exp 5 and Exp 4?**
Exp 5 forces WiFi to vacate the whole window (a hard constraint) and uses a fixed ZigBee superframe. Exp 4 only penalizes overlap (a soft constraint) and adapts the ZigBee superframe. Exp 5 shows what rigid clearing costs WiFi.

**Why does Exp 4 not reach the paper's numbers?**
Give the three causes from Section 5, point 5. Also say what we did not find: the large difference in Exp 1 and Exp 2.

**Is the simulation result valid?**
It is valid for the model: MAC layer only, synthetic traces, one ZigBee source, 60 s runs. We do not claim it reproduces the paper's absolute numbers.

**What would you do with more time?**
Use real WiFi traces. Model five ZigBee end devices. Run the full 600 s sweep with more seeds. Tune γ, λ and μ_base against a stated goal.

## 7. Where things are in the code

| Topic | File |
| --- | --- |
| FG and superframe data classes | `src/cowic/models/` |
| AP state machine | `src/cowic/wifi/ap_orchestrator.py` |
| MILP | `src/cowic/wifi/milp_scheduler.py` |
| Exp 1 to 3 baselines | `src/cowic/wifi/baselines.py` |
| Gap-based fallback and rigid vacate | `src/cowic/wifi/fallback.py` |
| ZC state machine | `src/cowic/zigbee/coordinator.py` |
| L-WSA | `src/cowic/zigbee/lwsa_adapter.py` |
| CTC channel | `src/cowic/ctc/channel.py` |
| Shared medium and collisions | `src/cowic/sim/channel_medium.py` |
| SimPy engine | `src/cowic/sim/simulation_engine.py` |
| Metrics and plots | `src/cowic/analysis/` |
| Synthetic WiFi traces | `traces/synthetic_generator.py` |
| Sweep and paper comparison | `scripts/run_benchmark_sweep.py`, `tests/test_paper_points.py` |

Who presents what (same as Review 2): Upayan, central architecture (engine, CTC, medium, metrics). Nitesh, WiFi side (traces, AP, MILP, baselines, fallback, sweep). Divyansh, ZigBee side (superframe, L-WSA, coordinator).

## 8. How to run a demo

Run these from the repository root with the virtual environment active.

```
python -m cowic.sim --config configs/exp1_uncoordinated.yaml --duration 10
python -m cowic.sim --config configs/exp4_cowic_full.yaml --duration 10
python scripts/run_benchmark_sweep.py --quick
python -m cowic.analysis --results-csv results/benchmark_results.csv --output-dir figures
pytest
```

Suggested order:

1. Run Exp 1, then Exp 4 with the same duration. Compare `zigbee_gain_ratio` and `wifi_drop_rate`.
2. Open `figures/fig11_Traffic_I_tzb1000.png` and walk through the six panels (G_z, PDR_w, U_eff, jitter, moved FGs, maximum delay).
3. Open `fig15_occupancy_tzb1000_dfree63.png` and explain the effect of load.
4. Open `fig16_sensing_Traffic_I_tzb1000.png` and explain the cost of the sensing window.
5. Run `pytest` and show the test count and the coverage.

## References

[1] T. Cheng, S. Li, Y. Pan, "CoWiC-MAC: A coordinated WiFi-centric MAC mechanism in heterogeneous wireless networks", *Computer Networks* 287 (2026) 112479.
