# F1+TF1 Computer Networks: Case Study Review 3

## 1. Case Study Title

### CoWiC-MAC: A Coordinated WiFi-Centric MAC Mechanism in Heterogeneous Wireless Networks

## Team Details

| S.No | Name            | Registration Number |
| ---- | --------------- | ------------------- |
| 1    | Upayan Mazumder | 24BDS0367           |
| 2    | Nitesh Pandey   | 24BCB0141           |
| 3    | Divyansh Saxena | 24BCB0108           |

---

## 2. Software Implementation Overview

This review documents the software-only CoWiC-MAC simulation built for the case study. The implementation follows Cheng, Li, and Pan (_Computer Networks_, 2026, Art. 112479).

Stack:

| Layer                 | Choice                      |
| --------------------- | --------------------------- |
| Language              | Python 3.11+                |
| Discrete-event engine | SimPy                       |
| MILP                  | PuLP + HiGHS (`highspy`)    |
| Tests                 | pytest                      |
| Lint / types          | ruff, mypy                  |

Verification status at document time:

- 263 automated tests passing
- CI green on Python 3.11 and 3.12
- Clean `mypy` and `ruff` checks

Implemented modules:

| Area       | Paths                                                                    |
| ---------- | ------------------------------------------------------------------------ |
| Models     | `src/cowic/models/frame_group.py`, `src/cowic/models/superframe.py`      |
| WiFi       | `ap_orchestrator.py`, `milp_scheduler.py`, `baselines.py`, `fallback.py` |
| ZigBee     | `coordinator.py`, `lwsa_adapter.py`                                      |
| CTC        | `src/cowic/ctc/channel.py`                                               |
| Simulation | `simulation_engine.py`, `channel_medium.py`                              |
| Analysis   | `metrics.py`, `plotter.py`                                               |
| Traces     | `traces/synthetic_generator.py`, `traces/playback.py`                    |
| Sweep      | `scripts/run_benchmark_sweep.py`                                         |

---

## 3. Contributor Workstream Summary

Presentation ownership matches Review 2.

| Contributor     | Presentation domain  | Main implementation focus                                                                                                   |
| --------------- | -------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Upayan Mazumder | Central architecture | Simulation engine, CTC, channel medium, MILP scheduler, L-WSA, plotter, integration                                         |
| Nitesh Pandey   | WiFi side            | Trace generator, AP orchestrator, baselines, fallback, hard-constraint MILP (Exp 5), engine fixes, metrics, benchmark sweep |
| Divyansh Saxena | ZigBee side          | ZigBee architecture review, superframe / L-WSA specification input, test design                                             |

---

## 4. System Architecture and Data Flow

The system has three domains: WiFi, ZigBee, and the shared 2.4 GHz medium. CTC joins the two MAC domains.

```mermaid
sequenceDiagram
  participant ZC as ZigBee Coordinator
  participant CTC as CTC Channel
  participant AP as AP Orchestrator
  participant SCH as MILP / Fallback
  participant LWSA as L-WSA
  participant MED as Channel Medium

  ZC->>CTC: ZBeacon (d_free_ms)
  CTC->>AP: demand
  AP->>SCH: reschedule Frame Groups
  SCH-->>AP: free + sensing windows
  AP->>CTC: WBeacon (window bounds)
  CTC->>ZC: grant
  ZC->>LWSA: adapt superframe
  LWSA-->>ZC: SuperframeConfig
  AP->>MED: WiFi transmissions
  ZC->>MED: ZigBee transmissions
  Note over MED: Records are read by the metrics module after the run
```

CTC payloads:

| Message | Direction | Fields                                                                   |
| ------- | --------- | ------------------------------------------------------------------------ |
| ZBeacon | ZC → AP   | `d_free_ms`                                                              |
| WBeacon | AP → ZC   | `t_free_start_ms`, `t_free_end_ms`, `t_sense_start_ms`, `t_sense_end_ms` |

Residual WiFi fragment lists stay outside the WBeacon scalar payload. The ZC receives them as a separate input, matching the base-paper model where the ZC senses leftover WiFi activity inside the granted window.

---

## 5. WiFi Frame Group MILP Formulation

Objective (Equations 11–17):

```math
\min \sum_{k=1}^{K} z_k^{\text{free}} d_k
+ \gamma \sum_{k=1}^{K} z_k^{\text{sense}} d_k
+ \lambda \sum_{k=1}^{K} y_k
+ \sum_{k=1}^{K} \mu_k P_k
```

Postponement cost (Eq. 2), larger for FGs close to their deadline:

```math
\mu_k = \frac{\mu_{\text{base}}}{\max\left(\epsilon,\ e_k - (T_{\text{end}} + d_k)\right)}
```

The code uses $\mu_{\text{base}} = 100$ and $\epsilon$ = 1 µs.

Constraint groups:

0. Window placement (Eq. 3/13): the free and sensing windows lie inside the cycle and do not overlap; a binary $b_{\text{ws}}$ picks which comes first
1. Delay definition for $y_k$
2. Causality with respect to arrival times
3. Deadline and cycle-end bounds
4. Feasibility of postponed Frame Groups in the next cycle
5. Inter-FG non-overlap through Big-M ordering binaries
6. Overlap indicator constraints for free and sensing windows (a disjunctive form: Eq. 16, as printed, would force $z_k = 1$ for non-overlapping FGs and contradict constraint 7)
7. Hard sensing-window protection: $\sum_k z_k^{\text{sense}} = 0$

Solver path: PuLP model solved with HiGHS (`highspy`). A custom solver can be supplied. If status is not optimal, the AP uses whitespace expansion fallback.

Postponed FG reporting convention: a postponed FG is reported at `scheduled_start_ms = t_end_ms` with `delay_ms = max(0, t_end_ms - original_start_ms)`.

---

## 6. ZigBee L-WSA Superframe Adaptation

Objective:

```math
\eta_{\text{total}}
= m \cdot t_{\min} \cdot \eta_{\text{cap}}^{p}
+ \alpha \cdot n \cdot t_{\text{SfSlot}} \cdot \eta_{\text{cfp}}^{p}
```

Search variables and domains:

| Variable            | Domain                                        |
| ------------------- | --------------------------------------------- |
| $t_{\text{SfSlot}}$ | $0.96 \cdot 2^{SO}$ ms, $SO \in [0, 14]$      |
| $j$                 | $[0, 7]$                                      |
| $i$                 | $i + j \le 15$                                |
| $t_{\text{offset}}$ | window start and residual-fragment boundaries |

Filter conditions:

- Active superframe duration must fit the free window.
- CAP slots contribute only when they clear a $t_{\min}$ occupancy check.
- CFP slots contribute only when the full slot clears residual WiFi.

IEEE 802.15.4 2.4 GHz O-QPSK constants used:

| Constant        | Value    |
| --------------- | -------- |
| Symbol duration | 0.016 ms |
| Base slot       | 0.96 ms  |
| Backoff slot    | 0.32 ms  |
| Turnaround      | 0.192 ms |
| ACK             | 0.352 ms |
| Max packet      | 4.256 ms (266 symbols: 127-byte PSDU plus 6-byte SHR/PHR) |

---

## 7. Cross-Technology Communication Model

The base paper uses WiCast for WiFi→ZigBee downlink and SymBee for ZigBee→WiFi uplink. This project does not emulate those waveforms.

Instead, CTC is a discrete-event control channel:

- Message types: `WBEACON`, `ZBEACON`
- Configurable packet error rate
- Scalar float payloads only

This abstraction is enough for MAC-layer coordination studies. It is not a PHY fidelity claim.

---

## 8. Experimental Design

| Exp   | Config                    | Purpose                   | Held fixed / disabled                |
| ----- | ------------------------- | ------------------------- | ------------------------------------ |
| Exp 1 | `exp1_uncoordinated.yaml` | Uncoordinated baseline    | No CTC, no MILP, no L-WSA            |
| Exp 2 | `exp2_opportunistic.yaml` | Natural whitespace only   | No WiFi rescheduling                 |
| Exp 3 | `exp3_ecc.yaml`           | NAV-style ECC             | Fixed ZigBee superframe              |
| Exp 4 | `exp4_cowic_full.yaml`    | Full CoWiC-MAC            | Soft MILP + L-WSA + sensing window   |
| Exp 5 | `exp5_rigid_vacate.yaml`  | Hard WiFi vacate ablation | Hard MILP + fixed ZigBee superframe  |
| Exp 6 | `exp6_overhead_free.yaml` | Overhead-free variant     | Soft MILP + L-WSA; no sensing window |

Exp 6 also changes CTC from bidirectional to WiFi-to-ZigBee.

The sweep has three stages:

| Stage     | Paper figure | Experiments | Traffic                          | $T_{ZB}$ (ms)    | $D_{\text{free}}$ (ms) | $D_{\text{sensing}}$ (ms) |
| --------- | ------------ | ----------- | -------------------------------- | ---------------- | ---------------------- | ------------------------- |
| Main      | Figs. 11–14  | Exp 1–6     | I and III (I–IV with `--traffic`) | 1000, 2000, 5000 | 31, 62, 93, 123        | Config value              |
| Occupancy | Fig. 15      | Exp 1–6     | I, II, III, IV                   | 1000             | 63                     | Config value              |
| Sensing   | Figs. 16, 17 | Exp 4       | I (Fig. 16), III (Fig. 17)       | 1000             | 31, 62, 93, 123        | 10, 20, 50, 100           |

Each stage uses 5 seeds. Each run replays 600 s of trace by default, as the paper traces in Fig. 10. The sweep warns when a run has fewer than 20 ZigBee cycles. The occupancy stage records the WiFi occupancy of each trace (busy airtime divided by run time). Fig. 15 uses this value as its x-axis.

Acceptance criterion: every $G_z$ and $PDR_w$ value that Sec. 5.3.1 states for Exp 4, 5, and 6 (Fig. 11: Traffic I, $T_{ZB} = 1000$ ms). The tolerance is ±0.02 for $G_z$ and ±0.005 for $PDR_w$. `acceptance_report` in `scripts/run_benchmark_sweep.py` lists each point with the paper sentence.

---

## 9. Evaluation and Key Claims

Hypothesis mapping:

| Hypothesis | Claim                           | Simulation support (reference run, Traffic I, $T_{ZB}$ = 1000 ms)                              |
| ---------- | ------------------------------- | ---------------------------------------------------------------------------------------------- |
| H1         | $G_z \ge 90\%$ under heavy load | Exp 4 holds at 31 ms (0.942), not at 62, 93 and 123 ms (0.873, 0.795, 0.609)                |
| H2         | $PDR_w = 0$                     | Not met: Exp 4 loses 1.3–4.1 % (postponement limit, see below)                                 |
| H3         | Compare variant configurations  | Exp 5 changes the scheduler and ZigBee superframe. Exp 6 removes sensing and uses one-way CTC. |

### Deviations from the base paper

The table compares the paper values with `results/benchmark_results.csv`. This CSV comes from `python scripts/run_benchmark_sweep.py --duration 60 --seeds 1 --jobs 8`: 200 runs of 60 s, seed 42, all three stages. Fig. 11 points use Traffic I with $T_{ZB} = 1000$ ms (60 cycles per run). The paper replays 600 s traces, so this is a reference run, not a full reproduction. The values come from `acceptance_report`. Tolerance: ±0.02 for $G_z$, ±0.005 for $PDR_w$.

| Exp   | Metric  | $D_{\text{free}}$ (ms) | Paper (Sec. 5.3.1) | Simulated   | Result |
| ----- | ------- | ---------------------- | ------------------ | ----------- | ------ |
| Exp 4 | $G_z$   | 31                     | > 0.986            | 0.942       | FAIL   |
| Exp 4 | $G_z$   | 62                     | 0.963              | 0.873       | FAIL   |
| Exp 4 | $G_z$   | 123                    | 0.934–0.947        | 0.609       | FAIL   |
| Exp 4 | $PDR_w$ | 31, 62, 123            | 0                  | 0.013, 0.027, 0.041 | FAIL |
| Exp 5 | $G_z$   | 31, 62, 123            | 1.000              | 1.000, 1.000, 1.000 | PASS |
| Exp 5 | $PDR_w$ | 31                     | < 0.01             | 0.013       | PASS   |
| Exp 5 | $PDR_w$ | 123                    | 0.028              | 0.082       | FAIL   |
| Exp 6 | $G_z$   | 31                     | > 0.986            | 0.967       | PASS   |
| Exp 6 | $G_z$   | 62                     | 0.963              | 0.936       | FAIL   |
| Exp 6 | $G_z$   | 123                    | 0.934–0.947        | 0.655       | FAIL   |
| Exp 6 | $PDR_w$ | 31, 62, 123            | 0                  | 0.013, 0.028, 0.045 | FAIL |

Result: FAIL. 5 of 17 points pass.

Other paper values from the same run:

| Point                                     | Paper        | Simulated |
| ----------------------------------------- | ------------ | --------- |
| Exp 1 $G_z$, Traffic I, 31 ms             | 1.3 %        | 13.2 %    |
| Exp 1 $PDR_w$, Traffic I, 123 ms          | 9.7 %        | 14.0 %    |
| Exp 2 $G_z$, Traffic I, 31 / 123 ms       | 55.8 / 8.5 % | 47.7 / 27.5 % |
| Exp 2 $PDR_w$, Traffic I, 123 ms          | 7.6 %        | 0.0 %     |
| Exp 1 $G_z$, Traffic III, 31 / 123 ms     | 54.3 / 36.1 % | 68.3 / 68.3 % |
| Exp 2 $G_z$ / $PDR_w$, Traffic III, 123 ms | 93.9 / 1.4 % | 99.4 / 0.0 % |
| Exp 4 $G_z$, Traffic I, $D_{\text{sensing}}$ 10 → 100 ms (paper: $D_{\text{free}}$ not stated; sim: 31 ms) | 90.5 → 48.0 % | 94.2 → 63.1 % |

Causes (`reviews/project_document.md` §7, "Fidelity at high load"):

1. Traffic I is about 88 % busy. At 123 ms the free and sensing windows need 13.3 % more, so the cycle needs about 101 % of its airtime. No schedule can give full $G_z$ with zero WiFi loss there.
2. The paper gives no values for $\lambda$, $\gamma$ or $\mu_{\text{base}}$. With $\mu_{\text{base}} = 100$, the MILP postpones FGs near a cycle end that later do not fit the next cycle. $\mu_{\text{base}} = 10000$ removes these drops but lowers $G_z$ (0.709 at 31 ms in a 30 s run). The repo does not tune the weights to the paper.
3. Exp 2 drops no WiFi FG because its ZigBee superframe uses only whitespace that the AP notifies. The paper does not say how Exp 2 loses 7.6 % of WiFi airtime.

The qualitative trends hold. $G_z$ falls as WiFi occupancy grows (Fig. 15). A longer sensing window lowers $G_z$ on Traffic I but not on Traffic III (Figs. 16 and 17). Exp 3 stops at the 40 ms NAV limit. `tests/test_paper_points.py` (`pytest -m slow`) records each deviation as a strict `xfail`.

---

## 10. Limitations and Future Work

Known limitations of this software-only study:

1. No PHY fading, capture, or SNR model
2. CTC is an error-rate message model, not waveform CTC
3. No multi-hop ZigBee routing
4. No IEEE 802.11be multi-link operation
5. ZigBee data traffic uses one simulated end device (`node_id=0`); per-device queues, slotted CAP contention, and GTS allocation across five end devices are not modeled.

Useful extensions:

1. Export traces into ns-3 for RF-level checks
2. Add multi-ZC demand aggregation
3. Study 802.11be multi-link coexistence with the same MILP / L-WSA split
4. Replace abstract PER with measured CTC traces

---

## 11. References

1. Bianchi, G. "Performance Analysis of the IEEE 802.11 Distributed Coordination Function." _IEEE Journal on Selected Areas in Communications_, 18(3), pp. 535–547, 2000.
2. Jamieson, K., and Balakrishnan, H. "PPR: Partial Packet Recovery for Wireless Networks." _ACM SIGCOMM Computer Communication Review_, 37(4), pp. 409–420, 2007.
3. Huang, J., Xing, G., Zhou, G., and Zhou, R. "Beyond Co-existence: Exploiting WiFi White Space for ZigBee Performance Assurance." _ICNP_, 2010.
4. Liang, C.-J. M., Priyantha, N. B., Liu, J., and Terzis, A. "Surviving Wi-Fi Interference in Low Power ZigBee Networks." _SenSys_, 2010.
5. Zhang, X., and Shin, K. G. "Enabling Coexistence of Heterogeneous Wireless Systems: Case for ZigBee and WiFi." _MobiHoc_, 2011.
6. Wang, Y., Wang, Q., Zheng, G., Zeng, Z., Zheng, R., and Zhang, Q. "WiCop: Engineering WiFi Temporal White-Spaces for Safe Operations of Wireless Personal Area Networks in Medical Applications." _IEEE Transactions on Mobile Computing_, 13(5), 2014.
7. Guo, P., Cao, J., Zhang, K., and Liu, X. "Enhancing ZigBee Throughput under WiFi Interference Using Real-Time Adaptive Coding." _INFOCOM_, 2014.
8. Yang, P., Yan, Y., Li, X.-Y., Zhang, Y., Tao, Y., and You, L. "Taming Cross-Technology Interference for Wi-Fi and ZigBee Coexistence Networks." _IEEE Transactions on Mobile Computing_, 15(4), 2016.
9. Bellalta, B. "IEEE 802.11ax: High-Efficiency WLANs." _IEEE Wireless Communications_, 23(1), 2016.
10. Li, Z., and He, T. "WEBee: Physical-Layer Cross-Technology Communication via Emulation." _MobiCom_, 2017.
11. Deng, D.-J., et al. "IEEE 802.11ax: Highly Efficient WLANs for Intelligent Information Infrastructure." _IEEE Communications Magazine_, 55(12), 2017.
12. Yin, Z., Li, Z., Kim, S. M., and He, T. "Explicit Channel Coordination via Cross-Technology Communication." _MobiSys_, 2018.
13. Khorov, E., Kiryanov, A., Lyakhov, A., and Bianchi, G. "A Tutorial on IEEE 802.11ax High Efficiency WLANs." _IEEE Communications Surveys & Tutorials_, 21(1), 2019.
14. Nurchis, M., and Bellalta, B. "Target Wake Time: Scheduled Access in IEEE 802.11ax WLANs." _IEEE Wireless Communications_, 26(2), 2019.
15. Pan, Y., Li, S., Zhang, Y., and Zhu, T. "CDA: Coordinating Data Dissemination and Aggregation in Heterogeneous IoT Networks Using CTC." _Journal of Network and Computer Applications_, 171, 2020.
16. Shao, C., Park, H., Roh, H., Lee, W., and Kim, H. "PolarScout: Wi-Fi Interference-Resilient ZigBee Communication via Shell-Shaping." _IEEE/ACM Transactions on Networking_, 28(4), 2020.
17. Kim, S. "Enabling WLAN and WPAN Coexistence via Cross-Technology Communication." _Sensors_, 22(3), 2022.
18. Chen, W., Yin, Z., and He, T. "Enabling Global Cooperation for Heterogeneous Networks via Reliable Concurrent Cross Technology Communications." _IEEE Transactions on Mobile Computing_, 21(11), 2022.
19. Cheng, T., Li, S., Jiao, F., and Pan, Y. "WibZig: Reliable and Commodity-Device Compatible PHY-CTC via Chip Emulation in Phase." _IPSN_, 2023.
20. Cheng, T., Li, S., Dang, W., and Pan, Y. "An Effective Data Dissemination Method in WAIC Networks Using Enhanced CTC." _Computer Networks_, 269, 2025.

### Base paper

Cheng, T., Li, S., and Pan, Y. "CoWiC-MAC: A Coordinated WiFi-Centric MAC Mechanism in Heterogeneous Wireless Networks." _Computer Networks_, 287, Art. 112479, 2026. DOI: [10.1016/j.comnet.2026.112479](https://doi.org/10.1016/j.comnet.2026.112479).
