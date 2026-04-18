# src/supervisor.py
"""System Orchestrator: manages the full lifecycle of the digital twin.

The Supervisor is the single entry point for the application.  It:
  1. Loads and validates the JSON configuration file (fail-fast).
  2. Creates the Asset Registry from the validated config.
  3. Allocates shared memory for all registered assets.
  4. Spawns worker processes for physics simulation and persistence.
  5. Handles orderly shutdown with explicit SHM cleanup.
"""

from __future__ import annotations

import logging
import multiprocessing
import time
from multiprocessing import Process
from pathlib import Path

from config.settings import MicrogridConfig, OperationMode, load_config
from core.registry import AssetRegistry
from core.shm_manager import (
    BESSControlBuffer,
    BESSSharedState,
    BESSUpdateBuffer,
    GensetSharedState,
    PVSharedState,
)
from engine.physics import bess_physics_loop
from services.db_writer import db_writer_loop

logger = logging.getLogger(__name__)


class Supervisor:
    """System orchestrator for the Microgrid Digital Twin.

    Manages the full lifecycle: config loading → SHM allocation →
    worker spawning → monitoring → orderly shutdown.

    Args:
        config_path: Path to the JSON configuration file.
            The file is NOT read until ``start()`` is called.
    """

    def __init__(self, config_path: Path) -> None:
        """Initialize the supervisor.

        Args:
            config_path: Path to the JSON configuration file.
        """
        self._config_path = config_path
        self._config: MicrogridConfig | None = None
        self._registry: AssetRegistry | None = None
        self._bess_states: dict[str, BESSSharedState] = {}
        self._bess_controls: dict[str, BESSControlBuffer] = {}
        self._bess_updates: dict[str, BESSUpdateBuffer] = {}
        self._genset_states: dict[str, GensetSharedState] = {}
        self._pv_states: dict[str, PVSharedState] = {}
        self._workers: list[Process] = []
        self._shutdown_event: multiprocessing.Event = multiprocessing.Event()
        self._running = False

    # ------------------------------------------------------------------
    # Public Interface
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Boot the system: load config → create registry → allocate SHM.

        Does NOT spawn worker processes. Call ``spawn_workers()``
        separately, or use ``run()`` for the full lifecycle.

        Raises:
            MissingConfigurationError: If the config file is missing or
                unreadable.
            pydantic.ValidationError: If the config fails schema validation.
            ValueError: If duplicate asset IDs are found in the registry.
        """
        logger.info("Loading configuration from '%s'...", self._config_path)
        self._config = load_config(self._config_path)
        self._registry = AssetRegistry(self._config)

        try:
            self._allocate_shm()
        except Exception:
            logger.error("SHM allocation failed — cleaning up partial state")
            self._deallocate_shm()
            raise

        self._running = True
        logger.info(
            "Supervisor started | mode=%s | assets=%d | buffers=%d",
            self._config.mode.value,
            self._registry.total_assets,
            len(self.all_buffer_names),
        )

    def spawn_workers(
        self,
        dt: float = 0.1,
        db_output_dir: Path | None = None,
        enable_db_writer: bool = True,
        enable_shadow_twin: bool = True,
    ) -> None:
        """Spawn simulation worker processes.

        Must be called after ``start()``.  In SIMULATION mode, spawns a
        physics engine process per BESS unit and optionally a DB writer.

        Args:
            dt: Physics engine time step in seconds (default 0.1 = 10 Hz).
            db_output_dir: Directory for CSV output.  Defaults to ``output/``.
            enable_db_writer: Whether to spawn the DB writer process.
            enable_shadow_twin: Whether to spawn the heavy PyBAMM shadow twin process.
        """
        if not self._running:
            raise RuntimeError("Supervisor not started. Call start() first.")

        assert self._config is not None
        assert self._registry is not None

        if self._config.mode == OperationMode.SIMULATION:
            self._spawn_simulation_workers(
                dt, db_output_dir or Path("output"), enable_db_writer, enable_shadow_twin
            )

    def shutdown(self) -> None:
        """Orderly shutdown: signal workers → join → close/unlink all SHM.

        Idempotent — safe to call multiple times.
        """
        if not self._running:
            return

        logger.info("Shutting down supervisor...")
        self._shutdown_event.set()
        self._stop_workers()
        self._deallocate_shm()
        self._running = False
        logger.info("Supervisor shutdown complete")

    def run(self, dt: float = 0.1) -> None:
        """Full lifecycle: start → spawn workers → block → shutdown.

        Blocks in a sleep loop until ``KeyboardInterrupt`` (Ctrl+C),
        then performs an orderly shutdown.

        Args:
            dt: Physics engine time step in seconds.
        """
        self.start()
        self.spawn_workers(dt=dt)
        try:
            logger.info("Supervisor running. Press Ctrl+C to stop.")
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        finally:
            self.shutdown()

    # ------------------------------------------------------------------
    # Properties & Accessors
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the supervisor is currently active."""
        return self._running

    @property
    def registry(self) -> AssetRegistry | None:
        """The asset registry (available after ``start()``)."""
        return self._registry

    def get_bess_state(self, bess_id: str) -> BESSSharedState:
        """Look up BESS shared state by ID.

        Args:
            bess_id: The BESS identifier.

        Returns:
            The ``BESSSharedState`` for the given ID.

        Raises:
            KeyError: If no BESS with the given ID has allocated SHM.
        """
        return self._bess_states[bess_id]

    def set_load_current(self, bess_id: str, current_a: float) -> None:
        """Update the load current setpoint for a BESS at runtime."""
        self._bess_controls[bess_id].load_current_a = current_a
        logger.info("BESS '%s' load current set to %.2f A", bess_id, current_a)

    def get_load_current(self, bess_id: str) -> float:
        """Read the current load current setpoint for a BESS."""
        return self._bess_controls[bess_id].load_current_a

    def get_genset_state(self, genset_id: str) -> GensetSharedState:
        """Look up Genset shared state by ID.

        Args:
            genset_id: The Genset identifier.

        Returns:
            The ``GensetSharedState`` for the given ID.

        Raises:
            KeyError: If no Genset with the given ID has allocated SHM.
        """
        return self._genset_states[genset_id]

    def get_pv_state(self, pv_id: str) -> PVSharedState:
        """Look up PV shared state by ID.

        Args:
            pv_id: The PV identifier.

        Returns:
            The ``PVSharedState`` for the given ID.

        Raises:
            KeyError: If no PV with the given ID has allocated SHM.
        """
        return self._pv_states[pv_id]

    @property
    def all_buffer_names(self) -> list[str]:
        """All SHM buffer names across all asset types."""
        names: list[str] = []
        for state in self._bess_states.values():
            names.extend(state.buffer_names)
        for ctrl in self._bess_controls.values():
            names.extend(ctrl.buffer_names)
        for upd in self._bess_updates.values():
            names.extend(upd.buffer_names)
        for state in self._genset_states.values():
            names.extend(state.buffer_names)
        for state in self._pv_states.values():
            names.extend(state.buffer_names)
        return names

    # ------------------------------------------------------------------
    # Internal: SHM Allocation
    # ------------------------------------------------------------------

    def _allocate_shm(self) -> None:
        """Allocate shared memory for all registered assets."""
        assert self._registry is not None

        for bess_id in self._registry.bess_ids:
            cfg = self._registry.get_bess(bess_id)
            state = BESSSharedState(cfg, create=True)
            self._bess_states[bess_id] = state
            
            ctrl = BESSControlBuffer(bess_id, create=True)
            ctrl.load_current_a = cfg.load_current_a
            self._bess_controls[bess_id] = ctrl
            
            upd = BESSUpdateBuffer(bess_id, create=True)
            upd.capacity_ah = cfg.cell_spec.nominal_capacity
            self._bess_updates[bess_id] = upd
            
            logger.info(
                "Allocated SHM for BESS '%s': %d cells, %d buffers",
                bess_id,
                cfg.total_units,
                len(state.buffer_names) + len(ctrl.buffer_names) + len(upd.buffer_names),
            )

        for genset_id in self._registry.genset_ids:
            cfg = self._registry.get_genset(genset_id)
            state = GensetSharedState(cfg, create=True)
            self._genset_states[genset_id] = state
            logger.info(
                "Allocated SHM for Genset '%s': %d units",
                genset_id,
                cfg.total_units,
            )

        for pv_id in self._registry.pv_ids:
            cfg = self._registry.get_pv(pv_id)
            state = PVSharedState(cfg, create=True)
            self._pv_states[pv_id] = state
            logger.info(
                "Allocated SHM for PV '%s': %d units",
                pv_id,
                cfg.total_units,
            )

    def _deallocate_shm(self) -> None:
        """Close and unlink all shared memory segments."""
        all_states = (
            list(self._bess_states.values())
            + list(self._bess_controls.values())
            + list(self._bess_updates.values())
            + list(self._genset_states.values())
            + list(self._pv_states.values())
        )

        for state in all_states:
            try:
                state.close()
                state.unlink()
            except Exception as exc:
                logger.warning("Error during SHM cleanup: %s", exc)

        self._bess_states.clear()
        self._bess_controls.clear()
        self._bess_updates.clear()
        self._genset_states.clear()
        self._pv_states.clear()

    # ------------------------------------------------------------------
    # Internal: Workers
    # ------------------------------------------------------------------

    def _spawn_simulation_workers(
        self, dt: float, output_dir: Path, enable_db_writer: bool, enable_shadow_twin: bool
    ) -> None:
        """Spawn physics engine, DB writer, and Shadow Twin processes for SIMULATION mode."""
        assert self._registry is not None

        for bess_id in self._registry.bess_ids:
            p = Process(
                target=bess_physics_loop,
                args=(
                    str(self._config_path),
                    bess_id,
                    dt,
                    self._shutdown_event,
                ),
                name=f"physics_{bess_id}",
                daemon=True,
            )
            p.start()
            self._workers.append(p)
            logger.info(
                "Spawned physics worker for BESS '%s' (pid=%d)", bess_id, p.pid
            )

        if enable_db_writer:
            p = Process(
                target=db_writer_loop,
                args=(
                    str(self._config_path),
                    str(output_dir),
                    dt * 10,  # DB writer runs 10× slower than physics
                    self._shutdown_event,
                ),
                name="db_writer",
                daemon=True,
            )
            p.start()
            self._workers.append(p)
            logger.info("Spawned DB writer (pid=%d)", p.pid)

        if enable_shadow_twin:
            from engine.shadow_twin import shadow_twin_loop

            for bess_id in self._registry.bess_ids:
                p = Process(
                    target=shadow_twin_loop,
                    args=(
                        str(self._config_path),
                        bess_id,
                        5.0,  # Shadow Twin updates every 5.0 seconds
                        self._shutdown_event,
                    ),
                    name=f"shadow_{bess_id}",
                    daemon=True,
                )
                p.start()
                self._workers.append(p)
                logger.info("Spawned PyBAMM Shadow Twin for BESS '%s' (pid=%d)", bess_id, p.pid)

    def _stop_workers(self) -> None:
        """Gracefully stop all worker processes."""
        # Shutdown event was already set in shutdown() — workers should
        # break out of their loops.  Give them time to finish cleanly.
        for worker in self._workers:
            if worker.is_alive():
                worker.join(timeout=5)
                if worker.is_alive():
                    logger.warning(
                        "Force-terminating worker %s (pid=%s)",
                        worker.name,
                        worker.pid,
                    )
                    worker.terminate()
                    worker.join(timeout=2)
                    if worker.is_alive():
                        worker.kill()
                        worker.join(timeout=1)
        self._workers.clear()