# src/services/db_writer.py
"""Asynchronous Observer: batched, non-blocking SHM persistence.

The DB Writer is a passive observer that sits *beside* the physics engine,
watching shared memory through a one-way mirror.  It never locks or
synchronises with producers — it copies SHM arrays into process-local
memory (``ndarray.copy()``) as quickly as possible, then performs slow
CSV formatting on those local copies.

Two distinct CSV streams are produced per BESS unit:

- **Summary CSV** (``{bess_id}_summary.csv``): PLC Dashboard metrics —
  total string voltage, system-level SoC, thermal extremes.
- **Detail CSV** (``{bess_id}_detail.csv``): Per-cell state — voltage,
  SoC, SoH, temperature for every cell index.

Rows are collected in an internal buffer and flushed in a single
``writerows()`` call every ``_FLUSH_INTERVAL`` snapshots to minimise
syscall overhead and disk I/O.
"""

from __future__ import annotations

import csv
import logging
import multiprocessing
import time
from pathlib import Path

import numpy as np

from config.settings import load_config
from core.registry import AssetRegistry
from core.shm_manager import BESSSharedState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUMMARY_FIELDS: list[str] = [
    "timestamp",
    "bess_id",
    "total_voltage_v",
    "mean_soc_pct",
    "mean_soh_pct",
    "max_temp_c",
    "min_temp_c",
    "mean_temp_c",
]

_DETAIL_FIELDS: list[str] = [
    "timestamp",
    "cell_index",
    "voltage_v",
    "soc_pct",
    "soh_pct",
    "temp_c",
]

_FLUSH_INTERVAL: int = 10  # Flush every N snapshots


# ---------------------------------------------------------------------------
# Per-BESS Writer Context
# ---------------------------------------------------------------------------


class _BESSWriterContext:
    """Holds open file handles, csv.writer instances, and row buffers for one BESS.

    File handles are opened **once** at construction and kept open for the
    process lifetime.  The ``close()`` method guarantees a final ``flush()``
    so no data is lost during shutdown.

    Attributes:
        bess_id: The BESS identifier (used in CSV rows).
        state: Attached SHM reference (read-only by this process).
        num_cells: Total cell count, cached from config.
    """

    def __init__(
        self,
        bess_id: str,
        state: BESSSharedState,
        num_cells: int,
        output_dir: Path,
    ) -> None:
        self.bess_id = bess_id
        self.state = state
        self.num_cells = num_cells

        # --- Summary CSV (open once) ---
        summary_path = output_dir / f"{bess_id}_summary.csv"
        write_summary_header = not summary_path.exists() or summary_path.stat().st_size == 0
        self._summary_fh = open(summary_path, "a", newline="", encoding="utf-8")
        self.summary_writer = csv.writer(self._summary_fh)
        if write_summary_header:
            self.summary_writer.writerow(_SUMMARY_FIELDS)
            self._summary_fh.flush()
        self.summary_buffer: list[list[str]] = []

        # --- Detail CSV (open once) ---
        detail_path = output_dir / f"{bess_id}_detail.csv"
        write_detail_header = not detail_path.exists() or detail_path.stat().st_size == 0
        self._detail_fh = open(detail_path, "a", newline="", encoding="utf-8")
        self.detail_writer = csv.writer(self._detail_fh)
        if write_detail_header:
            self.detail_writer.writerow(_DETAIL_FIELDS)
            self._detail_fh.flush()
        self.detail_buffer: list[list[str]] = []

    def flush(self) -> None:
        """Write buffered rows to disk and flush OS buffers."""
        if self.summary_buffer:
            self.summary_writer.writerows(self.summary_buffer)
            self.summary_buffer.clear()
        if self.detail_buffer:
            self.detail_writer.writerows(self.detail_buffer)
            self.detail_buffer.clear()
        self._summary_fh.flush()
        self._detail_fh.flush()

    def close(self) -> None:
        """Final flush + close file handles.  Idempotent."""
        self.flush()
        self._summary_fh.close()
        self._detail_fh.close()


# ---------------------------------------------------------------------------
# Snapshot (zero-intrusion SHM copy → local formatting)
# ---------------------------------------------------------------------------


def _snapshot_bess(ctx: _BESSWriterContext, timestamp: str) -> None:
    """Copy SHM arrays into local memory, then format rows from the copies.

    The four ``.copy()`` calls are the **only** window during which this
    process touches shared memory.  Each copy is a single ``memcpy`` of a
    contiguous C array — sub-microsecond for typical cell counts.  All
    subsequent string formatting operates on the local copies.

    Args:
        ctx: The writer context for this BESS (buffers, writers, state).
        timestamp: Pre-formatted timestamp string.
    """
    # --- 1. Fast SHM snapshot (no locks, pure memcpy) ---
    v_local: np.ndarray = ctx.state.voltages.array.copy()
    soc_local: np.ndarray = ctx.state.soc.array.copy()
    soh_local: np.ndarray = ctx.state.soh.array.copy()
    temp_local: np.ndarray = ctx.state.temperature.array.copy()

    # --- 2. Summary row (PLC Dashboard — vectorized reductions) ---
    ctx.summary_buffer.append([
        timestamp,
        ctx.bess_id,
        f"{np.sum(v_local):.4f}",       # Total string voltage
        f"{np.mean(soc_local):.4f}",     # System-level SoC
        f"{np.mean(soh_local):.2f}",     # System-level SoH
        f"{np.max(temp_local):.2f}",     # Thermal safety: max
        f"{np.min(temp_local):.2f}",     # Thermal safety: min
        f"{np.mean(temp_local):.2f}",    # Avg temperature
    ])

    # --- 3. Detail rows (per-cell state from local copies) ---
    for i in range(ctx.num_cells):
        ctx.detail_buffer.append([
            timestamp,
            str(i),
            f"{v_local[i]:.4f}",
            f"{soc_local[i]:.4f}",
            f"{soh_local[i]:.2f}",
            f"{temp_local[i]:.2f}",
        ])


# ---------------------------------------------------------------------------
# Process Entry Point
# ---------------------------------------------------------------------------


def db_writer_loop(
    config_path: str,
    output_dir: str,
    interval: float,
    shutdown_event: multiprocessing.Event,
) -> None:
    """DB writer process entry point: periodic SHM snapshots to CSV.

    Loads config independently (no pickling), attaches to all BESS SHM
    segments by name, and runs a snapshot-and-batch loop until the
    shutdown event is set.

    Args:
        config_path: Path to the JSON config file (string).
        output_dir: Directory to write CSV files into.
        interval: Seconds between snapshots.
        shutdown_event: Event to signal graceful shutdown.
    """
    # --- Independent config load (same pattern as physics engine) ---
    config = load_config(Path(config_path))
    registry = AssetRegistry(config)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # --- Build per-BESS writer contexts ---
    contexts: list[_BESSWriterContext] = []
    for bess_id in registry.bess_ids:
        cfg = registry.get_bess(bess_id)
        state = BESSSharedState(cfg, create=False)
        contexts.append(
            _BESSWriterContext(bess_id, state, cfg.total_units, output_path)
        )

    logger.info(
        "DB writer started | interval=%.1fs | output=%s | bess_units=%d",
        interval,
        output_path,
        len(contexts),
    )

    snapshot_count = 0
    try:
        while not shutdown_event.is_set():
            timestamp = f"{time.time():.3f}"

            for ctx in contexts:
                _snapshot_bess(ctx, timestamp)
            snapshot_count += 1

            # Periodic batched flush
            if snapshot_count >= _FLUSH_INTERVAL:
                for ctx in contexts:
                    ctx.flush()
                snapshot_count = 0

            shutdown_event.wait(timeout=interval)
    except Exception as exc:
        logger.error("DB writer error: %s", exc)
    finally:
        # Atomic final flush — no data loss on shutdown
        for ctx in contexts:
            ctx.close()
            ctx.state.close()
        logger.info("DB writer stopped")