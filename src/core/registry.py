# src/core/registry.py
"""Asset Registry: discovers and catalogs all configured microgrid assets.

Provides lookup by asset ID and type. In future phases, this will also
manage SHM key mappings for each registered asset.
"""

from __future__ import annotations

import logging

from config.bess_config import BESSConfig
from config.genset_config import GensetConfig
from config.pv_config import PVConfig
from config.settings import MicrogridConfig

logger = logging.getLogger(__name__)


class AssetRegistry:
    """Discovers and catalogs all microgrid assets from configuration.

    On construction, scans the provided ``MicrogridConfig`` and registers
    every asset by its unique ID. Duplicate IDs within the same asset type
    raise ``ValueError``.

    Attributes:
        mode: The operation mode from the loaded config.
    """

    def __init__(self, config: MicrogridConfig) -> None:
        """Initialize the registry from a validated microgrid config.

        Args:
            config: Validated ``MicrogridConfig`` instance.

        Raises:
            ValueError: If duplicate asset IDs are found within a type.
        """
        self._config = config
        self.mode = config.mode
        self._bess: dict[str, BESSConfig] = {}
        self._gensets: dict[str, GensetConfig] = {}
        self._pvs: dict[str, PVConfig] = {}
        self._discover()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Scan config and register all assets by ID."""
        for bess in self._config.bess_units:
            if bess.bess_id in self._bess:
                raise ValueError(f"Duplicate BESS ID: {bess.bess_id}")
            self._bess[bess.bess_id] = bess
            logger.info(
                "Registered BESS: %s (%d cells)", bess.bess_id, bess.total_cells
            )

        for genset in self._config.genset_units:
            if genset.genset_id in self._gensets:
                raise ValueError(f"Duplicate Genset ID: {genset.genset_id}")
            self._gensets[genset.genset_id] = genset
            logger.info("Registered Genset: %s", genset.genset_id)

        for pv in self._config.pv_units:
            if pv.pv_id in self._pvs:
                raise ValueError(f"Duplicate PV ID: {pv.pv_id}")
            self._pvs[pv.pv_id] = pv
            logger.info("Registered PV: %s", pv.pv_id)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_bess(self, bess_id: str) -> BESSConfig:
        """Look up a BESS config by ID.

        Args:
            bess_id: The BESS identifier.

        Returns:
            The ``BESSConfig`` for the given ID.

        Raises:
            KeyError: If no BESS with the given ID is registered.
        """
        return self._bess[bess_id]

    def get_genset(self, genset_id: str) -> GensetConfig:
        """Look up a Genset config by ID.

        Args:
            genset_id: The Genset identifier.

        Returns:
            The ``GensetConfig`` for the given ID.

        Raises:
            KeyError: If no Genset with the given ID is registered.
        """
        return self._gensets[genset_id]

    def get_pv(self, pv_id: str) -> PVConfig:
        """Look up a PV config by ID.

        Args:
            pv_id: The PV identifier.

        Returns:
            The ``PVConfig`` for the given ID.

        Raises:
            KeyError: If no PV with the given ID is registered.
        """
        return self._pvs[pv_id]

    # ------------------------------------------------------------------
    # Enumeration
    # ------------------------------------------------------------------

    @property
    def bess_ids(self) -> list[str]:
        """All registered BESS IDs."""
        return list(self._bess.keys())

    @property
    def genset_ids(self) -> list[str]:
        """All registered Genset IDs."""
        return list(self._gensets.keys())

    @property
    def pv_ids(self) -> list[str]:
        """All registered PV IDs."""
        return list(self._pvs.keys())

    @property
    def total_assets(self) -> int:
        """Total number of registered assets across all types."""
        return len(self._bess) + len(self._gensets) + len(self._pvs)