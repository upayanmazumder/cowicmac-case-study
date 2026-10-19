# CoWiC-MAC: WiFi and ZigBee Coexistence by Coordinated Scheduling

Computer Networks, F1+TF1 Case Study, Review 3

| Team member     | Registration |
| --------------- | ------------ |
| Upayan Mazumder | 24BDS0367    |
| Nitesh Pandey   | 24BCB0141    |
| Divyansh Saxena | 24BCB0108    |

Base paper: T. Cheng, S. Li, Y. Pan, "CoWiC-MAC: A coordinated WiFi-centric MAC mechanism in heterogeneous wireless networks", *Computer Networks* 287 (2026) 112479 [1]. Code: github.com/upayanmazumder/cowicmac-case-study.

## 1. The problem

WiFi (IEEE 802.11) and ZigBee (IEEE 802.15.4) both use the 2.4 GHz ISM band, so their frames can overlap in time and corrupt each other. The paper cites measurements in which ZigBee packet loss exceeds 60 % and WiFi throughput drops by 20 % (Sec. 1, refs. [5, 6] of the paper).

The paper names two causes (Sec. 1):

1. **WiFi traffic is bursty.** A ZigBee node uses CSMA/CA. It senses the channel (clear channel assessment, CCA) and sends only when the channel is idle. Under heavy WiFi load the idle gaps (whitespaces) are short and hard to predict, so ZigBee rarely finds a gap that is long enough for a packet, its backoff and its ACK.
2. **WiFi is the stronger technology.** It transmits at higher power and with more aggressive channel access, so ZigBee loses when both transmit.

Earlier fixes fall in two groups. Opportunistic schemes only use the gaps that exist. Explicit schemes such as ECC reserve the channel with a CTS frame, but ECC does not know how much airtime ZigBee needs, and a CTS reservation (NAV) is limited to 40 ms (paper Sec. 5.3.1). Both groups treat the other network as a black box.

**Idea of CoWiC-MAC.** The WiFi access point (AP) already schedules its stations. CoWiC-MAC lets the ZigBee coordinator (ZC) tell the AP how much airtime it needs. The AP then moves some WiFi traffic in time to open one clean window, and tells the ZC where the window is. The ZC reshapes its own superframe to fit the window. Contention is replaced by an agreed schedule.

## 2. Networking background used in this project

**IEEE 802.11ax (WiFi 6).** The AP broadcasts beacons. With Target Wake Time (TWT) and Trigger Frames the AP, not the stations, decides when each station transmits. This gives the AP control over WiFi timing (paper Sec. 3.1). The AP groups nearby WiFi transmissions into *Frame Groups* (FGs). An FG is the unit that CoWiC-MAC moves.

**IEEE 802.15.4 beacon-enabled mode (ZigBee).** The ZC sends a beacon that starts a *superframe*. The superframe has up to 16 equal time slots of length t_SfSlot = 0.96 ms × 2^SO, with superframe order SO from 0 to 14. The first slot carries the beacon. The next slots form the *Contention Access Period* (CAP), where nodes use slotted CSMA/CA. The last slots form the *Contention-Free Period* (CFP), where nodes use *Guaranteed Time Slots* (GTS) and do not contend. At most 7 slots can be GTS, and the CAP and CFP together have at most 15 slots.

**Cross-Technology Communication (CTC).** WiFi and ZigBee radios cannot decode each other's frames, so they need a trick to exchange data (paper Sec. 3.2).

| Direction | Method | Idea |
| --- | --- | --- |
| WiFi to ZigBee | WiCast [4] | The AP shapes the payload of a WiFi frame so that its waveform looks like a ZigBee signal. DSSS spreading in ZigBee tolerates the small errors. |
| ZigBee to WiFi | SymBee (paper ref. [39]) | The ZC sends chosen symbols (0xEF, 0x67). They cause phase shifts of +4π/5 and −4π/5 at the WiFi receiver, which the AP can detect. The rate is 1/8 of the normal ZigBee rate, which is enough for short demand messages. |

The paper calls the control frames WBeacon (AP to ZC) and ZBeacon (ZC to AP).

## 3. How CoWiC-MAC works

### 3.1 One coordination cycle

The protocol repeats every ZigBee period T_ZB (for example 1000 ms). Figure 1 shows one cycle.

<svg viewBox="0 0 760 200" width="100%" xmlns="http://www.w3.org/2000/svg" font-family="Arial, sans-serif" font-size="11">
<text x="4" y="30" font-weight="bold">WiFi</text>
<text x="4" y="95" font-weight="bold">Windows</text>
<text x="4" y="160" font-weight="bold">ZigBee</text>
<rect x="125" y="15" width="75" height="26" fill="#c9d7e8" stroke="#133a6f"/><text x="150" y="32">FG 1</text>
<rect x="208" y="15" width="62" height="26" fill="#c9d7e8" stroke="#133a6f"/><text x="228" y="32">FG 2</text>
<rect x="280" y="15" width="80" height="26" fill="#c9d7e8" stroke="#133a6f"/><text x="300" y="32">FG 3</text>
<rect x="395" y="15" width="40" height="26" fill="#f5d9a8" stroke="#8a5a00"/>
<rect x="590" y="15" width="70" height="26" fill="#c9d7e8" stroke="#133a6f"/><text x="610" y="32">FG 5</text>
<rect x="670" y="15" width="70" height="26" fill="#c9d7e8" stroke="#133a6f"/><text x="690" y="32">FG 6</text>
<rect x="60" y="80" width="56" height="26" fill="#e8e8e8" stroke="#555"/><text x="64" y="97" font-size="9">sensing</text>
<rect x="370" y="80" width="200" height="26" fill="#d8efd8" stroke="#2a6b2a"/><text x="400" y="97">free window (D_free)</text>
<rect x="64" y="145" width="48" height="26" fill="#f0f0f0" stroke="#555"/><text x="68" y="162" font-size="9">ZBeacon</text>
<rect x="372" y="145" width="16" height="26" fill="#b5d3f0" stroke="#133a6f"/>
<rect x="390" y="145" width="16" height="26" fill="#b5d3f0" stroke="#133a6f"/>
<rect x="408" y="145" width="75" height="26" fill="#e9f3e9" stroke="#2a6b2a"/><text x="425" y="162">CAP</text>
<rect x="485" y="145" width="83" height="26" fill="#d8efd8" stroke="#2a6b2a"/><text x="510" y="162">CFP (GTS)</text>
<text x="372" y="140" font-size="9">B1 B2</text>
<text x="388" y="55" font-size="9" fill="#8a5a00">residual WiFi (unavoidable)</text>
<line x1="60" y1="190" x2="745" y2="190" stroke="#333"/><text x="700" y="198" font-size="9">time</text>
</svg>

*Figure 1. One cycle (not to scale). The AP moves FG 4 out of the free window and keeps the sensing window clear. A small residual WiFi fragment remains inside the free window, and the ZC avoids it.*

### 3.2 The four-step handshake (paper Sec. 4.2)

| Step | What happens | Network concept |
| --- | --- | --- |
| 1. Discovery | The AP broadcasts WBeacon 1 with the beacon interval and the sensing window position. The ZC listens by default, so it needs no association. | Beacon-based synchronization without strict global time. |
| 2. Demand report | In the sensing window the AP listens for CTC signals. The ZC sends a ZBeacon with its traffic load. | Uplink CTC (Z2W). |
| 3. Notification | The AP reschedules its FGs, then broadcasts WBeacon 2 with the free window and the sensing window. | Downlink CTC (W2Z). |
| 4. Collision-free access | WiFi defers traffic out of the free window. ZigBee transmits inside it with an adapted superframe. | Time-division channel access. |

**Failure handling (Sec. 4.5.2).** If the AP does not get a valid ZBeacon, it assumes no ZigBee demand. If the ZC does not decode a WBeacon, it stays in listening mode and tries again next cycle. After a failed handshake both sides use plain CSMA/CA, so CTC errors lower efficiency but do not break normal operation. The messages are refreshed every cycle, so no retransmission protocol is needed.

### 3.3 WiFi side: choosing which FGs to move (paper Sec. 4.3, App. A)

The AP chooses a new start time s'_k for every FG k in the cycle [T_start, T_end]. An FG has an arrival time a_k, a duration d_k, a deadline e_k and an original start s_k. The AP also chooses where to place two windows: the free window for ZigBee data and a short sensing window for the next ZBeacon.

The objective (paper Eq. 1) minimizes four costs:

<div class="eq">min &nbsp; Σ z<sub>k</sub><sup>free</sup>·d<sub>k</sub> &nbsp;+&nbsp; γ Σ z<sub>k</sub><sup>sense</sup>·d<sub>k</sub> &nbsp;+&nbsp; λ Σ |s'<sub>k</sub> − s<sub>k</sub>| &nbsp;+&nbsp; Σ μ<sub>k</sub>·P<sub>k</sub></div>

| Term | Meaning |
| --- | --- |
| z_free · d | WiFi airtime that overlaps the free window. |
| γ · z_sense · d | WiFi airtime that overlaps the sensing window. γ is large because a lost ZBeacon breaks the loop. |
| λ · \|s' − s\| | How far an FG moves. This limits WiFi delay and jitter. |
| μ_k · P_k | Cost of postponing FG k to the next cycle (P_k = 1). μ_k = μ_base / max(ε, e_k − (T_end + d_k)) grows as the deadline gets close. |

The constraints keep the schedule physically valid (paper Eq. 3 to 5, 13 to 17):

- The free window and the sensing window lie inside the cycle and do not overlap.
- An FG cannot start before it arrives and must end before its deadline.
- A postponed FG must still be able to meet its deadline in the next cycle.
- Two FGs in the same cycle must not overlap (WiFi must not collide with itself).
- Nothing may overlap the sensing window. This is a hard rule.

The indicator functions make the problem non-linear. The paper rewrites it as a Mixed-Integer Linear Program (MILP) with the *Big-M method*. A binary variable switches a constraint on or off, and a large constant M makes the constraint harmless when it is off. We solve the MILP with HiGHS through PuLP, with a 10 s limit per cycle.

**Cheaper alternative (Sec. 4.3).** For weak APs, the AP skips the solver and uses an idle gap that is long enough, or it extends the largest gap. We use this *whitespace expansion* as a fallback when the MILP has no usable solution.

### 3.4 ZigBee side: L-WSA (paper Sec. 4.4)

CoWiC-MAC splits the ZigBee beacon into two parts (paper Sec. 3.1). Beacon1 tells the nodes when Beacon2 will come. Beacon2 carries the slot allocation. Because of this split, the ZC can start the superframe later than the cycle start, at a beacon offset, and so can skip an early WiFi fragment.

The AP cannot always clear the free window completely. The ZC learns where the residual WiFi fragments are and runs the Lightweight Window-Scaling Algorithm (L-WSA). It picks four parameters: the slot length t_SfSlot, the number of CAP slots i, the number of CFP slots j, and the beacon offset t_offset. It maximizes (paper Eq. 6):

<div class="eq">η<sub>total</sub> = m · t<sub>min</sub> · η<sub>cap</sub> + α · n · t<sub>SfSlot</sub> · η<sub>cfp</sub></div>

Here m is the number of CAP transmission attempts that do not overlap WiFi, and n is the number of GTS slots that do not overlap WiFi (Eq. 9). η is payload time divided by total time, so backoff, turnaround and ACK count as overhead. α weights the CFP higher because GTS access is deterministic. The search is limited by the standard: (1 + i + j) · t_SfSlot must fit the window, j ≤ 7 and i + j ≤ 15 (Eq. 10). The space is small, so the ZC enumerates all candidates. This is cheap enough for a ZigBee device.

## 4. Our simulation

The paper used NS-3 with traces recorded from a real router. We built a software-only simulation in Python. Table 1 shows what we kept and what we simplified.

| Aspect | Paper | Our simulation |
| --- | --- | --- |
| Simulator | NS-3 | SimPy discrete-event simulation, one cycle per T_ZB |
| WiFi traffic | Four 10-minute tcpdump traces | Synthetic traces from the paper's statistics (Table 5): exponential FG lengths and whitespaces, same mean values |
| ZigBee network | One ZC and five end devices | One ZC and one traffic source |
| CTC | Real signal emulation | Control messages with a packet error rate and a fixed delay. Lost messages trigger the fallback behavior. |
| Collision | Overlap in time corrupts both frames | The same rule (Sec. 5.1 of the paper) |
| Scheduler | MILP | MILP (HiGHS) with whitespace fallback |

*Table 1. Paper and simulation.*

**Shared medium.** The channel is one half-duplex resource. Every WiFi FG and every ZigBee packet is registered with a start time and a duration. If two registered bursts overlap in time, both are marked failed. The metrics read these records at the end of the run. This is the same "mutual corruption" rule the paper uses.

**Code layout.** `src/cowic/wifi` holds the AP state machine, the MILP, the baselines and the fallback. `src/cowic/zigbee` holds the ZC state machine and L-WSA. `src/cowic/ctc` holds the control channel. `src/cowic/sim` holds the SimPy engine and the medium. `src/cowic/analysis` holds the metrics and plots.

**Metrics (paper Sec. 5.1).**

| Metric | Definition in the code |
| --- | --- |
| G_z, ZigBee gain | Delivered ZigBee payload divided by the payload the same demand would carry in a clean window, summed over all cycles. A cycle with no granted window still counts as unmet demand. |
| PDR_w, WiFi drop rate | Airtime of FGs that missed their deadline or were destroyed by a collision, divided by requested WiFi airtime. |
| U_eff | Successful WiFi, ZigBee and sensing airtime divided by run time. |
| Jitter, moved FGs, maximum delay | Spread of FG delay, count of FGs moved from their original time, and the largest delay. |

## 5. Experiments

The six configurations follow Table 4 of the paper. Exp 1 to 3 are baselines. Exp 4 is CoWiC-MAC. Exp 5 and 6 each remove one part, so they show what that part contributes.

| Exp | WiFi scheduling | CTC | ZigBee superframe | Sensing window | Purpose |
| --- | --- | --- | --- | --- | --- |
| 1 | None | None | Fixed | None | Uncoordinated baseline |
| 2 | None, use gaps | WiFi to ZigBee | Fixed | None | Opportunistic baseline |
| 3 | ECC (CTS, NAV up to 40 ms) | Both | Fixed | None | Explicit reservation baseline |
| 4 | MILP, soft | Both | L-WSA | 10 ms | CoWiC-MAC |
| 5 | MILP, hard | WiFi to ZigBee | Fixed | None | WiFi must vacate the whole window |
| 6 | MILP, soft | WiFi to ZigBee | L-WSA | None | CoWiC-MAC without the sensing window |

Four traffic profiles come from the paper (Table 5): Traffic I is the busiest and Traffic IV is the lightest. The sweeps follow the paper's figures:

- **Fig. 11 to 14:** vary D_free (31, 62, 93, 123 ms) and T_ZB (1000, 2000, 5000 ms).
- **Fig. 15:** vary the WiFi occupancy (all four traces) at T_ZB = 1000 ms and D_free = 63 ms.
- **Fig. 16 and 17:** vary D_sensing (10, 20, 50, 100 ms) for Exp 4 on Traffic I and Traffic III.

## 6. Results

We ran 200 simulations of 60 s each (seed 42, `results/benchmark_results.csv`). The paper uses 600 s traces, so this is a reference run and not a full reproduction. All values below come from this run, at T_ZB = 1000 ms on Traffic I (88 % occupied).

| Exp | G_z at D_free = 31 / 62 / 123 ms | Paper G_z | PDR_w at 31 / 62 / 123 ms | Paper PDR_w |
| --- | --- | --- | --- | --- |
| 1 | 0.13 / 0.12 / 0.12 | 0.013 at 31 ms | 11 / 14 / 14 % | 9.7 % at 123 ms |
| 2 | 0.48 / 0.28 / 0.28 | 0.558 at 31 ms, 0.085 at 123 ms | 0 / 0 / 0 % | 7.6 % at 123 ms |
| 3 | 1.00 / 0.67 / 0.67 | near 1 at 31 ms, flat beyond 40 ms | 0 / 0 / 0 % | below 1 % at 31 ms |
| 4 | 0.94 / 0.87 / 0.63 | above 0.986 / 0.963 / 0.934 to 0.947 | 1.3 / 2.7 / 4.0 % | 0 |
| 5 | 1.00 / 1.00 / 1.00 | 1.00 | 1.3 / 3.4 / 8.1 % | below 1 % at 31 ms, 2.8 % at 123 ms |
| 6 | 0.97 / 0.94 / 0.67 | same as Exp 4 | 1.3 / 2.7 / 4.5 % | 0 |

*Table 2. Traffic I. The acceptance check compares 17 numbers that the paper states for Exp 4, 5 and 6. Five pass.*

### 6.1 What matches the paper

- **Coordination helps, and the order of the schemes is right.** The uncoordinated scheme (Exp 1) delivers about 12 % of the ZigBee demand on Traffic I. Gap search (Exp 2) delivers 28 %. The NAV-limited ECC scheme (Exp 3) stops at 0.67 once D_free passes 40 ms. CoWiC-MAC (Exp 4 and 6) stays at 0.87 to 0.94 for D_free up to 62 ms.
- **Rigid vacating gives full ZigBee gain but costs WiFi.** Exp 5 reaches G_z = 1.00 at every D_free, and its WiFi drop rate rises to 8.1 % at 123 ms. The paper reports the same direction (2.8 % at 123 ms).
- **Load matters (Fig. 15).** When occupancy rises from 0.14 to 0.88, G_z of Exp 1 falls from 0.87 to 0.12 and G_z of Exp 2 falls from 1.00 to 0.28. Exp 4 falls only from 0.98 to 0.89. Exp 4 also loses no WiFi frames on the traces below 0.6 occupancy.
- **A long sensing window costs ZigBee airtime on busy traffic (Fig. 16 and 17).** On Traffic I at D_free = 31 ms, G_z of Exp 4 falls from 0.94 to 0.62 when D_sensing grows from 10 to 100 ms. The paper reports 0.905 to 0.480 (it does not state D_free for this sentence). On Traffic III, G_z stays at 0.98 for every D_sensing, as the paper says for sparse traffic.
- **Sparse traffic is easy (Fig. 14).** On Traffic III, Exp 4 has no WiFi loss and G_z of at least 0.95 at every D_free.

### 6.2 Where it does not match, and why

1. **Not enough airtime at high demand.** Traffic I keeps the channel 88 % busy. At D_free = 123 ms and D_sensing = 10 ms, the two windows need another 13.3 % of every 1000 ms cycle. The total is about 101 %, so no schedule can give ZigBee its full window and also lose no WiFi frame. The paper reports both, and we could not reproduce that on our traces.
2. **The paper does not give γ, λ or μ_base.** We use γ = 10, λ = 1 and μ_base = 100. These weights set the trade-off between delaying WiFi and giving airtime to ZigBee. In a 30 s test of Exp 4 at 31 ms, raising μ_base to 10000 removes all WiFi drops but lowers G_z from 0.92 to 0.71. We did not tune the weights to match the paper.
3. **Synthetic traffic.** We use the paper's mean FG and whitespace lengths, but not its real traces. The paper's Fig. 9 gives a whitespace tail that no distribution with the Table 5 mean for Traffic I can have (Markov's inequality: P(W > 10 ms) ≤ 5.3 / 10 = 53 %, while Fig. 9 states 79.2 %). `reviews/project_document.md` explains this in detail.
4. **First cycle.** In Exp 4 the AP has no demand report in the first cycle, so ZigBee gets no window then. This costs at most 1 cycle in 60.
5. **Exp 1 and Exp 2 differ from the paper by large amounts** (for example Exp 2 drops no WiFi frame in our run, but 7.6 % in the paper). We did not find the cause. In our model, Exp 2 sends ZigBee only in the gaps the AP announces.

The WiFi drop rate of Exp 4 is not zero at any D_free on Traffic I, so the claim "zero WiFi loss" is not reproduced for this traffic.

## 7. Verification

- 331 automated tests pass with 98.55 % line coverage. The suite includes checks of the MILP constraints, the L-WSA search against a brute-force search, the collision rule, the CTC channel and full multi-cycle runs.
- `pytest -m slow` compares 30 s runs with every number the paper states. Points that differ are listed with their cause. A point that starts to pass makes the test fail, so the list stays correct.
- The checks hold on every run: no two WiFi FGs overlap, no WiFi frame enters the sensing window, and ZigBee defers instead of transmitting into a WiFi fragment.

## 8. Limitations

- No radio model: no fading, capture effect or SNR. Any overlap in time is a collision.
- CTC is a message model with an error rate. It does not emulate the WiCast and SymBee waveforms.
- One ZigBee traffic source. Contention between five end devices and GTS allocation among them are not modeled.
- Synthetic traces replace the paper's real traces.
- The MILP solver has a 10 s limit per cycle. In the reference run 432 of the cycles stopped at this limit, so some values depend on the machine.

## 9. How to run it

```
pip install -e ".[dev]"
python -m cowic.sim --config configs/exp4_cowic_full.yaml --duration 10
python scripts/run_benchmark_sweep.py --quick
pytest
```

## References

[1] T. Cheng, S. Li, Y. Pan, "CoWiC-MAC: A coordinated WiFi-centric MAC mechanism in heterogeneous wireless networks", *Computer Networks* 287 (2026) 112479. doi:10.1016/j.comnet.2026.112479.

[2] IEEE Std 802.11ax-2021, *Enhancements for High Efficiency WLAN*.

[3] IEEE Std 802.15.4, *Low-Rate Wireless Networks*.

[4] D. Xia, X. Zheng, L. Liu, H. Ma, "Parallel cross-technology transmission from IEEE 802.11ax to heterogeneous IoT devices", *IEEE INFOCOM*, 2023 (WiCast, paper ref. [20]).

[5] SimPy, discrete-event simulation for Python, simpy.readthedocs.io.

[6] HiGHS, high-performance linear and mixed-integer optimization solver, highs.dev.
