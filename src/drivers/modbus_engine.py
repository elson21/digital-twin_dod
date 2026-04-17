# src/drivers/modbus_engine.py
"""Async Modbus client for hardware telemetry ingestion.

NOTE: This module is a stub.  In TWIN mode, this will be an ``asyncio``
loop that polls physical hardware over Modbus TCP/RTU and writes raw
register values into the SharedState telemetry buffers.

In SIMULATION mode, the physics engine handles everything directly —
this driver is not spawned.

Planned interface (Phase 5+):
    ``modbus_driver_loop(config_path, asset_id, shutdown_event)``
"""