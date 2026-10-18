# CoWiC-MAC: Project Technical Document

**Project:** CoWiC-MAC Case Study  
**Document:** `reviews/project_document.md`  
**Authors:** Upayan Mazumder (24BDS0367), Nitesh Pandey (24BCB0141), Divyansh Saxena (24BCB0108)  
**Base paper:** Cheng, T., Li, S., and Pan, Y. "CoWiC-MAC: A Coordinated WiFi-Centric MAC Mechanism in Heterogeneous Wireless Networks." _Computer Networks_, Vol. 287, Art. 112479, Sept. 2026. DOI: [10.1016/j.comnet.2026.112479](https://doi.org/10.1016/j.comnet.2026.112479)

---

## 1. Problem Statement

WiFi (IEEE 802.11) and ZigBee (IEEE 802.15.4) share the 2.4 GHz ISM band. They use different MAC protocols. They cannot negotiate channel access with each other.

Uncoordinated operation causes Cross-Technology Interference (CTI). The base paper reports ZigBee packet loss above 60% and WiFi throughput loss near 20% under heavy shared-channel load.

CoWiC-MAC solves this with a WiFi-centric coordination loop:

1. The WiFi Access Point (AP) reschedules Frame Groups (FGs) with Mixed-Integer Linear Programming (MILP).
2. The ZigBee Coordinator (ZC) adapts its superframe with the Lightweight Window-Scaling Algorithm (L-WSA).
3. The two networks exchange control messages over Cross-Technology Communication (CTC).

This project builds a software-only simulation of that loop. It uses no radio hardware.

---

## 2. System Architecture

The system has three domains: the WiFi domain, the ZigBee domain, and the shared 2.4 GHz medium.

```mermaid
flowchart TB
  AP["WiFi AP<br/>MILP scheduler"] <-->|CTC| ZC["ZigBee Coordinator<br/>L-WSA"]
  AP --> CH["Shared 2.4 GHz channel"]
  ZC --> CH
  CH --> MET["Metrics calculator"]
```

### End-to-end coordination cycle

1. The ZC reports demand with a ZBeacon (`d_free_ms`).
2. The AP solves the MILP and places a free window and a separate sensing window. The two windows do not overlap (Eq. 3).
3. The AP sends a WBeacon with window bounds.
4. The ZC runs L-WSA and builds a superframe that fits the free window.
5. Both sides transmit on the shared medium.
6. The channel medium records each transmission and marks overlaps as collisions. The metrics module reads these records after the run.

### ZigBee device scope

The current simulator represents ZigBee data traffic as one end device (`node_id=0`) coordinated by one ZC. It does not model five per-device queues, slotted CAP contention between devices, or per-device GTS allocation. The shared-medium collision model still detects overlaps between the simulated WiFi and ZigBee transmissions.

---

## 3. WiFi Domain

### 3.1 Frame Groups

A Frame Group (FG) is a batch of WiFi frames that the AP schedules as one unit. This matches the 802.11ax Target Wake Time (TWT) and trigger-frame model in the base paper.

Main fields:

| Field               | Meaning                          |
| ------------------- | -------------------------------- |
| `fg_id`             | Unique identifier                |
| `arrival_time_ms`   | Earliest time the FG may start   |
| `duration_ms`       | Airtime duration                 |
| `deadline_ms`       | Latest finish time               |
| `original_start_ms` | Start time before rescheduling   |
| `qos_sensitive`     | Flag for delay-sensitive traffic |

### 3.2 MILP Scheduler (Equations 11-17)

The AP solves this programme each cycle:

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

Decision variables:

- $T_{\text{free}}^{\text{start}}, T_{\text{sensing}}^{\text{start}}$ — window start times
- $b_{\text{ws}}$ — window-ordering binary: which window comes first
- $s'_k$ — scheduled start time of FG $k$
- $y_k$ — delay relative to the original start
- $z_k^{\text{free}}, z_k^{\text{sense}}$ — overlap indicators with free and sensing windows
- $P_k$ — postpone binary (defer into the next cycle)
- $b_{kj}$ — ordering binary between FG pairs

Constraint groups:

0. Window placement (Eq. 3/13): both windows lie inside the cycle and do not overlap. $T_{\text{free}}^{\text{end}} \le T_{\text{sensing}}^{\text{start}} + M(1 - b_{\text{ws}})$ and $T_{\text{sensing}}^{\text{end}} \le T_{\text{free}}^{\text{start}} + M b_{\text{ws}}$
1. Delay definition: $y_k \ge s'_k - s_k$, $y_k \ge 0$
2. Causality: $a_k \le s'_k + M P_k$
3. Deadline and cycle bound: $s'_k + d_k \le e_k + M P_k$ and $s'_k + d_k \le T_{\text{end}} + M P_k$
4. Feasibility of postponed FGs: $T_{\text{end}} + d_k \le e_k + M(1 - P_k)$
5. Inter-FG non-overlap via Big-M ordering binaries $b_{kj}$
6. Overlap indicators $z_k^{\text{free}}$ and $z_k^{\text{sense}}$. Eq. 16, as printed, forces $z_k = 1$ for an FG that does not overlap the window, which contradicts constraint 7. The code uses a disjunctive form instead: if $z_k = 0$, the FG lies entirely before or entirely after the window
7. Sensing window protection: $\sum_{k=1}^{K} z_k^{\text{sense}} = 0$

Solver: PuLP with HiGHS (`highspy`). A custom PuLP solver can be supplied; if the MILP has no optimal solution, the AP uses whitespace expansion fallback.

### 3.3 AP Orchestrator State Machine

```mermaid
stateDiagram-v2
  [*] --> MAINTAINING
  MAINTAINING --> CTC_GATHERING: start cycle
  CTC_GATHERING --> RESCHEDULING: ZBeacon received / timeout
  RESCHEDULING --> CTC_NOTIFYING: schedule ready
  CTC_NOTIFYING --> TRANSMITTING: WBeacon sent
  TRANSMITTING --> MAINTAINING: cycle complete
```

States:

| State           | Action                |
| --------------- | --------------------- |
| `MAINTAINING`   | Idle between cycles   |
| `CTC_GATHERING` | Collect ZigBee demand |
| `RESCHEDULING`  | Run MILP or fallback  |
| `CTC_NOTIFYING` | Send WBeacon          |
| `TRANSMITTING`  | Drive WiFi airtime    |

### 3.4 Baseline Experiments (Exp 1-3)

| Exp   | Name          | Behaviour                                                                                                    |
| ----- | ------------- | ------------------------------------------------------------------------------------------------------------ |
| Exp 1 | Uncoordinated | No CTC. No rescheduling. ZigBee uses a fixed superframe at the cycle start. Carrier sensing is not modelled. |
| Exp 2 | Opportunistic | AP senses natural idle gaps. No WiFi rescheduling.                                                           |
| Exp 3 | ECC           | NAV-based reservation. Fixed ZigBee superframe.                                                              |

---

## 4. ZigBee Domain

### 4.1 IEEE 802.15.4 Superframe

A beacon-enabled superframe has:

- 1 beacon slot
- $i$ Contention Access Period (CAP) slots
- $j$ Contention Free Period (CFP) slots

Hard limits from the standard and the base paper:

- $0 \le j \le 7$
- $i + j \le 15$
- Total active slots: $1 + i + j$

IEEE 802.15.4 2.4 GHz O-QPSK PHY constants used in this project:

| Constant            | Value                  |
| ------------------- | ---------------------- |
| Symbol duration     | 0.016 ms               |
| Base slot duration  | 0.96 ms (60 symbols)   |
| Backoff slot        | 0.32 ms (20 symbols)   |
| Turnaround time     | 0.192 ms (12 symbols)  |
| ACK duration        | 0.352 ms (22 symbols)  |
| Max packet duration | 4.256 ms (266 symbols: 127-byte PSDU plus 6-byte SHR/PHR) |

### 4.2 L-WSA Algorithm (Algorithm 2)

The ZC maximises:

```math
\eta_{\text{total}}
= m \cdot t_{\min} \cdot \eta_{\text{cap}}^{p}
+ \alpha \cdot n \cdot t_{\text{SfSlot}} \cdot \eta_{\text{cfp}}^{p}
```

Search variables:

| Variable            | Domain                                                                |
| ------------------- | --------------------------------------------------------------------- |
| $t_{\text{SfSlot}}$ | `BASE_SLOT * 2^SO`, $SO \in [0, 14]$                                  |
| $j$ (CFP slots)     | $[0, 7]$                                                              |
| $i$ (CAP slots)     | $i + j \le 15$                                                        |
| $t_{\text{offset}}$ | Window start, or end of each residual WiFi fragment inside the window |

Filter: $(1 + i + j) \cdot t_{\text{SfSlot}} \le$ free-window duration. Each candidate slot must avoid residual WiFi fragments $\mathcal{T}^{\text{WiFi}}$. CAP attempts are counted at $t_{\min}$ stride.

### 4.3 ZigBee Coordinator State Machine

```mermaid
stateDiagram-v2
  [*] --> IDLE
  IDLE --> CTC_REPORTING: demand ready
  CTC_REPORTING --> IDLE: ZBeacon sent
  IDLE --> COMMAND_ACQUISITION: WBeacon received
  COMMAND_ACQUISITION --> TRAFFIC_ADJUSTMENT: window parsed
  TRAFFIC_ADJUSTMENT --> SUPERFRAME_ACTIVE: L-WSA done
  SUPERFRAME_ACTIVE --> CTC_REPORTING: next demand report
```

---

## 5. Cross-Technology Communication

CTC lets WiFi and ZigBee exchange control messages without a shared MAC.

| Message | Direction | Payload keys                                                             |
| ------- | --------- | ------------------------------------------------------------------------ |
| ZBeacon | ZC → AP   | `d_free_ms`                                                              |
| WBeacon | AP → ZC   | `t_free_start_ms`, `t_free_end_ms`, `t_sense_start_ms`, `t_sense_end_ms` |

Physical-layer CTC primitives in the base paper:

- **WiCast** — WiFi → ZigBee downlink by payload emulation
- **SymBee** — ZigBee → WiFi uplink by phase-shift symbol patterns

This software model abstracts CTC as discrete-event messages with a configurable packet error rate (PER). It does not emulate RF waveforms.

---

## 6. Shared Channel Model

`ChannelMedium` records every WiFi and ZigBee transmission interval. A collision occurs when intervals overlap beyond `OVERLAP_TOLERANCE_MS`.

The medium stores one record per transmission: protocol, start time, end time, and a success flag.

The metrics module computes airtime, delay, jitter, and drops after the run. It uses these records and the WiFi scheduling decisions of the engine.

---

## 7. Experiments

| Exp   | Config                    | What it tests           | Fixed parts                          |
| ----- | ------------------------- | ----------------------- | ------------------------------------ |
| Exp 1 | `exp1_uncoordinated.yaml` | Uncoordinated baseline  | No CTC, no MILP, no L-WSA            |
| Exp 2 | `exp2_opportunistic.yaml` | Natural whitespace only | No WiFi rescheduling                 |
| Exp 3 | `exp3_ecc.yaml`           | NAV-style ECC           | Fixed ZigBee superframe              |
| Exp 4 | `exp4_cowic_full.yaml`    | Full CoWiC-MAC          | Soft MILP + L-WSA + sensing window   |
| Exp 5 | `exp5_rigid_vacate.yaml`  | Hard-constraint vacate  | Hard MILP + fixed ZigBee superframe  |
| Exp 6 | `exp6_overhead_free.yaml` | Overhead-free variant   | Soft MILP + L-WSA; no sensing window |

Exp 6 also changes CTC from bidirectional to WiFi-to-ZigBee.

### Parameter sweep

| Parameter            | Values                                              |
| -------------------- | --------------------------------------------------- |
| $D_{\text{free}}$    | 31, 62, 93, 123 ms                                  |
| $T_{ZB}$             | 1000, 2000, 5000 ms                                 |
| Traffic profiles     | Traffic I and III by default; I–IV with `--traffic` |
| $D_{\text{sensing}}$ | 10, 20, 50, 100 ms                                  |
| Seeds                | 5 per point                                         |

**Acceptance criterion (Issue #11):** Exp 4 at $D_{\text{free}} = 31$ ms and $T_{ZB} = 1000$ ms must achieve $G_z > 0.9$ and $PDR_w = 0$.

---

## 8. Key Metrics

| Metric           | Definition                                                      |
| ---------------- | --------------------------------------------------------------- |
| $G_z$            | ZigBee delivered payload / theoretical clean-spectrum capacity  |
| $PDR_w$          | Discarded WiFi FG duration / total requested WiFi duration      |
| $U_{\text{eff}}$ | Successful WiFi + ZigBee + control airtime / simulated duration |
| Jitter           | Variation of WiFi FG delay                                      |
| Max delay        | Largest WiFi FG delay in the run                                |

### Hypotheses under test

1. Under heavy load, CoWiC-MAC achieves $G_z \ge 90\%$.
2. CoWiC-MAC keeps $PDR_w = 0$ by deadline-aware postponement.
3. Exp 5 changes the WiFi policy and ZigBee superframe. Exp 6 removes sensing and uses one-way WiFi-to-ZigBee CTC.

---

## 9. Software Stack

| Layer        | Choice                      |
| ------------ | --------------------------- |
| Language     | Python 3.11+                |
| DES engine   | SimPy                       |
| MILP         | PuLP + HiGHS (`highspy`)    |
| Data         | NumPy, Pandas, PyYAML       |
| Plots        | Matplotlib                  |
| Tests        | pytest (263 tests)          |
| Lint / types | ruff, mypy                  |

---

## 10. Presentation Ownership

| Presenter       | Domain               |
| --------------- | -------------------- |
| Upayan Mazumder | Central architecture |
| Nitesh Pandey   | WiFi side            |
| Divyansh Saxena | ZigBee side          |

---

## 11. References

1. Bianchi, G. "Performance Analysis of the IEEE 802.11 Distributed Coordination Function." _IEEE JSAC_, 18(3), 2000.
2. Jamieson, K., and Balakrishnan, H. "PPR: Partial Packet Recovery for Wireless Networks." _ACM SIGCOMM CCR_, 37(4), 2007.
3. Huang, J., Xing, G., Zhou, G., and Zhou, R. "Beyond Co-existence: Exploiting WiFi White Space for ZigBee Performance Assurance." _ICNP_, 2010.
4. Liang, C.-J. M., Priyantha, N. B., Liu, J., and Terzis, A. "Surviving Wi-Fi Interference in Low Power ZigBee Networks." _SenSys_, 2010.
5. Zhang, X., and Shin, K. G. "Enabling Coexistence of Heterogeneous Wireless Systems: Case for ZigBee and WiFi." _MobiHoc_, 2011.
6. Wang, Y., et al. "WiCop: Engineering WiFi Temporal White-Spaces for Safe Operations of Wireless Personal Area Networks in Medical Applications." _IEEE TMC_, 13(5), 2014.
7. Guo, P., Cao, J., Zhang, K., and Liu, X. "Enhancing ZigBee Throughput under WiFi Interference Using Real-Time Adaptive Coding." _INFOCOM_, 2014.
8. Yang, P., et al. "Taming Cross-Technology Interference for Wi-Fi and ZigBee Coexistence Networks." _IEEE TMC_, 15(4), 2016.
9. Bellalta, B. "IEEE 802.11ax: High-Efficiency WLANs." _IEEE Wireless Commun._, 23(1), 2016.
10. Li, Z., and He, T. "WEBee: Physical-Layer Cross-Technology Communication via Emulation." _MobiCom_, 2017.
11. Deng, D.-J., et al. "IEEE 802.11ax: Highly Efficient WLANs for Intelligent Information Infrastructure." _IEEE Commun. Mag._, 55(12), 2017.
12. Yin, Z., Li, Z., Kim, S. M., and He, T. "Explicit Channel Coordination via Cross-Technology Communication." _MobiSys_, 2018.
13. Khorov, E., Kiryanov, A., Lyakhov, A., and Bianchi, G. "A Tutorial on IEEE 802.11ax High Efficiency WLANs." _IEEE Commun. Surveys & Tutorials_, 21(1), 2019.
14. Nurchis, M., and Bellalta, B. "Target Wake Time: Scheduled Access in IEEE 802.11ax WLANs." _IEEE Wireless Commun._, 26(2), 2019.
15. Pan, Y., Li, S., Zhang, Y., and Zhu, T. "CDA: Coordinating Data Dissemination and Aggregation in Heterogeneous IoT Networks Using CTC." _JNCA_, 171, 2020.
16. Shao, C., et al. "PolarScout: Wi-Fi Interference-Resilient ZigBee Communication via Shell-Shaping." _IEEE/ACM ToN_, 28(4), 2020.
17. Kim, S. "Enabling WLAN and WPAN Coexistence via Cross-Technology Communication." _Sensors_, 22(3), 2022.
18. Chen, W., Yin, Z., and He, T. "Enabling Global Cooperation for Heterogeneous Networks via Reliable Concurrent Cross Technology Communications." _IEEE TMC_, 21(11), 2022.
19. Cheng, T., Li, S., Jiao, F., and Pan, Y. "WibZig: Reliable and Commodity-Device Compatible PHY-CTC via Chip Emulation in Phase." _IPSN_, 2023.
20. Cheng, T., Li, S., Dang, W., and Pan, Y. "An Effective Data Dissemination Method in WAIC Networks Using Enhanced CTC." _Computer Networks_, 269, 2025.

**Base paper:** Cheng, T., Li, S., and Pan, Y. "CoWiC-MAC: A Coordinated WiFi-Centric MAC Mechanism in Heterogeneous Wireless Networks." _Computer Networks_, 287, Art. 112479, 2026.
