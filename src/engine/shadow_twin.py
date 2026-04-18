# src/engine/shadow_twin.py
"""Deep-path heavy simulations (PyBAMM).

The Shadow Twin runs infrequent, high-fidelity electrochemical simulations
(via PyBAMM). It reads cell state from SHM, runs a full DAE solve,
and writes updated macroscopic parameters (like SoH/Capacity) back via
the BESSUpdateBuffer epoch lock-free synchronization.
"""

from __future__ import annotations

import logging
import multiprocessing
import time
from pathlib import Path

import numpy as np
import pybamm

from config.settings import load_config
from core.registry import AssetRegistry
from core.shm_manager import BESSSharedState, BESSUpdateBuffer

logger = logging.getLogger(__name__)

def shadow_twin_loop(
    config_path: str,
    bess_id: str,
    dt: float,
    shutdown_event: multiprocessing.Event,
) -> None:
    """Heavy PyBAMM shadow twin process loop."""
    config = load_config(Path(config_path))
    registry = AssetRegistry(config)
    bess_cfg = registry.get_bess(bess_id)

    # Attach to existing SHM
    state = BESSSharedState(bess_cfg, create=False)
    update_buffer = BESSUpdateBuffer(bess_id, create=False)

    logger.info("Initializing PyBAMM SPMe for BESS '%s'", bess_id)
    
    # Simple SPM with electrolyte and SEI degradation mechanism
    model = pybamm.lithium_ion.SPMe({"SEI": "ec reaction limited"})
    parameter_values = pybamm.ParameterValues("Chen2020")
    
    # Initialize simulation with Casadi solver for fast execution
    sim = pybamm.Simulation(
        model, 
        parameter_values=parameter_values, 
        solver=pybamm.CasadiSolver()
    )
    sim.step(dt=0.001)  # Execute cold initialization

    logger.info("PyBAMM Simulation spawned for BESS '%s'", bess_id)

    try:
        while not shutdown_event.is_set():
            # 1. Read State from Hot Path
            mean_temp = float(np.mean(state.temperature.array))
            mean_soc = float(np.mean(state.soc.array))

            # 2. Step PyBAMM using the dt duration
            # (In a fully robust pipeline we override PyBAMM variables with temperature)
            # but for this MVP, step time forward.
            sim.step(dt=dt)

            # 3. Extract Degradation Parameter
            sol = sim.solution
            new_capacity = float(sol["Total lithium capacity [A.h]"].entries[-1])

            # 4. Lock-Free Buffer Update
            update_buffer.capacity_ah = new_capacity
            
            # 5. Strict Memory Barrier Increment
            update_buffer.epoch += 1

            shutdown_event.wait(timeout=dt)
    except Exception as exc:
        logger.error("Shadow Twin error for BESS '%s': %s", bess_id, exc)
    finally:
        state.close()
        update_buffer.close()
        logger.info("Shadow Twin stopped for BESS '%s'", bess_id)