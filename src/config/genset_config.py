# src/config/genset_config.py
"""Genset (Generator Set) configuration models.

NOTE: This module is a stub. Full genset configuration (fuel curves,
rated power, exhaust parameters, etc.) will be implemented in the next
iteration. See iterations.md for planned enhancements.
"""

from pydantic import BaseModel, Field


class GensetConfig(BaseModel):
    """Validated configuration for a single genset unit.

    Stub implementation — only defines the minimum fields required
    by the SHM Manager and Asset Registry.

    Attributes:
        genset_id: Unique identifier for this genset unit.
        num_units: Number of monitored data points.
    """

    genset_id: str
    num_units: int = Field(gt=0)

    @property
    def total_units(self) -> int:
        """Total number of fundamental units for SHM buffer allocation."""
        return self.num_units