# Implementation and Research Plan: CoWiC-MAC (Software-Only)

**Project:** CoWiC-MAC Case Study  
**Document:** `IMPLEMENTATION_PLAN.md`  
**Authors/Contributors:** Upayan Mazumder (24BDS0367), Nitesh Pandey (24BCB0141), Divyansh Saxena (24BCB0108)  
**Date:** 2026-10-09  
**Status:** Approved Blueprint for Simulation & Research

---

## 1. Project Understanding and Source Evidence

### 1.1 Base Paper Summary

- **Title:** CoWiC-MAC: A coordinated WiFi-centric MAC mechanism in heterogeneous wireless networks
- **Authors:** Tao Cheng, Shining Li, Yan Pan
- **Venue:** _Computer Networks_ (Elsevier), Vol. 287, Art. 112479, Sept. 2026. DOI: [10.1016/j.comnet.2026.112479](https://doi.org/10.1016/j.comnet.2026.112479)
- **Problem:** Uncoordinated Cross-Technology Interference (CTI) in the shared 2.4 GHz ISM band between dominant, high-power IEEE 802.11 (WiFi) and resource-constrained, low-power IEEE 802.15.4 (ZigBee). Traditional uncoordinated coexistence results in severe packet loss (>60% for ZigBee) and throughput loss (~20% for WiFi).
- **Assumptions:**
  1. The WiFi AP possesses computational capacity to solve optimization programs online.
  2. The WiFi AP and ZigBee Coordinator (ZC) support physical/cross-layer Cross-Technology Communication (CTC): WiCast (downlink WiFi→ZigBee via payload emulation) and SymBee (uplink ZigBee→WiFi via phase-shift symbol patterns).
  3. Collision abstraction: Temporal overlap of WiFi and ZigBee on the same 2.4 GHz channel causes mutual transmission failure.
  4. WiFi traffic is aggregated into Frame Groups (FGs) scheduled via 802.11ax Target Wake Time (TWT) or trigger frames.
- **Core Solution:** A two-stage coordinated closed loop:
  1. _WiFi Rescheduling:_ MILP-based reordering/deferral of WiFi FGs at the AP to create interference-free temporal windows ($D_\text{free}$) and a protected sensing window ($D_\text{sensing}$) for ZigBee demand reporting.
  2. _ZigBee Superframe Adaptation (L-WSA):_ Dynamic superframe partitioning (slot duration $t_\text{SfSlot}$, CAP slots $i$, CFP slots $j$, beacon offset $t_\text{offset}$) at the ZC to navigate unavoidable residual WiFi fragments.

### 1.2 Evolution Across Review Documents

- **F1 TF1 Casestudy.pdf:** Assignment guidelines requiring a 20-paper literature survey table and paragraph format, team registration, and unique base paper selection.
- **Review 1 (`CoWiC-MAC_Case_Study.md` / `.pdf`):** Formalized the 20-paper literature survey across 802.11 DCF, white-space sensing, CTC primitives (WEBee, WibZig, SymBee), and 802.11ax MAC features. Established the academic lineage connecting the authors' prior works (CDA, WibZig, WAIC-CTC).
- **Review 2 (`CoWiC-MAC_Case_Study.odt` / `.pdf`):** Transitioned from literature survey to architecture and mathematical formulation:
  - System architecture diagram defining the WiFi domain, ZigBee domain, and shared medium.
  - Component breakdown: AP orchestrator, MILP model, CTC link, ZC, L-WSA algorithm, STAs, ZigBee nodes.
  - Mathematical formalization of the two optimization problems: WiFi FG rescheduling (Equations 1–2) and ZigBee superframe adaptation (Equations 3–6).
  - Concrete procedural algorithms: Algorithm 1 (AP MILP) and Algorithm 2 (ZC L-WSA).

### 1.3 Reconciliation of Requirements & Discrepancies

1. **MILP Formulation Consistency:** In the base paper Appendix A, the MILP includes full Big-M linearization, inter-FG non-overlapping constraints, and sensing window protection $\sum_{k=1}^K z_k^\text{sense} = 0$. Review 2 presents the compact non-linear indicator objective. The implementation must adopt the linear MILP from Appendix A for numerical solver stability.
2. **ZigBee Efficiency Metric:** In Review 2 Equation 4, $\eta_\text{cap}^p = \frac{t_\text{pk} - t_\text{ovh}}{t_\text{min}}$ and $\eta_\text{cfp}^p = \frac{t_\text{pk} - t_\text{ovh}}{t_\text{SfSlot}}$. In the base paper, $t_\text{min} = 2 \cdot t_\text{BoSlot} + t_\text{pk}$, where $t_\text{BoSlot}$ is the backoff slot (20 symbols = 320 $\mu$s).
3. **CTC Link Modeling:** The base paper abstracts CTC decoding into periodic signaling frames (WBeacon and ZBeacon). In a software-only implementation without SDR hardware, CTC must be modeled via a discrete-event control message layer with parametrized packet error rate (PER) and decoding latency.

---

## 2. Research Scope, Questions, and Hypotheses

### 2.1 Primary Research Question

> Can proactive, joint WiFi-centric frame group rescheduling and superframe adaptation achieve zero WiFi frame group loss and >90% ZigBee transmission gain under heavy 2.4 GHz traffic in a reproducible, software-only simulation?

### 2.2 Measurable Hypotheses

- **Hypothesis 1 (ZigBee Transmission Gain):** Under saturated WiFi traffic (occupancy > 70%), CoWiC-MAC achieves ZigBee transmission gain $G_z \ge 90\%$, whereas legacy uncoordinated CSMA/CA achieves $G_z < 10\%$.
- **Hypothesis 2 (WiFi QoS Invariance):** CoWiC-MAC maintains zero WiFi Frame Group drop rate ($PDR_w = 0\%$) by dynamically penalizing FG deferral according to strict delay deadlines, bounding maximum FG delay to $< 200$ ms.
- **Hypothesis 3 (Ablation Separation):** Removing the ZigBee L-WSA adaptive superframe (Exp 3/Exp 5) forces the system to either drop WiFi frames ($PDR_w > 0$) or leave residual collisions, demonstrating that bi-directional synergy is mandatory under fragmented whitespaces.

---

## 3. Software-Only Scope & Technical Foundation

### 3.1 Implementation Stack Comparison

| Criterion                | Pure Python (SimPy + PuLP)                     | ns-3 (C++ / Python API)                     | OMNeT++ / INET                           |
| ------------------------ | ---------------------------------------------- | ------------------------------------------- | ---------------------------------------- |
| **Protocol Fidelity**    | High for MAC/scheduling logic; abstract PHY    | Full 802.11ax & 802.15.4 PHY/MAC models     | Full PHY/MAC, complex C++ architecture   |
| **MILP Integration**     | Native (`PuLP` + HiGHS)                        | External C++ solver binding required        | External C++ solver binding required     |
| **Trace Playback**       | Native pandas/numpy streaming                  | Custom TraceSource / App generation         | Custom message generators                |
| **Laptop Feasibility**   | Runs in seconds/minutes, zero compile overhead | Heavy compile times, high memory footprint  | Heavy setup and configuration            |
| **Team Maintainability** | Clean Python 3.11+, modular dataclasses        | Complex debugging across C++ pointer layers | Steep learning curve for OMNeT NED files |

### 3.2 Recommended Stack: Dual-Layer Python Architecture

- **Core Engine:** **SimPy** (discrete-event simulation) + **PuLP / HiGHS** (MILP solver).
- **Rationale:** The base paper explicitly states that the MAC-layer evaluation is driven by real-world WiFi traffic traces aggregated at the Frame Group level, with temporal collision logic. A dedicated SimPy discrete-event framework implements the paper's MILP (Eq. 11-17) and L-WSA search space directly without the unnecessary compilation and integration friction of ns-3 C++ Spectrum modules.
- **Secondary / Extension:** Standardized trace formats exportable to ns-3 scripts if low-level RF fading validation is later required.

---

## 4. Modular Codebase Architecture

```text
cowicmac-case-study/
├── pyproject.toml                     # Python dependencies & build config
├── README.md                          # Quickstart & replication instructions
├── configs/                           # Experiment configuration presets
│   ├── base_traffic1.yaml             # Traffic I profile (densest, Table 5)
│   ├── base_traffic2.yaml             # Traffic II profile
│   ├── base_traffic3.yaml             # Traffic III profile
│   ├── base_traffic4.yaml             # Traffic IV profile (lightest)
│   ├── exp1_uncoordinated.yaml        # Baseline Exp 1
│   ├── exp2_opportunistic.yaml        # Opportunistic Exp 2
│   ├── exp3_ecc.yaml                  # ECC Scheme Exp 3
│   ├── exp4_cowic_full.yaml           # Full CoWiC-MAC Exp 4
│   ├── exp5_rigid_vacate.yaml         # Ablation Exp 5
│   └── exp6_overhead_free.yaml        # Ablation Exp 6
├── traces/                            # WiFi traffic traces
│   ├── synthetic_generator.py         # Trace generator matching Table 5
│   ├── playback.py                    # Trace loading and per-cycle replay
│   └── data/                          # Generated traces (.parquet, not committed)
├── scripts/
│   ├── run_benchmark_sweep.py         # Exp 1-6 sweep over D_free, T_ZB, traffic, seed
│   └── build_docs.py                  # Builds the review PDFs
├── src/cowic/
│   ├── exceptions.py                  # CoWiCError hierarchy
│   ├── models/
│   │   ├── frame_group.py             # WiFi Frame Group and scheduling decision
│   │   └── superframe.py              # ZigBee superframe configuration and packets
│   ├── wifi/
│   │   ├── ap_orchestrator.py         # AP scheduling loop & state machine
│   │   ├── milp_scheduler.py          # PuLP MILP formulation (Equations 11-17)
│   │   ├── baselines.py               # Exp 1-3 schedulers (static, gap search, ECC)
│   │   └── fallback.py                # Lightweight whitespace-expansion scheduler
│   ├── zigbee/
│   │   ├── coordinator.py             # ZC state machine & demand reporting
│   │   └── lwsa_adapter.py            # L-WSA discrete search (Eq. 6-10)
│   ├── ctc/
│   │   └── channel.py                 # WBeacon/ZBeacon exchange & PER modeling
│   ├── sim/
│   │   ├── __main__.py                # `python -m cowic.sim --config ...`
│   │   ├── channel_medium.py          # Shared 2.4 GHz medium collision evaluator
│   │   └── simulation_engine.py       # SimPy execution environment
│   └── analysis/
│       ├── __main__.py                # `python -m cowic.analysis --results-csv ...`
│       ├── metrics.py                 # G_z, PDR_w, U_eff, Jitter, Moved FGs, Max Delay
│       └── plotter.py                 # Figs. 11-14 and 16 (Figs. 15 and 17: #45)
└── tests/                             # 13 test modules, one per component plus
                                       # end-to-end, sweep, and CLI tests
```

---

## 5. Protocol Specification & Central Algorithms

### 5.1 WiFi Frame Group MILP Model (Linear Formulation)

The AP optimizes scheduled start times $s'_k$ and window placements:

```math
\min \sum_{k=1}^K z_k^\text{free} d_k
+ \gamma \sum_{k=1}^K z_k^\text{sense} d_k
+ \lambda \sum_{k=1}^K y_k
+ \sum_{k=1}^K \mu_k P_k
```

with the deadline-aware postponement cost of Eq. 2,

```math
\mu_k = \frac{\mu_\text{base}}{\max\left(\epsilon,\ e_k - (T_\text{end} + d_k)\right)}
```

(`mu_base = 100`, $\epsilon$ = 1 µs in `milp_scheduler.py`).

Subject to:

0. Window placement (Eq. 3/13): $T^\text{end}_\text{free} = T^\text{start}_\text{free} + D_\text{free}$, $T^\text{end}_\text{sensing} = T^\text{start}_\text{sensing} + D_\text{sensing}$, both inside $[T_\text{start}, T_\text{end}]$, and non-overlapping through the binary $b_\text{ws}$: $T^\text{end}_\text{free} \le T^\text{start}_\text{sensing} + M(1 - b_\text{ws})$ and $T^\text{end}_\text{sensing} \le T^\text{start}_\text{free} + M b_\text{ws}$.
1. $y_k \ge s'_k - s_k$, $y_k \ge 0$ (Delay penalty).
2. $a_k \le s'_k + M P_k$ (Causality).
3. $s'_k + d_k \le e_k + M P_k$ and $s'_k + d_k \le T_\text{end} + M P_k$ (Deadlines & cycle bounds).
4. $T_\text{end} + d_k \le e_k + M (1 - P_k)$ (Feasibility of postponed FGs in next cycle).
5. Inter-FG conflict avoidance via Big-M ordering variables $b_{kj}$.
6. Overlap indicators $z_k^\text{free}$ and $z_k^\text{sense}$. The code does not copy Appendix A's Eq. 16 literally: as extracted, it forces $z_k = 1$ for an FG that does *not* overlap the window, which contradicts $\sum_k z_k^\text{sense} = 0$. It uses a disjunctive encoding instead: if $z_k = 0$, the FG lies entirely before or entirely after the window (side chosen by a binary $w_k$).
7. Strict protection of sensing window: $\sum_{k=1}^K z_k^\text{sense} = 0$.

### 5.2 ZigBee Superframe Adaptation (L-WSA Search)

The ZC maximizes:

```math
\eta_\text{total}
= m \cdot t_\text{min} \cdot \eta_\text{cap}^p
+ \alpha \cdot n \cdot t_\text{SfSlot} \cdot \eta_\text{cfp}^p
```

Over discrete parameters:

- $t_\text{SfSlot} \in \{t_\text{slot}^\text{base} \cdot 2^{SO}\}$, where $SO \in [0, 14]$.
- $j \in [0, 7]$ CFP slots and $i$ CAP slots, with $i + j \le 15$.
- $t_\text{offset} \in [0, \text{whitespace duration}]$.
- Filter condition: $(1 + i + j) \cdot t_\text{SfSlot} \le dur_\text{end} - dur_\text{start}$.
- Slot count $m, n$: Slots evaluated against residual WiFi fragment intervals $\mathcal{T}^\text{WiFi}$.

---

## 6. Evaluation Plan & Benchmarking

### 6.1 Experimental Groups (Matching Table 4)

- **Exp 1 (Static Baseline):** Uncoordinated standard CSMA/CA on both WiFi and ZigBee.
- **Exp 2 (Opportunistic Whitespace):** AP senses natural idle gaps and notifies ZigBee; no WiFi rescheduling.
- **Exp 3 (ECC Scheme):** Standard NAV-based bidirectional reservation; fixed ZigBee superframe.
- **Exp 4 (Full CoWiC-MAC):** Soft-constraint MILP scheduling + L-WSA superframe adaptation + 10 ms sensing window.
- **Exp 5 (Rigid WiFi-Vacate):** Hard constraint (all overlapping WiFi FGs dropped/postponed); fixed ZigBee superframe.
- **Exp 6 (Overhead-Free CoWiC):** Soft-constraint MILP + L-WSA without sensing window overhead.

### 6.2 Key Metrics & Evaluation Dimensions

1. **ZigBee Transmission Gain ($G_z$):** Delivered ZigBee payload divided by theoretical maximum in clean spectrum.
2. **WiFi FG Drop Rate ($PDR_w$):** Ratio of discarded WiFi airtime duration to total requested duration.
3. **Effective Spectrum Utilization** ($U_\text{eff}$): Proportion of cycle occupied by successful WiFi, ZigBee, and control airtime.
4. **WiFi Delay Overhead:** Jitter (ms), number of moved FGs, and maximum delay (ms).
5. **Evaluated Parameter Sweeps:**
   - $D_\text{free} \in [31, 62, 93, 123]$ ms.
   - ZigBee superframe period $T_{ZB} \in [1000, 2000, 5000]$ ms.
   - Traffic intensities: Traffic I (high load, about 88 % occupancy: 39.2 ms FGs, 5.3 ms whitespace) to Traffic IV (light load).
   - Sensing window duration $D_\text{sensing} \in [10, 20, 50, 100]$ ms.

---

## 7. Contributor Workstreams & Milestones

### 7.1 Team Assignments and Presentation Ownership

Presentation ownership matches Review 2:

- **Upayan Mazumder (`upayanmazumder`) — Central architecture / Lead:**
  - Simulation engine and channel medium (`src/cowic/sim/`).
  - MILP scheduling engine (`src/cowic/wifi/milp_scheduler.py`).
  - L-WSA and ZigBee coordinator (`src/cowic/zigbee/`).
  - Figure plotter and integration (`src/cowic/analysis/plotter.py`).
- **Nitesh Pandey (`Nitesh050`) — WiFi side:**
  - Trace generation and playback (`traces/`).
  - AP orchestrator, baselines, and fallback (`src/cowic/wifi/`).
  - CTC channel and metrics (`src/cowic/ctc/channel.py`, `src/cowic/analysis/metrics.py`).
  - Benchmark sweep runner (`scripts/run_benchmark_sweep.py`).
- **Divyansh Saxena (`DIVYANSHSAXENA23`) — ZigBee side:**
  - ZigBee architecture and L-WSA specification review.
  - Superframe design input.
  - Test-suite design and verification planning.

### 7.2 Milestone Roadmap

- **Milestone 1 (Foundation & Trace Engine):** Establish trace generator matching Table 5 statistical distributions and basic SimPy collision medium.
- **Milestone 2 (Optimization Modules):** Implement and unit-test PuLP MILP scheduler and L-WSA discrete search.
- **Milestone 3 (End-to-End Simulation):** Integrate AP orchestrator, ZC adapter, and CTC exchange for Exp 1 and Exp 4 on Traffic I.
- **Milestone 4 (Full Benchmark & Ablations):** Execute Exp 1–6 across all traffic profiles, generate replication plots (Figs 11–14 and 16; Figs 15 and 17 are tracked in #45).
- **Milestone 5 (Paper & Case Study Delivery):** Draft final case study report mapping quantitative results to original base paper claims.

---

## 8. Paper Outline & Claim-to-Evidence Mapping

1. **Introduction:** Problem statement, limitations of opportunistic coexistence, overview of CoWiC-MAC.
2. **Related Work:** Survey of 20 papers covering DCF, white-space sensing, CTC, and 802.11ax MAC.
3. **System Architecture & Models:** Two-stage optimization, MILP formulation, L-WSA discrete search, CTC link.
4. **Simulation Methodology:** Trace-driven SimPy setup, traffic categorization, parameter sweeps.
5. **Evaluation & Discussion:**
   - _Claim 1:_ Zero WiFi frame drop rate ($PDR_w = 0$) verified via Exp 4 delay-aware postponement.
   - _Claim 2:_ ZigBee gain $G_z > 90\%$ under heavy load verified via Fig. 11 replication.
   - _Claim 3:_ L-WSA necessity confirmed via Exp 5/6 ablations.
6. **Conclusion & Future Directions:** Software-only validation summary and path toward 802.11be multi-link operation.

---

## 9. Immediate First Implementation Steps

1. Initialize virtual environment and install dependencies (`simpy`, `pulp`, `numpy`, `pandas`, `pyyaml`, `matplotlib`, `pytest`).
2. Implement `traces/synthetic_generator.py` to produce reproducible Frame Group sequences matching Table 5.
3. Build `src/cowic/wifi/milp_scheduler.py` and run deterministic unit tests with HiGHS.
