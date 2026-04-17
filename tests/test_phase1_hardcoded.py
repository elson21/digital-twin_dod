# tests/test_phase1_config.py
"""Phase 1 Tests: Configuration loading, Pydantic validation, and Asset Registry.

Verifies:
  - CellConfig and BESSConfig instantiation and computed properties.
  - MicrogridConfig root model with mode selection.
  - load_config() JSON round-trip, missing-file error, malformed JSON.
  - AssetRegistry discovery, lookup, and duplicate-ID rejection.
"""

import json
from pathlib import Path

import pytest

from config.bess_config import BESSConfig, CellConfig
from config.genset_config import GensetConfig
from config.pv_config import PVConfig
from config.settings import (
    MicrogridConfig,
    MissingConfigurationError,
    OperationMode,
    load_config,
)
from core.registry import AssetRegistry


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_CELL_SPEC: dict = {
    "name": "Samsung SDI 94Ah",
    "nominal_voltage": 3.68,
    "nominal_capacity": 94.0,
    "nominal_current": 94.0,
    "temperature_min": -20.0,
    "temperature_max": 55.0,
}

VALID_BESS: dict = {
    "bess_id": "BESS_01",
    "num_strings": 2,
    "packs_per_string": 4,
    "cells_per_pack": 12,
    "load_current_a": 50.0,
    "manufacturer_metadata": {"manufacturer": "Samsung SDI", "model": "ESS Module"},
    "cell_spec": VALID_CELL_SPEC,
}

VALID_CONFIG: dict = {
    "mode": "SIMULATION",
    "bess_units": [VALID_BESS],
}


# ===================================================================
# CellConfig
# ===================================================================


class TestCellConfig:
    """Tests for CellConfig Pydantic model."""

    def test_valid_cell(self) -> None:
        cell = CellConfig(**VALID_CELL_SPEC)
        assert cell.nominal_voltage == 3.68
        assert cell.nominal_capacity == 94.0
        assert cell.name == "Samsung SDI 94Ah"

    def test_missing_field_raises(self) -> None:
        from pydantic import ValidationError

        incomplete = {k: v for k, v in VALID_CELL_SPEC.items() if k != "name"}
        with pytest.raises(ValidationError):
            CellConfig(**incomplete)


# ===================================================================
# BESSConfig
# ===================================================================


class TestBESSConfig:
    """Tests for BESSConfig Pydantic model and computed properties."""

    def test_valid_bess(self) -> None:
        bess = BESSConfig(**VALID_BESS)
        assert bess.bess_id == "BESS_01"
        assert bess.num_strings == 2
        assert bess.packs_per_string == 4
        assert bess.cells_per_pack == 12

    def test_total_cells(self) -> None:
        bess = BESSConfig(**VALID_BESS)
        assert bess.total_cells == 2 * 4 * 12  # 96

    def test_total_packs(self) -> None:
        bess = BESSConfig(**VALID_BESS)
        assert bess.total_packs == 2 * 4  # 8

    def test_total_strings(self) -> None:
        bess = BESSConfig(**VALID_BESS)
        assert bess.total_strings == 2

    def test_total_units_equals_total_cells(self) -> None:
        """total_units is the SHM buffer size — must equal total_cells."""
        bess = BESSConfig(**VALID_BESS)
        assert bess.total_units == bess.total_cells

    def test_load_current(self) -> None:
        bess = BESSConfig(**VALID_BESS)
        assert bess.load_current_a == 50.0

    def test_negative_load_current_is_charge(self) -> None:
        data = {**VALID_BESS, "load_current_a": -30.0}
        bess = BESSConfig(**data)
        assert bess.load_current_a == -30.0

    def test_missing_cell_spec_raises(self) -> None:
        from pydantic import ValidationError

        incomplete = {k: v for k, v in VALID_BESS.items() if k != "cell_spec"}
        with pytest.raises(ValidationError):
            BESSConfig(**incomplete)

    def test_zero_strings_raises(self) -> None:
        from pydantic import ValidationError

        data = {**VALID_BESS, "num_strings": 0}
        with pytest.raises(ValidationError):
            BESSConfig(**data)

    def test_negative_packs_raises(self) -> None:
        from pydantic import ValidationError

        data = {**VALID_BESS, "packs_per_string": -1}
        with pytest.raises(ValidationError):
            BESSConfig(**data)


# ===================================================================
# GensetConfig / PVConfig (Stubs)
# ===================================================================


class TestGensetConfig:
    """Smoke test for the GensetConfig stub."""

    def test_valid_genset(self) -> None:
        genset = GensetConfig(genset_id="GEN_01", num_units=3)
        assert genset.genset_id == "GEN_01"
        assert genset.total_units == 3

    def test_zero_units_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            GensetConfig(genset_id="GEN_01", num_units=0)


class TestPVConfig:
    """Smoke test for the PVConfig stub."""

    def test_valid_pv(self) -> None:
        pv = PVConfig(pv_id="PV_01", num_units=10)
        assert pv.pv_id == "PV_01"
        assert pv.total_units == 10


# ===================================================================
# OperationMode
# ===================================================================


class TestOperationMode:
    """Tests for the OperationMode enum."""

    def test_simulation_mode(self) -> None:
        assert OperationMode("SIMULATION") == OperationMode.SIMULATION

    def test_twin_mode(self) -> None:
        assert OperationMode("TWIN") == OperationMode.TWIN

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            OperationMode("INVALID")


# ===================================================================
# MicrogridConfig
# ===================================================================


class TestMicrogridConfig:
    """Tests for the root MicrogridConfig model."""

    def test_valid_config(self) -> None:
        config = MicrogridConfig(**VALID_CONFIG)
        assert config.mode == OperationMode.SIMULATION
        assert len(config.bess_units) == 1

    def test_twin_mode(self) -> None:
        data = {**VALID_CONFIG, "mode": "TWIN"}
        config = MicrogridConfig(**data)
        assert config.mode == OperationMode.TWIN

    def test_invalid_mode_raises(self) -> None:
        from pydantic import ValidationError

        data = {**VALID_CONFIG, "mode": "INVALID"}
        with pytest.raises(ValidationError):
            MicrogridConfig(**data)

    def test_empty_bess_list(self) -> None:
        data = {"mode": "SIMULATION", "bess_units": []}
        config = MicrogridConfig(**data)
        assert len(config.bess_units) == 0

    def test_genset_pv_default_empty(self) -> None:
        config = MicrogridConfig(**VALID_CONFIG)
        assert config.genset_units == []
        assert config.pv_units == []

    def test_with_all_asset_types(self) -> None:
        data = {
            "mode": "SIMULATION",
            "bess_units": [VALID_BESS],
            "genset_units": [{"genset_id": "GEN_01", "num_units": 2}],
            "pv_units": [{"pv_id": "PV_01", "num_units": 5}],
        }
        config = MicrogridConfig(**data)
        assert len(config.bess_units) == 1
        assert len(config.genset_units) == 1
        assert len(config.pv_units) == 1


# ===================================================================
# load_config()
# ===================================================================


class TestLoadConfig:
    """Tests for the JSON config file loader."""

    def test_load_valid_json(self, tmp_path: Path) -> None:
        config_file = tmp_path / "simulation.json"
        config_file.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")

        config = load_config(config_file)
        assert config.mode == OperationMode.SIMULATION
        assert config.bess_units[0].bess_id == "BESS_01"
        assert config.bess_units[0].total_cells == 96

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(MissingConfigurationError, match="not found"):
            load_config(missing)

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json content", encoding="utf-8")
        with pytest.raises(MissingConfigurationError, match="Failed to read"):
            load_config(bad_file)

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        from pydantic import ValidationError

        bad_schema = tmp_path / "bad_schema.json"
        bad_schema.write_text(
            json.dumps({"mode": "SIMULATION", "bess_units": [{"invalid": "data"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValidationError):
            load_config(bad_schema)

    def test_round_trip_preserves_nested_data(self, tmp_path: Path) -> None:
        """End-to-end: JSON file -> MicrogridConfig -> BESSConfig -> CellConfig."""
        config_file = tmp_path / "full.json"
        config_file.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")

        config = load_config(config_file)
        bess = config.bess_units[0]
        assert bess.cell_spec.nominal_voltage == 3.68
        assert bess.cell_spec.nominal_capacity == 94.0
        assert bess.manufacturer_metadata["manufacturer"] == "Samsung SDI"


# ===================================================================
# AssetRegistry
# ===================================================================


class TestAssetRegistry:
    """Tests for asset discovery and catalog lookup."""

    def test_discover_bess(self) -> None:
        config = MicrogridConfig(**VALID_CONFIG)
        registry = AssetRegistry(config)
        assert registry.bess_ids == ["BESS_01"]
        assert registry.total_assets == 1

    def test_get_bess(self) -> None:
        config = MicrogridConfig(**VALID_CONFIG)
        registry = AssetRegistry(config)
        bess = registry.get_bess("BESS_01")
        assert bess.num_strings == 2
        assert bess.total_cells == 96

    def test_get_missing_bess_raises(self) -> None:
        config = MicrogridConfig(**VALID_CONFIG)
        registry = AssetRegistry(config)
        with pytest.raises(KeyError):
            registry.get_bess("NONEXISTENT")

    def test_duplicate_bess_id_raises(self) -> None:
        data = {
            "mode": "SIMULATION",
            "bess_units": [VALID_BESS, VALID_BESS],
        }
        config = MicrogridConfig(**data)
        with pytest.raises(ValueError, match="Duplicate BESS ID"):
            AssetRegistry(config)

    def test_empty_registry(self) -> None:
        data = {"mode": "SIMULATION", "bess_units": []}
        config = MicrogridConfig(**data)
        registry = AssetRegistry(config)
        assert registry.total_assets == 0
        assert registry.bess_ids == []
        assert registry.genset_ids == []
        assert registry.pv_ids == []

    def test_multiple_bess_units(self) -> None:
        bess2 = {**VALID_BESS, "bess_id": "BESS_02"}
        data = {
            "mode": "SIMULATION",
            "bess_units": [VALID_BESS, bess2],
        }
        config = MicrogridConfig(**data)
        registry = AssetRegistry(config)
        assert registry.total_assets == 2
        assert set(registry.bess_ids) == {"BESS_01", "BESS_02"}

    def test_mixed_asset_types(self) -> None:
        data = {
            "mode": "SIMULATION",
            "bess_units": [VALID_BESS],
            "genset_units": [{"genset_id": "GEN_01", "num_units": 2}],
            "pv_units": [{"pv_id": "PV_01", "num_units": 10}],
        }
        config = MicrogridConfig(**data)
        registry = AssetRegistry(config)
        assert registry.total_assets == 3
        assert registry.get_genset("GEN_01").total_units == 2
        assert registry.get_pv("PV_01").total_units == 10

    def test_registry_exposes_mode(self) -> None:
        config = MicrogridConfig(**VALID_CONFIG)
        registry = AssetRegistry(config)
        assert registry.mode == OperationMode.SIMULATION
