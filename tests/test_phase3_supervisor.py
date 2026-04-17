# tests/test_phase3_supervisor.py
"""Phase 3 Tests: Supervisor lifecycle management.

Validates:
  - start() loads config, creates registry, and allocates SHM.
  - shutdown() cleans up all SHM segments.
  - Missing config raises MissingConfigurationError before any allocation.
  - Idempotent shutdown (safe to call twice).
  - SHM is accessible after start and freed after shutdown.

Config is loaded from ``tests/fixtures/valid_config.json``.
"""

from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

import numpy as np
import pytest

from config.settings import MissingConfigurationError
from supervisor import Supervisor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_CONFIG_PATH = FIXTURES_DIR / "valid_config.json"


# ===================================================================
# Supervisor Lifecycle
# ===================================================================


class TestSupervisorLifecycle:
    """Tests for the boot → operate → shutdown lifecycle."""

    def test_start_loads_config(self) -> None:
        """start() loads config and creates the asset registry."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            assert sup.is_running
            assert sup.registry is not None
            assert sup.registry.total_assets == 3  # 1 BESS + 1 Genset + 1 PV
        finally:
            sup.shutdown()

    def test_start_allocates_all_buffers(self) -> None:
        """start() allocates the correct number of SHM buffers."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            # BESS: 4 state buffers + 1 control buffer + Genset: 7 buffers + PV: 6 buffers = 18
            assert len(sup.all_buffer_names) == 18
        finally:
            sup.shutdown()

    def test_shutdown_clears_running_flag(self) -> None:
        """shutdown() sets is_running to False."""
        sup = Supervisor(VALID_CONFIG_PATH)
        sup.start()
        assert sup.is_running
        sup.shutdown()
        assert not sup.is_running

    def test_shutdown_is_idempotent(self) -> None:
        """Calling shutdown() twice does not raise."""
        sup = Supervisor(VALID_CONFIG_PATH)
        sup.start()
        sup.shutdown()
        sup.shutdown()  # Must not raise
        assert not sup.is_running

    def test_missing_config_raises_before_allocation(self) -> None:
        """Missing config raises MissingConfigurationError — no SHM leaked."""
        sup = Supervisor(Path("nonexistent.json"))
        with pytest.raises(MissingConfigurationError):
            sup.start()
        assert not sup.is_running
        assert len(sup.all_buffer_names) == 0


# ===================================================================
# SHM Access
# ===================================================================


class TestSupervisorSHMAccess:
    """Tests for accessing shared state through the supervisor."""

    def test_bess_state_writable(self) -> None:
        """BESS voltages array is writable through the supervisor."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            state = sup.get_bess_state("BESS_01")
            assert state.voltages.size == 96  # 2 * 4 * 12

            state.voltages.array[:] = 3.68
            np.testing.assert_allclose(state.voltages.array, 3.68)
        finally:
            sup.shutdown()

    def test_genset_state_accessible(self) -> None:
        """Genset shared state is accessible after start."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            state = sup.get_genset_state("GEN_01")
            assert state.voltages.size == 2
        finally:
            sup.shutdown()

    def test_pv_state_accessible(self) -> None:
        """PV shared state is accessible after start."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            state = sup.get_pv_state("PV_01")
            assert state.voltages.size == 10
        finally:
            sup.shutdown()

    def test_missing_asset_raises(self) -> None:
        """Looking up a non-existent asset ID raises KeyError."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            with pytest.raises(KeyError):
                sup.get_bess_state("NONEXISTENT")
        finally:
            sup.shutdown()


# ===================================================================
# SHM Cleanup Verification
# ===================================================================


class TestSupervisorCleanup:
    """Tests that shutdown properly frees all shared memory."""

    def test_shutdown_frees_all_shm(self) -> None:
        """After shutdown, SHM segments are no longer attachable."""
        sup = Supervisor(VALID_CONFIG_PATH)
        sup.start()
        names = sup.all_buffer_names.copy()
        assert len(names) == 18

        sup.shutdown()

        # Every buffer name should now be unreachable
        for name in names:
            with pytest.raises(FileNotFoundError):
                SharedMemory(name=name, create=False)

    def test_buffer_names_cleared_after_shutdown(self) -> None:
        """After shutdown, all_buffer_names returns an empty list."""
        sup = Supervisor(VALID_CONFIG_PATH)
        sup.start()
        assert len(sup.all_buffer_names) > 0
        sup.shutdown()
        assert len(sup.all_buffer_names) == 0
