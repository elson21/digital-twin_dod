# Digital Twin DOD — Project Overview

## What This Project Is

A **high-performance Microgrid Digital Twin** that mirrors real-world energy assets (BESS, Genset, PV) in software using **Data-Oriented Design** and **Mechanical Sympathy**.

Instead of the traditional OOP approach (thousands of Python `Cell` objects → pointer chasing → GC pressure → missed real-time deadlines), this architecture stores all mutable state in **contiguous shared-memory arrays** accessed via NumPy views. Multiple OS-level processes (telemetry ingestion, physics simulation, database persistence) read/write the same physical RAM with **zero-copy IPC**.

---

## End Goal

A running multi-process system where:

1. A **Supervisor** reads a JSON config, allocates shared memory, spawns workers, and manages lifecycle.
2. A **Modbus/CAN Driver** (or a simulation stub) writes raw telemetry into SHM at 10–100 Hz.
3. A **Physics Engine** reads telemetry from SHM, computes SoC/SoH/voltage via vectorized NumPy, and writes results back — all without copying data.
4. A **DB Writer** periodically snapshots SHM and batch-inserts into a time-series database — without blocking the physics loop.
5. A **Shadow Twin** (PyBAMM) runs infrequent deep-fidelity simulations for health assessment.

The system must support two **modes**: `SIMULATION` (synthetic data) and `TWIN` (real hardware via Modbus/CAN), selectable from config — and must **fail fast** with `MissingConfigurationError` if no valid config is provided.

---

## Current State

### ✅ Phase 1 — Config + Registry (COMPLETE)

| File | Status | Content |
|---|---|---|
| `src/config/bess_config.py` | ✅ Done | `CellConfig` + `BESSConfig` with topology props, `load_current_a`, `total_units`, `Field(gt=0)` |
| `src/config/genset_config.py` | ✅ Stub | `GensetConfig` — minimal, full config deferred to next iteration |
| `src/config/pv_config.py` | ✅ Stub | `PVConfig` — minimal, full config deferred to next iteration |
| `src/config/settings.py` | ✅ Done | `OperationMode` enum, `MicrogridConfig` root model, `MissingConfigurationError`, `load_config()` |
| `src/config/__init__.py` | ✅ Done | Re-exports all models, loader, exception, and enum |
| `src/core/registry.py` | ✅ Done | `AssetRegistry` — discovers assets from config, lookup by ID, duplicate rejection |
| `src/core/shm_manager.py` | ✅ Done | `SingleDataBuffer`, `BESSSharedState`, `GensetSharedState`, `PVSharedState` |
| `src/core/__init__.py` | ✅ Done | Re-exports `AssetRegistry` + all SHM classes |
| `src/engine/physics.py` | ✅ Done | Vectorized `update_soc`, `update_voltage_from_soc`, `update_temperature` + process loop |
| `src/engine/shadow_twin.py` | ✅ Stub | Interface documented for future PyBAMM integration |
| `src/drivers/modbus_engine.py` | ✅ Stub | Interface documented for TWIN mode |
| `src/drivers/canbus_driver.py` | ✅ Stub | Interface documented |
| `src/services/db_writer.py` | ✅ Done | Periodic SHM snapshots to CSV (non-blocking, own process) |
| `src/supervisor.py` | ✅ Done | Full lifecycle orchestrator with graceful shutdown |
| `main.py` | ✅ Done | Entry point: `uv run python main.py <config_path>` |
| `tests/test_phase1_config.py` | ✅ Done | 21 tests — config loading, validation, registry |
| `tests/test_phase2_shm.py` | ✅ Done | 18 tests — SHM primitives, cross-process IPC |
| `tests/test_phase3_supervisor.py` | ✅ Done | 11 tests — supervisor lifecycle, SHM cleanup |
| `tests/test_phase4_engine.py` | ✅ Done | 18 tests — physics math, integration pipeline, DB writer |
| **Total** | | **105 tests passing** |

---

## Build Plan — All Phases Complete ✅

### Phase 1 — Config + Registry ✅
Loads a user-provided JSON config, validates with Pydantic, instantiates models, and catalogs assets in the registry.

### Phase 2 — Shared Memory Data Plane ✅
`SingleDataBuffer` wrapping OS-level shared memory + NumPy. Cross-process zero-copy IPC validated.

### Phase 3 — Supervisor (The Heartbeat) ✅
Full lifecycle orchestrator: config → registry → SHM allocation → worker spawning → graceful shutdown.

### Phase 4 — Physics Engine + Drivers ✅
Vectorized Coulomb-counting SoC, linear OCV voltage model, thermal model. DB writer for CSV persistence. Full pipeline: supervisor spawns physics process → SHM gets updated in real time.

---

## Directory Structure

```
digital-twin-dod/
├── src/
│   ├── config/               # Control Plane (Pydantic models + loader)
│   │   ├── __init__.py
│   │   ├── bess_config.py    # CellConfig + BESSConfig
│   │   ├── genset_config.py  # GensetConfig (stub)
│   │   ├── pv_config.py      # PVConfig (stub)
│   │   └── settings.py       # OperationMode, MicrogridConfig, load_config()
│   ├── core/                 # Data Plane (SHM + Registry)
│   │   ├── __init__.py
│   │   ├── registry.py       # AssetRegistry
│   │   └── shm_manager.py    # SingleDataBuffer + SharedState (Phase 2)
│   ├── drivers/              # Producers (Modbus/CAN → SHM)
│   │   ├── __init__.py
│   │   ├── modbus_engine.py  # (Phase 4)
│   │   └── canbus_driver.py  # (Phase 4)
│   ├── engine/               # Processors (Physics + Shadow Twin)
│   │   ├── __init__.py
│   │   ├── physics.py        # (Phase 4)
│   │   └── shadow_twin.py    # (Phase 4)
│   ├── services/             # Consumers (DB Persistence)
│   │   ├── __init__.py
│   │   └── db_writer.py      # (Phase 4)
│   └── supervisor.py         # Orchestrator (Phase 3)
├── tests/
│   ├── fixtures/
│   │   ├── valid_config.json
│   │   ├── invalid_schema.json
│   │   └── malformed.json
│   └── test_phase1_config.py # 21 tests ✅
├── main.py
├── pyproject.toml
├── iterations.md
└── uv.lock
```
