# CoWiC-MAC Case Study

Software-only simulation and evaluation of the **CoWiC-MAC** protocol (Cheng et al., _Computer Networks_ 2026).

## Overview

This project studies wireless coexistence between IEEE 802.11 (WiFi) and IEEE 802.15.4 (ZigBee) on the shared 2.4 GHz ISM band. The WiFi Access Point (AP) reschedules WiFi Frame Groups (FGs) using Mixed-Integer Linear Programming (MILP). The ZigBee Coordinator (ZC) adapts its superframe structure using the Lightweight Window-Scaling Algorithm (L-WSA). The two networks communicate over a Cross-Technology Communication (CTC) link.

## Team

| Name            | Registration Number | GitHub             |
| --------------- | ------------------- | ------------------ |
| Upayan Mazumder | 24BDS0367           | `upayanmazumder`   |
| Nitesh Pandey   | 24BCB0141           | `Nitesh050`        |
| Divyansh Saxena | 24BCB0108           | `DIVYANSHSAXENA23` |

See [Contributors](CONTRIBUTORS.md) for module and presentation ownership.

## Repository Structure

```text
cowicmac-case-study/
├── pyproject.toml              # Build system, dependencies, tool configuration
├── README.md                   # Project overview and quickstart guide
├── CONTRIBUTORS.md             # Contributor roles and module ownership
├── IMPLEMENTATION_PLAN.md      # Detailed system specification and formulas
├── configs/                    # Experiment configuration presets (Exp 1 to Exp 6)
├── traces/                     # Synthetic trace generator and trace playback
├── scripts/                    # Benchmark sweep runner and document builder
├── src/cowic/                  # Core protocol and simulation package
│   ├── models/                 # Dataclass models (FrameGroup, SuperframeConfig)
│   ├── wifi/                   # AP orchestrator, MILP scheduler, Exp 1-3 baselines, fallback
│   ├── zigbee/                 # ZigBee coordinator and L-WSA adapter
│   ├── ctc/                    # Cross-technology signaling channel model
│   ├── sim/                    # SimPy engine, collision medium, `python -m cowic.sim`
│   └── analysis/               # Metrics, figure plotter, `python -m cowic.analysis`
├── tests/                      # Automated unit and integration test suite
└── reviews/                    # Course review documents and base paper PDF
```

Read the [project document](reviews/project_document.md) and the [presentation guides](reviews/README.md).

## Quickstart

### Installation

Install dependencies using Python 3.11 or newer:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

Run the full test suite:

```bash
pytest
```

`pytest` skips the slow paper comparison. Run it on its own (about 2 min on 16 cores):

```bash
pytest -m slow
```

It compares 30 s runs with each $G_z$ and $PDR_w$ value of paper Sec. 5.3.1. Known deviations are strict `xfail` entries with their measured cause.

Run linter, format, and type checks:

```bash
ruff check .
ruff format --check .
mypy src/ tests/
```

CI also fails if test coverage of `src/cowic` drops below 85 %.

### Running Experiments

Generate a synthetic traffic trace:

```bash
python -m traces.synthetic_generator --profile Traffic_I --output traces/data/traffic1.parquet
```

Run one experiment and print its metrics (add `--duration 10` for a shorter run, `--json` for JSON output):

```bash
python -m cowic.sim --config configs/exp4_cowic_full.yaml
```

The CLI and sweep report fallback cycles, unproven MILP cycles, missed sensing
windows, and deadline-dropped frame groups. A feasible MILP result with unproven
drops uses the fallback instead. When a cycle is too short for the full rigid
reservation, the fallback protects sensing first and reports a truncated grant.
ZigBee gain compares delivered payload with demand in a clean window each cycle.
An empty grant still counts as unmet demand.

Run the benchmark sweep (quick smoke run: 3 s runs, one seed, small grid):

```bash
python scripts/run_benchmark_sweep.py --quick
```

Run the full benchmark sweep:

```bash
python scripts/run_benchmark_sweep.py --output-csv results/benchmark_results.csv
```

The full sweep replays 600 s of trace per run (`--duration`, default 600). It warns when a run has fewer than 20 ZigBee cycles. It has three stages:

- Figs. 11–14: Exp 1–6 against $D_{free}$ for each traffic profile and $T_{ZB}$.
- Fig. 15: Exp 1–6 at $T_{ZB}$ = 1000 ms and $D_{free}$ = 63 ms on Traffic I–IV, against the WiFi occupancy of each trace.
- Figs. 16 and 17: Exp 4 against $D_{sensing}$ on Traffic I and Traffic III.

The sweep compares every $G_z$ and $PDR_w$ value that Sec. 5.3.1 of the paper states for Exp 4, 5, and 6 with the results. It prints PASS, FAIL, or SKIP for each point. Add `--strict` to exit with code 1 when a point fails.

### Evaluation Results

**Provenance of `results/benchmark_results.csv` and `figures/`:** `python scripts/run_benchmark_sweep.py --duration 60 --seeds 1 --jobs 8` made both (200 runs, seed 42, all three stages, Traffic I and III in the main stage). Each run replays 60 s of trace: 60 cycles at $T_{ZB}$ = 1000 ms, 12 cycles at 5000 ms. The paper replays 600 s traces (Fig. 10), so treat these values as a reference run, not a full reproduction. A 10 s solver limit applies to each cycle, so time-limited cycles can differ between machines.

Main results on Traffic I at $T_{ZB}$ = 1000 ms (Fig. 11):

| Exp   | $G_z$ 31 / 62 / 123 ms | $PDR_w$ 31 / 62 / 123 ms | Paper $G_z$ / $PDR_w$             |
| ----- | ---------------------- | ------------------------ | --------------------------------- |
| Exp 1 | 0.132 / 0.116 / 0.116  | 11.4 / 14.0 / 14.0 %     | 1.3 % at 31 ms / 9.7 % at 123 ms  |
| Exp 4 | 0.942 / 0.873 / 0.609  | 1.3 / 2.7 / 4.1 %        | > 98.6 / 96.3 / 93.4–94.7 % / 0 % |
| Exp 5 | 1.000 / 1.000 / 1.000  | 1.3 / 3.4 / 8.2 %        | 100 % / < 1 % at 31, 2.8 % at 123 |
| Exp 6 | 0.967 / 0.936 / 0.655  | 1.3 / 2.8 / 4.5 %        | as Exp 4                          |

The acceptance check passes 5 of 17 paper points. Review 3 §9 lists each deviation. `reviews/project_document.md` §7 explains the causes: Traffic I at 123 ms needs about 101 % of each cycle, and the paper gives no MILP weights. The qualitative trends of Figs. 15–17 hold: $G_z$ falls as WiFi occupancy grows, and a longer sensing window lowers $G_z$ on Traffic I but not on Traffic III.

The sweep draws the figures itself. To redraw them from an existing results CSV:

```bash
python -m cowic.analysis --results-csv results/benchmark_results.csv --output-dir figures/
```

## Base Paper

Cheng, T., Li, S., and Pan, Y. "CoWiC-MAC: A Coordinated WiFi-Centric MAC Mechanism in Heterogeneous Wireless Networks." _Computer Networks_, Vol. 287, Art. 112479, Sept. 2026. DOI: [10.1016/j.comnet.2026.112479](https://doi.org/10.1016/j.comnet.2026.112479)
