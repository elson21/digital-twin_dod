# tests/test_phase2_shm.py
"""Phase 2 Tests: Shared Memory Data Plane.

Validates:
  - SingleDataBuffer creation, read/write, dtype compliance, and cleanup.
  - BESSSharedState construction from the config fixture with correct sizing.
  - Cross-process zero-copy IPC: parent writes → child reads via raw SHM.
  - Bidirectional IPC: child writes → parent reads.

Config is loaded from ``tests/fixtures/valid_config.json``.
Child processes use only ``stdlib + numpy`` (not our wrapper) to prove
the SHM segments are genuine OS-level resources.
"""

import multiprocessing
import uuid
from pathlib import Path

import numpy as np
import pytest

from config.settings import load_config
from core.shm_manager import (
    BESSSharedState,
    GensetSharedState,
    PVSharedState,
    SingleDataBuffer,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_CONFIG_PATH = FIXTURES_DIR / "valid_config.json"


# ---------------------------------------------------------------------------
# Child process targets (module-level for picklability on Windows).
# Uses ONLY stdlib + numpy — no custom imports — to prove the SHM
# segments are real OS resources accessible by any process.
# ---------------------------------------------------------------------------


def _child_read_shm(
    shm_name: str,
    size: int,
    dtype_str: str,
    index: int,
    result_queue: multiprocessing.Queue,
) -> None:
    """Attach to existing SHM by name, read value at *index*, put to queue."""
    from multiprocessing.shared_memory import SharedMemory as _SHM

    import numpy as _np

    shm = _SHM(name=shm_name, create=False)
    arr = _np.ndarray(shape=(size,), dtype=_np.dtype(dtype_str), buffer=shm.buf)
    result_queue.put(float(arr[index]))
    shm.close()


def _child_write_shm(
    shm_name: str,
    size: int,
    dtype_str: str,
    index: int,
    value: float,
) -> None:
    """Attach to existing SHM by name, write *value* at *index*."""
    from multiprocessing.shared_memory import SharedMemory as _SHM

    import numpy as _np

    shm = _SHM(name=shm_name, create=False)
    arr = _np.ndarray(shape=(size,), dtype=_np.dtype(dtype_str), buffer=shm.buf)
    arr[index] = value
    shm.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def unique_name() -> str:
    """Unique SHM name to avoid cross-test collisions."""
    return f"t_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def bess_config():
    """Load the BESS config from the fixture JSON."""
    config = load_config(VALID_CONFIG_PATH)
    return config.bess_units[0]


@pytest.fixture()
def genset_config():
    """Load the Genset config from the fixture JSON."""
    config = load_config(VALID_CONFIG_PATH)
    return config.genset_units[0]


@pytest.fixture()
def pv_config():
    """Load the PV config from the fixture JSON."""
    config = load_config(VALID_CONFIG_PATH)
    return config.pv_units[0]


# ===================================================================
# SingleDataBuffer — Unit Tests
# ===================================================================


class TestSingleDataBuffer:
    """Tests for the core SHM primitive."""

    def test_create_and_size(self, unique_name: str) -> None:
        """Buffer allocates with correct size, dtype, and shape."""
        buf = SingleDataBuffer(unique_name, 10, np.float64, create=True)
        try:
            assert buf.size == 10
            assert buf.dtype == np.float64
            assert buf.array.shape == (10,)
            assert buf.name == unique_name
        finally:
            buf.close()
            buf.unlink()

    def test_zero_initialized(self, unique_name: str) -> None:
        """Creator zero-fills the entire array."""
        buf = SingleDataBuffer(unique_name, 5, np.float64, create=True)
        try:
            np.testing.assert_array_equal(buf.array, np.zeros(5))
        finally:
            buf.close()
            buf.unlink()

    def test_write_and_read(self, unique_name: str) -> None:
        """Direct NumPy write/read on SHM-backed array."""
        buf = SingleDataBuffer(unique_name, 5, np.float64, create=True)
        try:
            buf.array[0] = 3.68
            buf.array[4] = 3.72
            assert buf.array[0] == 3.68
            assert buf.array[4] == 3.72
        finally:
            buf.close()
            buf.unlink()

    def test_float32_dtype(self, unique_name: str) -> None:
        """float32 buffers work correctly (used for temperature)."""
        buf = SingleDataBuffer(unique_name, 3, np.float32, create=True)
        try:
            assert buf.dtype == np.float32
            buf.array[0] = 25.5
            assert buf.array[0] == pytest.approx(25.5, rel=1e-5)
        finally:
            buf.close()
            buf.unlink()

    def test_attach_by_name(self, unique_name: str) -> None:
        """A second buffer can attach to the same SHM and see the data."""
        creator = SingleDataBuffer(unique_name, 10, np.float64, create=True)
        try:
            creator.array[7] = 42.0

            reader = SingleDataBuffer(unique_name, 10, np.float64, create=False)
            try:
                assert reader.array[7] == 42.0
            finally:
                reader.close()
        finally:
            creator.close()
            creator.unlink()

    def test_vectorized_operations(self, unique_name: str) -> None:
        """NumPy vectorized math works on the SHM array — no Python loops."""
        buf = SingleDataBuffer(unique_name, 96, np.float64, create=True)
        try:
            # Vectorized fill
            buf.array[:] = 3.68
            assert np.all(buf.array == 3.68)

            # Vectorized math
            buf.array[:] += 0.04
            np.testing.assert_allclose(buf.array, 3.72)
        finally:
            buf.close()
            buf.unlink()

    def test_close_is_idempotent(self, unique_name: str) -> None:
        """Calling close() twice does not raise."""
        buf = SingleDataBuffer(unique_name, 5, np.float64, create=True)
        buf.close()
        buf.close()  # Should not raise
        buf.unlink()


# ===================================================================
# BESSSharedState — From Config Fixture
# ===================================================================


class TestBESSSharedState:
    """Tests for BESS shared state construction and properties."""

    def test_construction_sizing(self, bess_config) -> None:
        """All buffers are sized to total_units (= total_cells = 96)."""
        state = BESSSharedState(bess_config, create=True)
        try:
            assert state.voltages.size == bess_config.total_units
            assert state.soc.size == bess_config.total_units
            assert state.soh.size == bess_config.total_units
            assert state.temperature.size == bess_config.total_units
        finally:
            state.close()
            state.unlink()

    def test_buffer_count(self, bess_config) -> None:
        """BESS state has exactly 4 buffers (V, SoC, SoH, Temp)."""
        state = BESSSharedState(bess_config, create=True)
        try:
            assert len(state.buffer_names) == 4
        finally:
            state.close()
            state.unlink()

    def test_naming_convention(self, bess_config) -> None:
        """Buffer names follow {bess_id}_{param} pattern."""
        state = BESSSharedState(bess_config, create=True)
        try:
            names = state.buffer_names
            prefix = bess_config.bess_id
            assert f"{prefix}_V" in names
            assert f"{prefix}_SoC" in names
            assert f"{prefix}_SoH" in names
            assert f"{prefix}_Temp" in names
        finally:
            state.close()
            state.unlink()

    def test_dtype_compliance(self, bess_config) -> None:
        """float64 for precision-critical, float32 for temperature."""
        state = BESSSharedState(bess_config, create=True)
        try:
            assert state.voltages.dtype == np.float64
            assert state.soc.dtype == np.float64
            assert state.soh.dtype == np.float64
            assert state.temperature.dtype == np.float32
        finally:
            state.close()
            state.unlink()

    def test_write_voltage_array(self, bess_config) -> None:
        """Vectorized voltage write to the full BESS array."""
        state = BESSSharedState(bess_config, create=True)
        try:
            state.voltages.array[:] = 3.68
            np.testing.assert_allclose(state.voltages.array, 3.68)
        finally:
            state.close()
            state.unlink()

    def test_close_unlink_lifecycle(self, bess_config) -> None:
        """close() + unlink() cascade works without error."""
        state = BESSSharedState(bess_config, create=True)
        state.close()
        state.unlink()  # Should not raise


# ===================================================================
# GensetSharedState & PVSharedState — Smoke Tests
# ===================================================================


class TestGensetSharedState:
    """Smoke tests for Genset shared state."""

    def test_construction(self, genset_config) -> None:
        state = GensetSharedState(genset_config, create=True)
        try:
            assert len(state.buffer_names) == 7
            assert state.voltages.size == genset_config.total_units
            assert state.rpm.dtype == np.float64
            assert state.temperature.dtype == np.float32
            assert state.coolant_temp.dtype == np.float32
        finally:
            state.close()
            state.unlink()


class TestPVSharedState:
    """Smoke tests for PV shared state."""

    def test_construction(self, pv_config) -> None:
        state = PVSharedState(pv_config, create=True)
        try:
            assert len(state.buffer_names) == 6
            assert state.voltages.size == pv_config.total_units
            assert state.temperature.dtype == np.float32
            assert state.panel_temp.dtype == np.float32
            assert state.current.dtype == np.float64
        finally:
            state.close()
            state.unlink()


# ===================================================================
# Cross-Process Zero-Copy IPC
# ===================================================================


class TestCrossProcessIPC:
    """Validates zero-copy inter-process communication via SHM.

    Child processes use raw ``SharedMemory`` + ``numpy`` (not our wrapper)
    to prove the segments are genuine OS-level shared memory.
    """

    def test_parent_writes_child_reads(self, unique_name: str) -> None:
        """Parent writes voltage[5] = 3.72, child reads it back."""
        size = 10
        buf = SingleDataBuffer(unique_name, size, np.float64, create=True)
        try:
            buf.array[5] = 3.72

            q: multiprocessing.Queue = multiprocessing.Queue()
            p = multiprocessing.Process(
                target=_child_read_shm,
                args=(unique_name, size, "float64", 5, q),
            )
            p.start()
            p.join(timeout=5)

            assert p.exitcode == 0, f"Child process failed (exit={p.exitcode})"
            result = q.get(timeout=1)
            assert result == 3.72
        finally:
            buf.close()
            buf.unlink()

    def test_child_writes_parent_reads(self, unique_name: str) -> None:
        """Child writes voltage[3] = 4.15, parent reads it back."""
        size = 10
        buf = SingleDataBuffer(unique_name, size, np.float64, create=True)
        try:
            assert buf.array[3] == 0.0  # Initially zero

            p = multiprocessing.Process(
                target=_child_write_shm,
                args=(unique_name, size, "float64", 3, 4.15),
            )
            p.start()
            p.join(timeout=5)

            assert p.exitcode == 0, f"Child process failed (exit={p.exitcode})"
            assert buf.array[3] == 4.15
        finally:
            buf.close()
            buf.unlink()

    def test_bess_state_cross_process(self, bess_config) -> None:
        """Full BESS SharedState: parent writes cell 42, child reads via raw SHM."""
        state = BESSSharedState(bess_config, create=True)
        try:
            state.voltages.array[42] = 3.65

            q: multiprocessing.Queue = multiprocessing.Queue()
            p = multiprocessing.Process(
                target=_child_read_shm,
                args=(
                    state.voltages.name,
                    bess_config.total_units,
                    "float64",
                    42,
                    q,
                ),
            )
            p.start()
            p.join(timeout=5)

            assert p.exitcode == 0, f"Child process failed (exit={p.exitcode})"
            result = q.get(timeout=1)
            assert result == 3.65
        finally:
            state.close()
            state.unlink()
