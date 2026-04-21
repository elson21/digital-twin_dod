# src/drivers/modbus_engine.py
"""Modbus TCP Driver for the Edge Digital Twin.

Binds to a TCP port and acts as a Modbus Slave/Server.
"""

import logging
import multiprocessing
import struct
from pathlib import Path

# These imports are standard and stable in 3.6.8
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
from pymodbus.server import StartTcpServer

from config.settings import load_config
from core.registry import AssetRegistry
from core.shm_manager import BESSControlBuffer

logger = logging.getLogger(__name__)

class BESSDataBlock(ModbusSequentialDataBlock):
    """Holding-register block that intercepts writes to the current setpoint.

    The float32 load current occupies registers 0-1 (two 16-bit words).
    The EMS must write to address=0 with two registers (Big-Endian FLOAT32).

    NOTE: ModbusSlaveContext is created with zero_mode=True so that pymodbus
    does NOT silently add +1 to the incoming PDU address before calling
    setValues().  With zero_mode=False (the default) a master write to
    PDU addr 0 would arrive here as address=1, causing the check to fail.
    """

    def __init__(self, address, values, ctrl_buffer):
        super().__init__(address, values)
        self.ctrl = ctrl_buffer

    def setValues(self, address, values):
        super().setValues(address, values)

        # Log every write for diagnostics
        logger.info("Modbus Write -> Address: %s, Values: %s", address, values)

        # The current setpoint occupies the first two registers (offset 0).
        if address == 0 and len(values) >= 2:
            try:
                packed_bytes = struct.pack('>HH', values[0], values[1])
                commanded_current = struct.unpack('>f', packed_bytes)[0]
                self.ctrl.load_current_a = commanded_current
                logger.info("SUCCESS: SHM Updated! Current = %.2fA", commanded_current)
            except Exception as e:
                logger.error("Failed to decode Modbus payload: %s", e)


def modbus_server_loop(config_path, bess_id, port, shutdown_event):
    config = load_config(Path(config_path))
    ctrl = BESSControlBuffer(bess_id, create=False)

    # Datablock at address 0, zero_mode=True disables the hidden +1 offset
    # that ModbusSlaveContext normally applies to incoming PDU addresses.
    store = ModbusSlaveContext(
        hr=BESSDataBlock(0, [0] * 100, ctrl_buffer=ctrl),
        zero_mode=True,
    )
    context = ModbusServerContext(slaves=store, single=True)

    logger.info("Modbus TCP Server starting for '%s' on port %d...", bess_id, port)
    StartTcpServer(context=context, address=("0.0.0.0", port))
    ctrl.close()