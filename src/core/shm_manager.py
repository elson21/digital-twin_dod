# src/core/shm_manager.py
"""Shared Memory Manager: zero-copy data buffers for the Data Plane.

Provides:
  - ``SingleDataBuffer``: Core SHM primitive wrapping SharedMemory + NumPy.
  - ``BESSSharedState``: Aggregated SHM buffers for a BESS unit.
  - ``GensetSharedState``: Aggregated SHM buffers for a Genset unit.
  - ``PVSharedState``: Aggregated SHM buffers for a PV unit.

All mutable simulation state lives here, in contiguous 1-D NumPy arrays
backed by OS-level shared memory.  Multiple processes (telemetry driver,
physics engine, DB writer) attach to the *same* physical RAM by name —
no data is copied, serialized, or pickled.
"""

from __future__ import annotations

import logging
from multiprocessing.shared_memory import SharedMemory

import numpy as np
from numpy.typing import DTypeLike

from config.bess_config import BESSConfig
from config.genset_config import GensetConfig
from config.pv_config import PVConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core Primitive
# ---------------------------------------------------------------------------


class SingleDataBuffer:
    """Zero-copy shared memory buffer backed by a contiguous 1-D NumPy array.

    Wraps ``multiprocessing.shared_memory.SharedMemory`` to provide a
    high-level NumPy interface over raw OS-managed shared memory.

    The creator process (``create=True``) allocates and zero-initializes
    the segment.  Worker processes (``create=False``) attach to an existing
    segment by name — no data is copied.

    Args:
        name: Unique name for the shared memory segment.
        size: Number of elements in the 1-D array.
        dtype: NumPy-compatible data type (e.g., ``np.float64``).
        create: If True, allocate new shared memory.
            If False, attach to an existing segment.
    """

    def __init__(
        self,
        name: str,
        size: int,
        dtype: DTypeLike,
        create: bool = False,
    ) -> None:
        self._dtype = np.dtype(dtype)
        self._size = size
        self._name = name
        self._closed = False

        nbytes = size * self._dtype.itemsize

        if create:
            self._shm = SharedMemory(name=name, create=True, size=nbytes)
            logger.debug("Created SHM '%s' (%d bytes)", name, nbytes)
        else:
            self._shm = SharedMemory(name=name, create=False)
            logger.debug("Attached to SHM '%s'", name)

        self._array: np.ndarray = np.ndarray(
            shape=(size,), dtype=self._dtype, buffer=self._shm.buf
        )

        if create:
            self._array[:] = 0  # Zero-initialize

    # ----- Properties -----

    @property
    def array(self) -> np.ndarray:
        """The NumPy array view into shared memory."""
        return self._array

    @property
    def name(self) -> str:
        """The OS-level name of this shared memory segment."""
        return self._name

    @property
    def size(self) -> int:
        """Number of elements in the array."""
        return self._size

    @property
    def dtype(self) -> np.dtype:
        """Data type of the array elements."""
        return self._dtype

    # ----- Lifecycle -----

    def close(self) -> None:
        """Detach from the shared memory segment (idempotent).

        Must be called by every process that attached to this buffer.
        Does NOT destroy the underlying memory — use ``unlink()`` for that.
        """
        if not self._closed:
            self._shm.close()
            self._closed = True
            logger.debug("Closed SHM '%s'", self._name)

    def unlink(self) -> None:
        """Destroy the shared memory segment.

        Should be called exactly once (by the creator process) after all
        other processes have called ``close()``.
        """
        self._shm.unlink()
        logger.debug("Unlinked SHM '%s'", self._name)


# ---------------------------------------------------------------------------
# Lifecycle Base
# ---------------------------------------------------------------------------


class _SharedStateBase:
    """Internal base providing lifecycle management for shared memory buffers.

    Not part of the public API.  Subclasses register buffers via
    ``_register()`` and inherit ``close()`` / ``unlink()`` for free.
    """

    def __init__(self) -> None:
        self._buffers: list[SingleDataBuffer] = []

    def _register(
        self,
        name: str,
        size: int,
        dtype: DTypeLike,
        create: bool,
    ) -> SingleDataBuffer:
        """Create or attach to a SingleDataBuffer and track it for cleanup.

        Args:
            name: SHM segment name.
            size: Number of array elements.
            dtype: NumPy data type.
            create: True to allocate, False to attach.

        Returns:
            The registered ``SingleDataBuffer``.
        """
        buf = SingleDataBuffer(name, size, dtype, create)
        self._buffers.append(buf)
        return buf

    def close(self) -> None:
        """Detach from all shared memory segments owned by this state."""
        for buf in self._buffers:
            buf.close()

    def unlink(self) -> None:
        """Destroy all shared memory segments owned by this state."""
        for buf in self._buffers:
            buf.unlink()

    @property
    def buffer_names(self) -> list[str]:
        """Names of all SHM segments managed by this state."""
        return [buf.name for buf in self._buffers]


# ---------------------------------------------------------------------------
# BESS Shared State
# ---------------------------------------------------------------------------


class BESSSharedState(_SharedStateBase):
    """Aggregated shared memory buffers for a single BESS unit.

    Each attribute is a ``SingleDataBuffer`` sized to ``config.total_units``
    (i.e., total cell count).  The naming convention is ``{bess_id}_{param}``.

    Attributes:
        voltages: Cell voltages in volts (float64).
        soc: State of Charge per cell, 0–100% (float64).
        soh: State of Health per cell, 0–100% (float64).
        temperature: Cell temperatures in °C (float32, lower precision).
    """

    def __init__(self, config: BESSConfig, create: bool = False) -> None:
        """Initialize BESS shared state from a validated config.

        Args:
            config: Validated ``BESSConfig`` instance.
            create: True to allocate new SHM, False to attach.
        """
        super().__init__()
        n = config.total_units
        prefix = config.bess_id

        self.voltages = self._register(f"{prefix}_V", n, np.float64, create)
        self.soc = self._register(f"{prefix}_SoC", n, np.float64, create)
        self.soh = self._register(f"{prefix}_SoH", n, np.float64, create)
        self.temperature = self._register(f"{prefix}_Temp", n, np.float32, create)


# ---------------------------------------------------------------------------
# BESS Control Buffer
# ---------------------------------------------------------------------------


class BESSControlBuffer(_SharedStateBase):
    """Small control plane buffer for runtime-adjustable BESS parameters.

    Layout (float64 array):
        [0] load_current_a — system-level load current in amps.

    The supervisor initialises this from config.  Any process can
    update it at runtime to switch between charge/discharge.
    """

    IDX_LOAD_CURRENT: int = 0
    _SIZE: int = 1

    def __init__(self, bess_id: str, create: bool = False) -> None:
        """Initialize BESS control buffer.

        Args:
            bess_id: Unique identifier for this BESS unit.
            create: True to allocate new SHM, False to attach.
        """
        super().__init__()
        self.control = self._register(f"{bess_id}_Ctrl", self._SIZE, np.float64, create)

    @property
    def load_current_a(self) -> float:
        """Current load current setpoint in amps."""
        return float(self.control.array[self.IDX_LOAD_CURRENT])

    @load_current_a.setter
    def load_current_a(self, value: float) -> None:
        self.control.array[self.IDX_LOAD_CURRENT] = value


# ---------------------------------------------------------------------------
# BESS Update Buffer
# ---------------------------------------------------------------------------


class BESSUpdateBuffer(_SharedStateBase):
    """Small update plane buffer for runtime-adjustable BESS macroscopic params.

    Epoch-based lock-free synchronization.
    Layout (structured array):
        [bytes 0-7] epoch (int64)
        [bytes 8-15] capacity_ah (float64)
    """

    def __init__(self, bess_id: str, create: bool = False) -> None:
        super().__init__()
        dt = np.dtype([("epoch", np.int64), ("capacity_ah", np.float64)])
        self.update = self._register(f"{bess_id}_Upd", 1, dt, create)
        if create:
            self.epoch = 0

    @property
    def epoch(self) -> int:
        return int(self.update.array["epoch"][0])

    @epoch.setter
    def epoch(self, value: int) -> None:
        self.update.array["epoch"][0] = value

    @property
    def capacity_ah(self) -> float:
        return float(self.update.array["capacity_ah"][0])

    @capacity_ah.setter
    def capacity_ah(self, value: float) -> None:
        self.update.array["capacity_ah"][0] = value


# ---------------------------------------------------------------------------
# Genset Shared State
# ---------------------------------------------------------------------------


class GensetSharedState(_SharedStateBase):
    """Aggregated shared memory buffers for a single genset unit.

    Attributes:
        voltages: Output voltages per unit (float64).
        rpm: Engine RPM per unit (float64).
        temperature: Engine temperature per unit (float32).
        fuel_level: Fuel level percentage per unit (float64).
        fuel_rate: Fuel consumption rate per unit (float64).
        frequency: Output frequency in Hz per unit (float64).
        coolant_temp: Coolant temperature per unit (float32).
    """

    def __init__(self, config: GensetConfig, create: bool = False) -> None:
        """Initialize Genset shared state from a validated config.

        Args:
            config: Validated ``GensetConfig`` instance.
            create: True to allocate new SHM, False to attach.
        """
        super().__init__()
        n = config.total_units
        prefix = config.genset_id

        self.voltages = self._register(f"{prefix}_V", n, np.float64, create)
        self.rpm = self._register(f"{prefix}_RPM", n, np.float64, create)
        self.temperature = self._register(f"{prefix}_Temp", n, np.float32, create)
        self.fuel_level = self._register(f"{prefix}_Fuel", n, np.float64, create)
        self.fuel_rate = self._register(f"{prefix}_FuelRate", n, np.float64, create)
        self.frequency = self._register(f"{prefix}_Freq", n, np.float64, create)
        self.coolant_temp = self._register(
            f"{prefix}_CoolantTemp", n, np.float32, create
        )


# ---------------------------------------------------------------------------
# PV Shared State
# ---------------------------------------------------------------------------


class PVSharedState(_SharedStateBase):
    """Aggregated shared memory buffers for a single PV array.

    Attributes:
        voltages: Panel voltages per unit (float64).
        current: Panel currents per unit (float64).
        power: Power output per unit (float64).
        temperature: Panel temperature per unit (float32).
        irradiance: Solar irradiance per unit (float64).
        panel_temp: Panel surface temperature per unit (float32).
    """

    def __init__(self, config: PVConfig, create: bool = False) -> None:
        """Initialize PV shared state from a validated config.

        Args:
            config: Validated ``PVConfig`` instance.
            create: True to allocate new SHM, False to attach.
        """
        super().__init__()
        n = config.total_units
        prefix = config.pv_id

        self.voltages = self._register(f"{prefix}_V", n, np.float64, create)
        self.current = self._register(f"{prefix}_I", n, np.float64, create)
        self.power = self._register(f"{prefix}_P", n, np.float64, create)
        self.temperature = self._register(f"{prefix}_Temp", n, np.float32, create)
        self.irradiance = self._register(
            f"{prefix}_Irradiance", n, np.float64, create
        )
        self.panel_temp = self._register(
            f"{prefix}_PanelTemp", n, np.float32, create
        )
