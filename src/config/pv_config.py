# src/config/pv_config.py
"""PV (Photovoltaic) configuration models.

NOTE: This module is a stub. Full PV configuration (panel specs, inverter
ratings, string topology, etc.) will be implemented in the next iteration.
See iterations.md for planned enhancements.
"""

from pydantic import BaseModel, Field


class PVConfig(BaseModel):
    """Validated configuration for a single PV array.

    Stub implementation — only defines the minimum fields required
    by the SHM Manager and Asset Registry.

    Attributes:
        pv_id: Unique identifier for this PV array.
        num_units: Number of monitored data points.
    """

    pv_id: str
    num_units: int = Field(gt=0)

    @property
    def total_units(self) -> int:
        """Total number of fundamental units for SHM buffer allocation."""
        return self.num_units