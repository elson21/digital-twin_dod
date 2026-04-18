# src/config/bess_config.py

"""BESS (Battery Energy Storage System) configuration models.

Defines the Pydantic models for BESS topology validation, including
cell-level specifications and system-level layout (strings, packs, cells).
"""

from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class ScalarInit(BaseModel):
    mode: Literal["scalar"] = "scalar"
    soc_pct: float = Field(ge=0, le=100, default=80.0)
    voltage_v: float = Field(ge=0, default=3.3)
    temperature_c: float = Field(ge=-273.15, default=25.0)


class DistributionInit(BaseModel):
    mode: Literal["distribution"] = "distribution"
    soc_mean: float = Field(ge=0, le=100, default=80.0)
    soc_std: float = 2.0
    voltage_mean: float = 3.3
    voltage_std: float = 0.05
    temperature_mean: float = 25.0
    temperature_std: float = 2.0


class CellConfig(BaseModel):
    """Manufacturer specification for a single battery cell type.

    Attributes:
        name: Human-readable cell model identifier.
        nominal_voltage: Nominal cell voltage in volts (V).
        nominal_capacity: Rated capacity in ampere-hours (Ah).
        nominal_current: Rated continuous current in amperes (A).
        temperature_min: Minimum operating temperature in °C.
        temperature_max: Maximum operating temperature in °C.
    """

    name: str
    nominal_voltage: float
    nominal_capacity: float
    nominal_current: float
    temperature_min: float
    temperature_max: float


class BESSConfig(BaseModel):
    """Validated configuration for a single BESS unit.

    Defines the physical topology (strings * packs * cells) and the
    cell specification. Computed properties provide derived counts used
    by the SHM Manager to size memory buffers.

    Attributes:
        bess_id: Unique identifier for this BESS unit.
        num_strings: Number of parallel strings.
        packs_per_string: Number of series packs per string.
        cells_per_pack: Number of series cells per pack.
        load_current_a: Applied load current in amperes.
            Positive = discharge, negative = charge.
        manufacturer_metadata: Freeform vendor metadata.
        cell_spec: Cell-level electrical and thermal specification.
    """

    bess_id: str
    num_strings: int = Field(gt=0)
    packs_per_string: int = Field(gt=0)
    cells_per_pack: int = Field(gt=0)
    load_current_a: float
    manufacturer_metadata: dict[str, Any]
    cell_spec: CellConfig
    initial_state: Union[ScalarInit, DistributionInit] = Field(
        default_factory=ScalarInit, discriminator="mode"
    )

    @property
    def total_strings(self) -> int:
        """Total number of strings in this BESS."""
        return self.num_strings

    @property
    def total_packs(self) -> int:
        """Total number of packs across all strings."""
        return self.num_strings * self.packs_per_string

    @property
    def total_cells(self) -> int:
        """Total number of cells across all strings and packs."""
        return self.num_strings * self.packs_per_string * self.cells_per_pack

    @property
    def total_units(self) -> int:
        """Total number of fundamental units for SHM buffer allocation.

        For BESS, the fundamental unit is the individual cell. This property
        provides a consistent interface with GensetConfig and PVConfig.
        """
        return self.total_cells