# Patch Notes

## Recent Updates
- **Config Alignment**: Re-aligned `BESSConfig` schemas (in `bess_config.py`) to properly match JSON initialization fields (`temperature_c`, `temperature_mean`, `temperature_std`).
- **CC-CV Throttling**: Added a purely vectorized, branchless function `apply_cc_cv_throttling` to handle Constant-Current / Constant-Voltage tapering for charging currents.
- **Physics Pre-allocation**: Improved GC reliability by pre-allocating the loop constraint with `current_array = np.empty(...)`.
- **Physics Loop Integration**: Re-plumbed the `bess_physics_loop` while-loop to broadcast loads dynamically into the mutable `current_array`, piping the continuous memory segment through the physics updates in-place.
