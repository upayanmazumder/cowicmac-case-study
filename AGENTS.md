# Repository Guidelines

## Project Overview

This repository implements a software-only simulation of the CoWiC-MAC protocol. The project studies wireless coexistence between IEEE 802.11ax (WiFi) and IEEE 802.15.4 (ZigBee) in the shared 2.4 GHz ISM band. The WiFi Access Point (AP) operates as a centralized orchestrator. The AP reschedules WiFi Frame Groups to create clear transmission windows for ZigBee nodes. Cross-Technology Communication (CTC) connects the two networks for bidirectional control signaling. The primary goal is to reproduce and evaluate CoWiC-MAC without physical radio hardware.

## Architecture & Data Flow

The system consists of three architectural layers: the WiFi domain, the ZigBee domain, and the shared channel simulation.

```
+-----------------------------------------------------------+
|                   WiFi AP Orchestrator                    |
|  - Receives ZBeacon requests via CTC                      |
|  - Solves MILP scheduling program                         |
|  - Defer/reorder WiFi Frame Groups                        |
|  - Transmits WBeacon coordination notifications           |
+-----------------------------+-----------------------------+
                              | CTC Control Signaling
                              v
+-----------------------------+-----------------------------+
|                     ZigBee Coordinator                    |
|  - Collects traffic demands from ZigBee nodes             |
|  - Transmits demand reports via ZBeacon                   |
|  - Executes L-WSA algorithm to adapt superframe slots     |
|  - Configures CAP, CFP, slot times, and beacon offset     |
+-----------------------------+-----------------------------+
                              | Scheduled Transmissions
                              v
+-----------------------------------------------------------+
|                   Shared 2.4 GHz Medium                   |
|  - Evaluates concurrent airtime overlap                   |
|  - Marks overlapping WiFi and ZigBee bursts as collisions |
|  - Records throughput, loss rate, delay, and jitter       |
+-----------------------------------------------------------+
```

### Data Flow Sequence
1. The ZigBee Coordinator (ZC) reports traffic demands to the AP using a ZBeacon frame.
2. The AP solves a Mixed-Integer Linear Program (MILP). The solver reschedules WiFi Frame Groups (FGs) around an interference-free window ($D_\text{free}$) and a protected sensing window ($D_\text{sensing}$).
3. The AP broadcasts the allocation in a WBeacon frame.
4. The ZC executes the Lightweight Window-Scaling Algorithm (L-WSA). The algorithm optimizes the superframe slot duration, CAP count, CFP count, and beacon offset.
5. Devices transmit within their assigned intervals on the shared channel.
6. The simulation engine detects temporal collisions and records performance metrics.

## Key Directories

- `src/cowic/models/`: Core data structures for WiFi Frame Groups, ZigBee superframes, and packets.
- `src/cowic/wifi/`: WiFi AP orchestration state machine and PuLP MILP scheduling solvers.
- `src/cowic/zigbee/`: ZigBee Coordinator state machine and L-WSA discrete search logic.
- `src/cowic/ctc/`: Cross-Technology Communication link emulation and packet error models.
- `src/cowic/sim/`: Discrete-event simulation runner and shared channel collision medium.
- `src/cowic/analysis/`: Calculation of transmission gain, packet drops, spectrum utilization, and plotting.
- `configs/`: YAML configuration files for experimental groups (Exp 1 to Exp 6) and traffic profiles.
- `traces/`: Replay datasets and synthetic generator for WiFi traffic traces (Traffic I to IV).
- `tests/`: Automated unit tests, property checks, and end-to-end integration tests.
- `reviews/`: Course case study submissions, review documents, and base paper PDF.

## Development Commands

Use standard Python tooling. Run all commands from the repository root.

### Environment Setup
Create a virtual environment and install development dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Testing
Execute the complete test suite:
```bash
pytest
```

Run a specific test file with verbose output:
```bash
pytest tests/test_milp_solver.py -v
```

Execute tests with code coverage:
```bash
pytest --cov=src/cowic --cov-report=term-missing
```

### Linting and Formatting
Check code style and formatting using Ruff:
```bash
ruff check .
ruff format --check .
```

Apply automated fixes:
```bash
ruff format .
ruff check --fix .
```

Run static type checks with MyPy:
```bash
mypy src/ tests/
```

### Running Experiments
Generate synthetic traffic traces:
```bash
python -m traces.synthetic_generator --profile Traffic_I --output traces/data/traffic1.parquet
```

Execute a specific coexistence scenario:
```bash
python -m cowic.sim --config configs/exp4_cowic_full.yaml
```

Generate evaluation figures:
```bash
python -m cowic.analysis --results-csv results/benchmark_results.csv --output-dir figures/
```

## Code Conventions & Common Patterns

### Language and Formatting
- Target Python 3.11 or newer.
- Follow PEP 8 style conventions enforced by Ruff.
- Line length limit is 88 characters.
- Use explicit type annotations on all function signatures and class attributes.

### Data Structures and Models
- Use `@dataclass(frozen=True)` or Pydantic models for immutable protocol elements:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class FrameGroup:
    fg_id: int
    arrival_time_ms: float
    duration_ms: float
    deadline_ms: float
    original_start_ms: float
    qos_sensitive: bool
```

### State Management
- Model protocol states explicitly using Python `Enum` classes.
- Do not use arbitrary string literals for protocol states:
```python
from enum import Enum, auto


class APState(Enum):
    MAINTAINING = auto()
    CTC_GATHERING = auto()
    RESCHEDULING = auto()
    CTC_NOTIFYING = auto()
    TRANSMITTING = auto()
```

### Optimization Solvers
- Encapsulate the MILP formulation inside `src/cowic/wifi/milp_scheduler.py`.
- Use `pulp` with the HiGHS solver backend (`pulp.HiGHS`, provided by `highspy`).
- Catch solver infeasibility and trigger the whitespace fallback mode:
```python
import pulp


def solve_schedule(
    fgs: list[FrameGroup], d_free: float, d_sense: float
) -> ScheduleResult:
    prob = pulp.LpProblem("WiFi_Rescheduling", pulp.LpMinimize)
    # Define decision variables, objective, and constraints
    status = prob.solve(pulp.HiGHS(msg=False))
    if status != pulp.LpStatusOptimal:
        return fallback_whitespace_expansion(fgs, d_free)
    return extract_solution(prob, fgs)
```

### Error Handling
- Use domain-specific exceptions inheriting from a base `CoWiCError`.
- Fail fast on invalid configuration ranges or impossible timeline intervals.

## Important Files

- `IMPLEMENTATION_PLAN.md`: System specification, mathematical formulas, evaluation plan, and milestones.
- `pyproject.toml`: Project build definition, tool settings, and package dependencies.
- `src/cowic/wifi/milp_scheduler.py`: MILP formulation of Equations 11 to 17 from the base paper.
- `src/cowic/zigbee/lwsa_adapter.py`: Implementation of the L-WSA discrete search algorithm.
- `src/cowic/sim/simulation_engine.py`: SimPy discrete-event coordinator.
- `src/cowic/sim/channel_medium.py`: Overlap detection and collision tracking logic on the 2.4 GHz channel.
- `configs/exp4_cowic_full.yaml`: Default parameter set for the proposed CoWiC-MAC protocol.

## Runtime/Tooling Preferences

- **Runtime:** CPython 3.11+.
- **Package Manager:** `pip` or `uv`. Use standard `pyproject.toml` packaging.
- **Discrete-Event Simulation:** `simpy` 4.x.
- **Optimization:** `pulp` with `highspy` (HiGHS) solver.
- **Data Analysis:** `numpy`, `pandas`, `pyarrow` (parquet format).
- **Visualization:** `matplotlib`.
- **Quality Assurance:** `pytest`, `ruff`, `mypy`.
- Avoid heavy runtime containers or external databases. Keep simulation workflows file-based.

## Testing & QA

### Testing Strategy
- **Unit Tests (`tests/test_milp_solver.py`):** Verify that the MILP scheduler prevents frame group overlaps and strictly protects the sensing window ($\sum z_k^\text{sense} = 0$).
- **Unit Tests (`tests/test_lwsa_algorithm.py`):** Verify that L-WSA chooses optimal superframe configurations within IEEE 802.15.4 bounds ($j \le 7$, $i + j \le 15$).
- **Channel Verification (`tests/test_collision_detector.py`):** Assert that concurrent transmissions collide and non-overlapping transmissions succeed.
- **Integration Tests (`tests/test_end_to_end.py`):** Execute short simulation cycles for Exp 1 and Exp 4 to assert that CoWiC-MAC eliminates WiFi Frame Group drop.

### Coverage Expectations
- Maintain test coverage above 85% for core protocol modules (`src/cowic/wifi/`, `src/cowic/zigbee/`, `src/cowic/sim/`).
- Enforce deterministic test runs by supplying fixed pseudo-random generator seeds (`random.seed(42)`, `numpy.random.default_rng(42)`).
