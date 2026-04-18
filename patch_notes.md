# Patch Notes

## Recent Updates
- **Config Alignment**: Re-aligned `BESSConfig` schemas (in `bess_config.py`) to properly match JSON initialization fields (`temperature_c`, `temperature_mean`, `temperature_std`).
- **CC-CV Throttling**: Added a purely vectorized, branchless function `apply_cc_cv_throttling` to handle Constant-Current / Constant-Voltage tapering for charging currents.
- **Physics Pre-allocation**: Improved GC reliability by pre-allocating the loop constraint with `current_array = np.empty(...)`.
- **Physics Loop Integration**: Re-plumbed the `bess_physics_loop` while-loop to broadcast loads dynamically into the mutable `current_array`, piping the continuous memory segment through the physics updates in-place.
- **Hierarchical Aggregation**: Implemented zero-copy `aggregate_voltages` matching `num_strings`, `packs_per_string`, and `cells_per_pack` utilizing `.reshape(strings, packs, cells)` hierarchy mapping. Pack, String, and System (average) voltages are tracked dynamically using hierarchical `.sum()` operations and logged via `logger.debug` at the tail of the hot path.
- **Double Buffer Architecture**: Integrated a Lock-Free Epoch-based Update Buffer mapping a structured NumPy schema over the PyBAMM constraints. The `BESSUpdateBuffer` dynamically allocates via the pool manager without conflicting namespace issues.
- **Asynchronous Hot Path Sync**: Piped the lock-free payload parameters dynamically inside `bess_physics_loop`. The tight loop conditionally checks `.epoch` to grab overarching constraints effortlessly without standard thread locking context switches.
- **DB Writer Topological Tracking**: Migrated naive CSV summation off the DB Writer loop, replicating it directly with the mathematical O(1) `.reshape()` topological map. Added distinct bounding telemetry constraints (`system_voltage_v` and formatted array `string_voltages_v`).
- **Heavy Path Execution Block**: Integrated the definitive PyBAMM CasadiSolver architecture inside `shadow_twin.py`. The asynchronous solver pulls dynamic macroscopic State properties directly from the 100Hz `SHM` parallel node and forces execution parameters up via the atomic lock-free `update_buffer.epoch` constraint payload, finalizing the Shadow Twin orchestrator loop perfectly.
