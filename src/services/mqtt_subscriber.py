# src/services/mqtt_subscriber.py
"""MQTT Subscriber: Cloud-to-Edge parameter update pipeline.

Closes the feedback loop in the Edge Digital Twin by listening for
cloud-dispatched configuration updates and applying them directly to
shared memory so the physics engine picks them up on its next tick.

The subscriber attaches to ``BESSUpdateBuffer`` (``create=False``) —
the supervisor owns the allocation — and reacts to messages on:

    ``cloud/updates/{bess_id}``

Expected JSON payload schema (all fields optional; unknown keys ignored)::

    {
        "capacity_ah": 120.5   // New nominal cell capacity in Amp-hours
    }

On receipt of a valid ``capacity_ah`` update:

1. ``upd.capacity_ah`` is written atomically to the SHM float64 slot.
2. ``upd.epoch`` is incremented by 1.

The epoch increment is the lock-free signal that tells the physics engine
a parameter change is pending — it will reload on its next cycle without
any explicit IPC or locking.
"""

from __future__ import annotations

import json
import logging
from multiprocessing import Event
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from config.settings import load_config
from core.registry import AssetRegistry
from core.shm_manager import BESSUpdateBuffer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MQTT callbacks (module-level factories so they close over the buffer ref)
# ---------------------------------------------------------------------------


def _make_on_message(upd: BESSUpdateBuffer, bess_id: str, topic: str):
    """Return an ``on_message`` callback bound to *upd* for *bess_id*."""

    def on_message(
        client: mqtt.Client,
        userdata: object,
        message: mqtt.MQTTMessage,
    ) -> None:
        """Parse incoming JSON and apply recognised parameter updates to SHM."""
        try:
            payload_str = message.payload.decode("utf-8")
            data: dict = json.loads(payload_str)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning(
                "MQTT subscriber [%s]: invalid payload on '%s' — %s",
                bess_id,
                message.topic,
                exc,
            )
            return

        if "capacity_ah" in data:
            try:
                new_capacity = float(data["capacity_ah"])
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "MQTT subscriber [%s]: bad capacity_ah value '%s' — %s",
                    bess_id,
                    data["capacity_ah"],
                    exc,
                )
                return

            upd.capacity_ah = new_capacity
            upd.epoch += 1

            logger.info(
                "MQTT subscriber [%s]: capacity_ah updated to %.4f Ah (epoch=%d)",
                bess_id,
                new_capacity,
                upd.epoch,
            )
        else:
            logger.debug(
                "MQTT subscriber [%s]: received message with no recognised keys — %s",
                bess_id,
                list(data.keys()),
            )

    return on_message


def _on_connect(
    client: mqtt.Client,
    userdata: object,
    flags: dict,
    rc: int,
) -> None:
    """Re-subscribe on (re-)connect to survive broker restarts."""
    if rc == 0:
        topic: str = userdata["topic"]
        client.subscribe(topic, qos=1)
        logger.info(
            "MQTT subscriber connected and subscribed to '%s'", topic
        )
    else:
        logger.warning("MQTT subscriber broker connection failed (rc=%d)", rc)


def _on_disconnect(
    client: mqtt.Client,
    userdata: object,
    rc: int,
) -> None:
    if rc == 0:
        logger.info("MQTT subscriber disconnected cleanly")
    else:
        logger.warning("MQTT subscriber unexpected disconnect (rc=%d)", rc)


# ---------------------------------------------------------------------------
# Process Entry Point
# ---------------------------------------------------------------------------


def mqtt_subscriber_loop(
    config_path: str,
    bess_id: str,
    broker_host: str,
    broker_port: int,
    shutdown_event: Event,
) -> None:
    """MQTT subscriber process entry point: Cloud-to-Edge parameter updates.

    Loads configuration independently, attaches to the pre-allocated
    ``BESSUpdateBuffer`` for *bess_id*, then blocks in an MQTT network
    loop until *shutdown_event* is set.

    The paho ``loop_start()`` / ``loop_stop()`` pattern is used so that
    the main thread is free to poll ``shutdown_event`` cleanly without
    relying on ``loop_forever()``'s internal threading.

    Args:
        config_path: Absolute or relative path to the JSON config file.
        bess_id: Identifier of the BESS unit whose SHM to attach.
        broker_host: Hostname or IP of the MQTT broker (e.g. ``"127.0.0.1"``).
        broker_port: TCP port of the MQTT broker (e.g. ``1883``).
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

    # --- Independent config load ---
    config = load_config(Path(config_path))
    registry = AssetRegistry(config)
    # Validate bess_id is known before touching SHM
    _ = registry.get_bess(bess_id)

    topic: str = f"cloud/updates/{bess_id}"

    # --- Attach to pre-existing BESSUpdateBuffer (supervisor owns lifecycle) ---
    upd = BESSUpdateBuffer(bess_id, create=False)
    logger.info(
        "MQTT subscriber attached to BESSUpdateBuffer for BESS '%s' | topic=%s | broker=%s:%d",
        bess_id,
        topic,
        broker_host,
        broker_port,
    )

    # --- Build MQTT client ---
    # userdata carries the topic so _on_connect can re-subscribe on reconnect
    client = mqtt.Client(
        client_id=f"mqtt_sub_{bess_id}",
        clean_session=True,
        userdata={"topic": topic},
    )
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _make_on_message(upd, bess_id, topic)

    try:
        client.connect(broker_host, broker_port, keepalive=60)
        client.loop_start()
    except Exception as exc:
        logger.error(
            "MQTT subscriber failed to connect to %s:%d — %s",
            broker_host,
            broker_port,
            exc,
        )
        upd.close()
        return

    logger.info("MQTT subscriber started | bess_id=%s", bess_id)

    # --- Poll shutdown event (loop_start handles the network in background) ---
    try:
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except Exception as exc:
        logger.error("MQTT subscriber error for BESS '%s': %s", bess_id, exc)
    finally:
        client.loop_stop()
        client.disconnect()
        upd.close()
        logger.info("MQTT subscriber stopped for BESS '%s'", bess_id)
