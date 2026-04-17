# src/config/settings.py
"""Application-level configuration: operation mode, root config model, and loader.

This module defines the top-level configuration schema for the microgrid
digital twin. It provides:
  - ``OperationMode``: Enum selecting SIMULATION vs TWIN mode.
  - ``MicrogridConfig``: Root Pydantic model aggregating all asset configs.
  - ``MissingConfigurationError``: Raised when config file is absent/unreadable.
  - ``load_config()``: Entry point for parsing and validating a JSON config file.
"""

import json
import logging
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from config.bess_config import BESSConfig
from config.genset_config import GensetConfig
from config.pv_config import PVConfig

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH: Path = Path(__file__).parent / "user" / "simulation.json"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MissingConfigurationError(Exception):
    """Raised when a required configuration file is missing or unreadable."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OperationMode(str, Enum):
    """Selects between synthetic simulation and live hardware telemetry.

    Attributes:
        SIMULATION: Generate synthetic data internally (no hardware needed).
        TWIN: Ingest real-time telemetry from physical hardware via Modbus/CAN.
    """

    SIMULATION = "SIMULATION"
    TWIN = "TWIN"


# ---------------------------------------------------------------------------
# Root Configuration Model
# ---------------------------------------------------------------------------

class MicrogridConfig(BaseModel):
    """Root configuration model for the entire microgrid digital twin.

    Aggregates per-asset configurations and selects the operation mode.
    All asset lists default to empty — a microgrid may contain any
    combination of asset types.

    Attributes:
        mode: Operation mode (SIMULATION or TWIN).
        bess_units: List of BESS configurations.
        genset_units: List of Genset configurations (stub — see iterations.md).
        pv_units: List of PV configurations (stub — see iterations.md).
    """

    mode: OperationMode
    bess_units: list[BESSConfig] = []
    genset_units: list[GensetConfig] = []
    pv_units: list[PVConfig] = []


# ---------------------------------------------------------------------------
# Config Loader
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> MicrogridConfig:
    """Load and validate microgrid configuration from a JSON file.

    Args:
        config_path: Path to the JSON configuration file.

    Returns:
        Validated ``MicrogridConfig`` instance.

    Raises:
        MissingConfigurationError: If the file does not exist or cannot
            be read / decoded.
        pydantic.ValidationError: If the JSON content fails schema
            validation.
    """
    if not config_path.exists():
        raise MissingConfigurationError(
            f"Configuration file not found: {config_path}"
        )

    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise MissingConfigurationError(
            f"Failed to read configuration file '{config_path}': {exc}"
        ) from exc

    config = MicrogridConfig.model_validate(data)
    logger.info(
        "Configuration loaded: mode=%s, bess=%d, gensets=%d, pv=%d",
        config.mode.value,
        len(config.bess_units),
        len(config.genset_units),
        len(config.pv_units),
    )
    return config
