# src/services/db_writer.py
"""Batched non-blocking database persistence.

Periodically snapshots SHM state and appends to CSV files.
Runs in its own process to avoid blocking the physics loop.
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


def db_writer_loop(
    config_path: str,
    output_dir: str,
    interval: float,
    shutdown_event: multiprocessing.Event,
) -> None:
    """DB writer process entry point: periodic SHM snapshots to CSV.

    Loads config independently, attaches to all BESS SHM segments, and
    writes summary statistics at the given interval.

    Args:
        config_path: Path to the JSON config file (string).
        output_dir: Directory to write CSV files into.
        interval: Seconds between snapshots.
        shutdown_event: Event to signal graceful shutdown.
    """
    config = load_config(Path(config_path))
    registry = AssetRegistry(config)

    bess_states: dict[str, BESSSharedState] = {}
    for bess_id in registry.bess_ids:
        cfg = registry.get_bess(bess_id)
        bess_states[bess_id] = BESSSharedState(cfg, create=False)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    logger.info(
        "DB writer started | interval=%.1fs | output=%s", interval, output_path
    )

    try:
        while not shutdown_event.is_set():
            timestamp = time.time()
            for bess_id, state in bess_states.items():
                _write_bess_snapshot(output_path, bess_id, timestamp, state)
            shutdown_event.wait(timeout=interval)
    except Exception as exc:
        logger.error("DB writer error: %s", exc)
    finally:
        for state in bess_states.values():
            state.close()
        logger.info("DB writer stopped")


def _write_bess_snapshot(
    output_dir: Path,
    bess_id: str,
    timestamp: float,
    state: BESSSharedState,
) -> None:
    """Append a single BESS snapshot row to its CSV log.

    Args:
        output_dir: Directory containing CSV files.
        bess_id: BESS identifier (used as filename prefix).
        timestamp: Unix timestamp of the snapshot.
        state: The BESS shared state to read from.
    """
    csv_path = output_dir / f"{bess_id}_log.csv"
    write_header = not csv_path.exists()

    row = {
        "timestamp": f"{timestamp:.3f}",
        "bess_id": bess_id,
        "mean_voltage_v": f"{float(np.mean(state.voltages.array)):.4f}",
        "min_voltage_v": f"{float(np.min(state.voltages.array)):.4f}",
        "max_voltage_v": f"{float(np.max(state.voltages.array)):.4f}",
        "mean_soc_pct": f"{float(np.mean(state.soc.array)):.4f}",
        "mean_soh_pct": f"{float(np.mean(state.soh.array)):.2f}",
        "mean_temp_c": f"{float(np.mean(state.temperature.array)):.2f}",
    }

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)