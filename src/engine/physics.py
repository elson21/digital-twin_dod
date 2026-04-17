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


# ---------------------------------------------------------------------------
# Process entry point
# ---------------------------------------------------------------------------


def bess_physics_loop(
    config_path: str,
    bess_id: str,
    dt: float,
    shutdown_event: multiprocessing.Event,
    initial_soc: float = 80.0,
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
    state.soc.array[:] = initial_soc
    state.soh.array[:] = 100.0
    state.temperature.array[:] = np.float32(ambient := 25.0)
    update_voltage_from_soc(state.voltages.array, state.soc.array)

    logger.info(
        "Physics engine started for BESS '%s' | dt=%.3fs | I_cell=%.2fA | C=%.1fAh",
        bess_id,
        dt,
        cell_current,
        capacity_ah,
    )

    try:
        while not shutdown_event.is_set():
            cell_current = ctrl.load_current_a / bess_cfg.num_strings
            update_soc(state.soc.array, cell_current, dt, capacity_ah)
            update_voltage_from_soc(state.voltages.array, state.soc.array)
            update_temperature(
                state.temperature.array, cell_current, dt, ambient=ambient
            )
            shutdown_event.wait(timeout=dt)
    except Exception as exc:
        logger.error("Physics engine error for BESS '%s': %s", bess_id, exc)
    finally:
        state.close()
        ctrl.close()
        logger.info("Physics engine stopped for BESS '%s'", bess_id)