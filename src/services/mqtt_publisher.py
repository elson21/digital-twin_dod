# src/services/mqtt_publisher.py
"""MQTT Publisher: high-throughput telemetry pipeline over the network.

Replaces the disk-based DB writer with a lightweight, low-latency MQTT
publish loop.  The publisher is a passive observer — it attaches to
existing BESSSharedState buffers (``create=False``) and performs fast,
lock-free ``ndarray.copy()`` snapshots before computing macro stats.

Macro-level statistics published per interval:

- **System Voltage** (V): sum of all cell voltages divided by number of strings.
- **System SoC** (%): arithmetic mean of all cell SoCs.
- **Max Temperature** (°C): global thermal ceiling across all cells.

Each payload is a compact JSON object published to:
    ``edge/telemetry/{bess_id}``
with QoS 0 (fire-and-forget), optimised for throughput over delivery
guarantees — appropriate for high-frequency telemetry streams.
"""

from __future__ import annotations

import json
import logging
from multiprocessing import Event
import time
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

from config.settings import load_config
from core.registry import AssetRegistry
from core.shm_manager import BESSSharedState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------


def _on_connect(
    client: mqtt.Client,
    userdata: object,
    flags: dict,
    rc: int,
) -> None:
    """Log MQTT broker connection result."""
    if rc == 0:
        logger.info("MQTT publisher connected to broker (rc=0)")
    else:
        logger.warning("MQTT broker connection failed (rc=%d)", rc)


def _on_disconnect(
    client: mqtt.Client,
    userdata: object,
    rc: int,
) -> None:
    """Log MQTT broker disconnection."""
    if rc == 0:
        logger.info("MQTT publisher disconnected cleanly")
    else:
        logger.warning("MQTT publisher unexpected disconnect (rc=%d)", rc)


# ---------------------------------------------------------------------------
# Process Entry Point
# ---------------------------------------------------------------------------


def mqtt_publisher_loop(
    config_path: str,
    bess_id: str,
    broker_host: str,
    broker_port: int,
    interval: float,
    shutdown_event: Event,
) -> None:
    """MQTT publisher process entry point: periodic SHM snapshots to broker.

    Loads the system configuration independently (no pickling), attaches to
    the ``BESSSharedState`` for ``bess_id`` by name (``create=False``), then
    enters a publish loop until ``shutdown_event`` is set.

    The loop computes three macro-level statistics per iteration:

    - ``system_voltage_v``: ``sum(cell_voltages) / num_strings``
    - ``system_soc_pct``: ``mean(cell_socs)``
    - ``max_temp_c``: ``max(cell_temps)``

    These are serialised as JSON and published to
    ``edge/telemetry/{bess_id}`` with QoS 0.

    Args:
        config_path: Absolute or relative path to the JSON config file.
        bess_id: Identifier of the BESS unit whose SHM to attach.
        broker_host: Hostname or IP of the MQTT broker (e.g. "127.0.0.1").
        broker_port: TCP port of the MQTT broker (e.g. 1883).
        interval: Seconds between publish cycles.
        shutdown_event: Multiprocessing event; loop exits when set.
    """
    # --- Configure logging for this child process ---
    # On Windows, multiprocessing uses 'spawn': each worker starts a fresh
    # interpreter with no logging handlers. basicConfig() must be called
    # here so that INFO messages are not silently dropped.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-26s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Independent config load (avoids pickling complex objects) ---
    config = load_config(Path(config_path))
    registry = AssetRegistry(config)
    cfg = registry.get_bess(bess_id)

    num_strings: int = cfg.num_strings
    topic: str = f"edge/telemetry/{bess_id}"

    # --- Attach to pre-existing shared memory (supervisor owns lifecycle) ---
    state = BESSSharedState(cfg, create=False)
    logger.info(
        "MQTT publisher attached to SHM for BESS '%s' | topic=%s | broker=%s:%d",
        bess_id,
        topic,
        broker_host,
        broker_port,
    )

    # --- Build and connect MQTT client ---
    client = mqtt.Client(client_id=f"mqtt_pub_{bess_id}", clean_session=True)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect

    try:
        client.connect(broker_host, broker_port, keepalive=60)
        client.loop_start()  # Starts background network thread
    except Exception as exc:
        logger.error(
            "MQTT publisher failed to connect to %s:%d — %s",
            broker_host,
            broker_port,
            exc,
        )
        state.close()
        return

    # --- Publish loop ---
    logger.info(
        "MQTT publisher started | bess_id=%s | interval=%.2fs", bess_id, interval
    )
    try:
        while not shutdown_event.is_set():
            # Fast, lock-free SHM snapshots (single memcpy per array)
            v_local: np.ndarray = state.voltages.array.copy()
            soc_local: np.ndarray = state.soc.array.copy()
            temp_local: np.ndarray = state.temperature.array.copy()

            # Macro-level statistics
            system_voltage_v: float = float(v_local.sum()) / num_strings
            system_soc_pct: float = float(np.mean(soc_local))
            max_temp_c: float = float(np.max(temp_local))

            payload: str = json.dumps(
                {
                    "bess_id": bess_id,
                    "timestamp": f"{time.time():.3f}",
                    "system_voltage_v": round(system_voltage_v, 4),
                    "system_soc_pct": round(system_soc_pct, 4),
                    "max_temp_c": round(max_temp_c, 2),
                }
            )

            result = client.publish(topic, payload, qos=0)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(
                    "MQTT publish failed for topic '%s' (rc=%d)", topic, result.rc
                )

            shutdown_event.wait(timeout=interval)

    except Exception as exc:
        logger.error("MQTT publisher error for BESS '%s': %s", bess_id, exc)
    finally:
        # --- Graceful teardown ---
        client.loop_stop()
        client.disconnect()
        state.close()
        logger.info("MQTT publisher stopped for BESS '%s'", bess_id)
