# src/engine/physics.py

"""Fast-path vectorized physics engine (NumPy).

Pure functions for vectorized SoC, voltage, and temperature updates,
plus the process entry point for running the physics loop on SHM.

All math is vectorized, no Python loops over cells. This is the
"Hot Path" that runs at 10-100 Hz in a dedicated CPU-bound process.
"""

from __future__ import annotations

import logging
import multiprocessing
from pathlib import Path

import numpy as np

from config.settings import load_config
from core.registry import AssetRegistry
from core.shm_manager import BESSControlBuffer, BESSSharedState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure vectorized math (no SHM dependency — unit-testable)
# ---------------------------------------------------------------------------


def update_soc(
    soc: np.ndarray,
    cell_current: float,
    dt: float,
    capacity_ah: float,
) -> None:
    """Coulomb-counting SoC update (vectorized, in-place).

    Args:
        soc: State of Charge array in percent, modified in-place.
        cell_current: Per-cell current in amps.
            Positive = discharge (SoC decreases).
        dt: Time step in seconds.
        capacity_ah: Cell capacity in ampere-hours.
    """
    delta_pct = (cell_current * dt) / (capacity_ah * 3600.0) * 100.0
    soc[:] -= delta_pct
    np.clip(soc, 0.0, 100.0, out=soc)


def update_voltage_from_soc(
    voltage: np.ndarray,
    soc: np.ndarray,
    v_min: float = 2.5,
    v_max: float = 4.2,
) -> None:
    """Linear OCV model: voltage = f(SoC) (vectorized, in-place).

    Maps SoC linearly to voltage between ``v_min`` (0% SoC) and
    ``v_max`` (100% SoC).

    Args:
        voltage: Voltage array in volts, modified in-place.
        soc: State of Charge array in percent (read-only).
        v_min: Empty-cell voltage (V).
        v_max: Full-cell voltage (V).
    """
    voltage[:] = v_min + (v_max - v_min) * (soc / 100.0)


def update_temperature(
    temperature: np.ndarray,
    cell_current: float,
    dt: float,
    ambient: float = 25.0,
    thermal_tau: float = 0.01,
) -> None:
    """Simple thermal model: drift toward ambient + self-heating (in-place).

    Args:
        temperature: Temperature array in °C (float32), modified in-place.
        cell_current: Per-cell current in amps (absolute value used).
        dt: Time step in seconds.
        ambient: Ambient temperature in °C.
        thermal_tau: Thermal time constant (dimensionless, per second).
    """
    self_heating = abs(cell_current) * 0.005
    target = ambient + self_heating
    delta = (target - temperature) * thermal_tau * dt
    temperature[:] += delta


def apply_cc_cv_throttling(
    current: np.ndarray,
    voltage: np.ndarray,
    v_max: float = 4.2,
    taper_band: float = 0.05,
) -> None:
    """Applies Constant-Current / Constant-Voltage charging factor in-place.
    
    Uses mathematical vector operations to throttle charging (negative) current
    as cell voltage approaches v_max linearly within the taper_band.
    """
    throttle_factor = np.clip((v_max - voltage) / taper_band, 0.0, 1.0)
    current[:] = np.where(current < 0, current * throttle_factor, current)


# ---------------------------------------------------------------------------
# Process entry point
# ---------------------------------------------------------------------------


def bess_physics_loop(
    config_path: str,
    bess_id: str,
    dt: float,
    shutdown_event: multiprocessing.Event,
) -> None:
    """Physics engine process entry point for a single BESS.

    Loads config independently (no pickling), attaches to existing SHM
    by name, and runs a tight simulation loop until the shutdown event
    is set.

    Args:
        config_path: Path to the JSON config file (string, not Path).
        bess_id: BESS identifier to operate on.
        dt: Time step in seconds (e.g., 0.1 for 10 Hz).
        shutdown_event: Event to signal graceful shutdown.
        initial_soc: Starting SoC in percent (default 80%).
    """
    config = load_config(Path(config_path))
    registry = AssetRegistry(config)
    bess_cfg = registry.get_bess(bess_id)

    # Attach to existing SHM (supervisor already allocated)
    state = BESSSharedState(bess_cfg, create=False)
    ctrl = BESSControlBuffer(bess_id, create=False)

    # Extract cell parameters
    capacity_ah = bess_cfg.cell_spec.nominal_capacity
    cell_current = ctrl.load_current_a / bess_cfg.num_strings

    # Initialize state arrays
    init_state = bess_cfg.initial_state

    if init_state.mode == "scalar":
        state.soc.array[:] = init_state.soc_pct
        state.voltages.array[:] = init_state.voltage_v
        state.temperature.array[:] = init_state.temperature_c
        ambient_target = init_state.temperature_c
    elif init_state.mode == "distribution":
        state.soc.array[:] = np.random.normal(
            loc=init_state.soc_mean,
            scale=init_state.soc_std,
            size=state.soc.array.shape,
        )
        state.voltages.array[:] = np.random.normal(
            loc=init_state.voltage_mean,
            scale=init_state.voltage_std,
            size=state.voltages.array.shape,
        )
        state.temperature.array[:] = np.random.normal(
            loc=init_state.temperature_mean,
            scale=init_state.temperature_std,
            size=state.temperature.array.shape,
        )
        np.clip(state.soc.array, 0.0, 100.0, out=state.soc.array)
        np.clip(state.voltages.array, 2.5, 4.2, out=state.voltages.array)
        ambient_target = init_state.temperature_mean

    state.soh.array[:] = 100.0

    logger.info(
        "Physics engine started for BESS '%s' | dt=%.3fs | I_cell=%.2fA | C=%.1fAh",
        bess_id,
        dt,
        cell_current,
        capacity_ah,
    )

    current_array = np.empty(state.soc.array.shape, dtype=np.float64)

    try:
        while not shutdown_event.is_set():
            current_array[:] = ctrl.load_current_a / bess_cfg.num_strings
            apply_cc_cv_throttling(current_array, state.voltages.array, v_max=4.2, taper_band=0.05)
            update_soc(state.soc.array, current_array, dt, capacity_ah)
            update_voltage_from_soc(state.voltages.array, state.soc.array)
            update_temperature(
                state.temperature.array, current_array, dt, ambient=ambient_target
            )
            shutdown_event.wait(timeout=dt)
    except Exception as exc:
        logger.error("Physics engine error for BESS '%s': %s", bess_id, exc)
    finally:
        state.close()
        ctrl.close()
        logger.info("Physics engine stopped for BESS '%s'", bess_id)