# Iterations Log

## Phase 1: Configuration & Registry (Control Plane)
**Date:** 2026-04-16

### Changes
- Enriched `BESSConfig` with `load_current_a` field, `total_units` property, and `Field(gt=0)` validators
- Added Google-style docstrings to all config models
- Left `GensetConfig` and `PVConfig` as stubs (full config deferred to next iteration)
- Created `settings.py` with `OperationMode` enum, `MicrogridConfig` root model, `MissingConfigurationError`, and `load_config()` loader
- Implemented `AssetRegistry` in `registry.py` for asset discovery and ID-based cataloging
- Added `__init__.py` files with proper re-exports for all packages
- Created Phase 1 test suite (`test_phase1_config.py`)

### Design Decisions
- `total_units` on BESSConfig maps to `total_cells` (cell-level is finest granularity for SHM buffers)
- All asset lists in MicrogridConfig default to empty (microgrid may not have all asset types)
- Config loader raises `MissingConfigurationError` for both missing files and unreadable/malformed JSON
- Asset Registry enforces unique IDs per type; raises `ValueError` on duplicates

---

## Phase 2: Shared Memory Data Plane
**Date:** 2026-04-16

### Changes
- Implemented `SingleDataBuffer`: wrapper around `multiprocessing.shared_memory.SharedMemory` + `numpy.ndarray`
- Created `_SharedStateBase` internal base class for lifecycle management (`close()`, `unlink()`, buffer tracking)
- Implemented `BESSSharedState` (4 buffers: V, SoC, SoH, Temp)
- Implemented `GensetSharedState` (7 buffers) and `PVSharedState` (6 buffers)
- Made `close()` idempotent via `_closed` flag
- Updated `core/__init__.py` to export all SHM classes
- Created Phase 2 test suite (`test_phase2_shm.py`) with 18 tests including cross-process IPC

### Design Decisions
- Single unified `SingleDataBuffer` replaces the three redundant `*SingleDataBuffer` classes (DRY)
- `float64` for precision-critical data (voltage, SoC, SoH), `float32` for temperature (cache optimization)
- No `current` buffer — physics engine derives per-cell current from `load_current_a / num_strings`
- Cross-process tests use raw `SharedMemory` + `numpy` in child (not our wrapper) to prove genuine OS-level IPC
- Buffer naming convention: `{asset_id}_{param}` (e.g., `BESS_01_V`)

---

## Phase 3: Supervisor (The Heartbeat)
**Date:** 2026-04-16

### Changes
- Implemented `Supervisor` class in `src/supervisor.py`: full lifecycle orchestrator
- Rewrote `main.py` as entry point (`uv run python main.py <config_path>`)
- Supervisor lifecycle: `start()` → `run()` → `shutdown()` with Ctrl+C handling
- `start()`: loads config → creates registry → allocates SHM (with partial-failure rollback)
- `shutdown()`: stops workers → closes/unlinks all SHM (idempotent)
- Added `pyproject.toml` build-system config (hatchling) for installable packages
- Created Phase 3 test suite (`test_phase3_supervisor.py`) with 11 tests

### Design Decisions
- `supervisor.py` lives at `src/` root (not in a package) per README architecture
- `main.py` adds `src/` to `sys.path` for direct execution; pytest uses `pythonpath` config
- `_deallocate_shm()` wraps each close/unlink in try/except to ensure all segments get cleaned up
- `shutdown()` is idempotent via `_running` flag guard
- Worker spawning is stubbed out (`_stop_workers`) — ready for Phase 4 integration

---

## Phase 4: Physics Engine + Drivers + DB Writer
**Date:** 2026-04-16

### Changes
- Implemented `engine/physics.py` with pure vectorized math functions:
  - `update_soc()` — Coulomb-counting SoC update with clamping
  - `update_voltage_from_soc()` — Linear OCV model
  - `update_temperature()` — Simple thermal drift toward ambient + self-heating
  - `bess_physics_loop()` — Process entry point (attaches to SHM by name, runs tight loop)
- Implemented `services/db_writer.py` — Periodic SHM snapshots to CSV (non-blocking, own process)
- Updated `supervisor.py`:
  - Added `spawn_workers(dt, db_output_dir, enable_db_writer)` method
  - Added `multiprocessing.Event` for graceful shutdown signaling
  - `start()` remains worker-free (backward compatible with Phase 3 tests)
  - `run()` now calls `spawn_workers()` automatically
- Wrote proper stubs for `modbus_engine.py`, `canbus_driver.py`, `shadow_twin.py`
- Created Phase 4 test suite (`test_phase4_engine.py`) with 18 tests

### Design Decisions
- Physics math extracted as pure functions (testable without SHM or processes)
- In SIMULATION mode, physics engine handles full simulation — driver not spawned
- `spawn_workers()` separated from `start()` so Phase 3 lifecycle tests remain unaffected
- Graceful shutdown via `multiprocessing.Event` instead of `terminate()` — workers clean up SHM handles
- DB writer runs at 10× slower than physics engine (dt * 10)
- 1000 iterations × 100k elements completes in <2s (vectorization verified)
