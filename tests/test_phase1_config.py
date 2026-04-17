# tests/test_phase1_config.py
"""Phase 1 Tests: Configuration loading, Pydantic validation, and Asset Registry.

All test data is loaded from JSON fixture files in ``tests/fixtures/``.

Verifies:
  - load_config() JSON round-trip from a real file on disk.
  - Missing-file and malformed-JSON error paths.
  - Pydantic schema rejection for invalid configs.
  - Computed properties (total_cells, total_units, etc.).
  - AssetRegistry discovery, lookup, and duplicate-ID rejection.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings import (
    MicrogridConfig,
    MissingConfigurationError,
    OperationMode,
    load_config,
)
from core.registry import AssetRegistry

# ---------------------------------------------------------------------------
# Fixture paths (resolved relative to this test file)
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_CONFIG_PATH = FIXTURES_DIR / "valid_config.json"
INVALID_SCHEMA_PATH = FIXTURES_DIR / "invalid_schema.json"
MALFORMED_JSON_PATH = FIXTURES_DIR / "malformed.json"


# ===================================================================
# Config loading from JSON files
# ===================================================================


class TestLoadConfig:
    """Tests for loading and validating config from real JSON files."""

    def test_load_valid_config(self) -> None:
        """Load valid_config.json and verify the root model."""
        config = load_config(VALID_CONFIG_PATH)
        assert config.mode == OperationMode.SIMULATION
        assert len(config.bess_units) == 1
        assert len(config.genset_units) == 1
        assert len(config.pv_units) == 1

    def test_bess_identity(self) -> None:
        """BESS unit ID and topology are correctly parsed."""
        config = load_config(VALID_CONFIG_PATH)
        bess = config.bess_units[0]
        assert bess.bess_id == "BESS_01"
        assert bess.num_strings == 2
        assert bess.packs_per_string == 4
        assert bess.cells_per_pack == 12

    def test_bess_computed_properties(self) -> None:
        """Computed topology properties derive correctly from the JSON."""
        config = load_config(VALID_CONFIG_PATH)
        bess = config.bess_units[0]
        assert bess.total_strings == 2
        assert bess.total_packs == 8       # 2 * 4
        assert bess.total_cells == 96      # 2 * 4 * 12
        assert bess.total_units == 96      # total_units == total_cells

    def test_bess_load_current(self) -> None:
        """Load current is parsed from the JSON."""
        config = load_config(VALID_CONFIG_PATH)
        bess = config.bess_units[0]
        assert bess.load_current_a == 50.0

    def test_cell_spec(self) -> None:
        """Nested CellConfig is correctly deserialized."""
        config = load_config(VALID_CONFIG_PATH)
        cell = config.bess_units[0].cell_spec
        assert cell.name == "Samsung SDI 94Ah"
        assert cell.nominal_voltage == 3.68
        assert cell.nominal_capacity == 94.0
        assert cell.nominal_current == 94.0
        assert cell.temperature_min == -20.0
        assert cell.temperature_max == 55.0

    def test_manufacturer_metadata(self) -> None:
        """Freeform manufacturer metadata is preserved."""
        config = load_config(VALID_CONFIG_PATH)
        meta = config.bess_units[0].manufacturer_metadata
        assert meta["manufacturer"] == "Samsung SDI"
        assert meta["model"] == "ESS Module"

    def test_genset_stub(self) -> None:
        """Genset stub config is parsed correctly."""
        config = load_config(VALID_CONFIG_PATH)
        genset = config.genset_units[0]
        assert genset.genset_id == "GEN_01"
        assert genset.total_units == 2

    def test_pv_stub(self) -> None:
        """PV stub config is parsed correctly."""
        config = load_config(VALID_CONFIG_PATH)
        pv = config.pv_units[0]
        assert pv.pv_id == "PV_01"
        assert pv.total_units == 10

    def test_missing_file_raises(self) -> None:
        """A nonexistent file path raises MissingConfigurationError."""
        missing = FIXTURES_DIR / "nonexistent.json"
        with pytest.raises(MissingConfigurationError, match="not found"):
            load_config(missing)

    def test_malformed_json_raises(self) -> None:
        """An unparseable JSON file raises MissingConfigurationError."""
        with pytest.raises(MissingConfigurationError, match="Failed to read"):
            load_config(MALFORMED_JSON_PATH)

    def test_invalid_schema_raises(self) -> None:
        """Valid JSON but invalid Pydantic schema raises ValidationError."""
        with pytest.raises(ValidationError):
            load_config(INVALID_SCHEMA_PATH)


# ===================================================================
# AssetRegistry (driven by the loaded config)
# ===================================================================


class TestAssetRegistry:
    """Tests for asset discovery and catalog lookup using the fixture config."""

    @pytest.fixture()
    def registry(self) -> AssetRegistry:
        """Load the valid config and build a registry from it."""
        config = load_config(VALID_CONFIG_PATH)
        return AssetRegistry(config)

    def test_total_assets(self, registry: AssetRegistry) -> None:
        """Registry discovers all three asset types from the fixture."""
        assert registry.total_assets == 3

    def test_bess_ids(self, registry: AssetRegistry) -> None:
        assert registry.bess_ids == ["BESS_01"]

    def test_genset_ids(self, registry: AssetRegistry) -> None:
        assert registry.genset_ids == ["GEN_01"]

    def test_pv_ids(self, registry: AssetRegistry) -> None:
        assert registry.pv_ids == ["PV_01"]

    def test_get_bess(self, registry: AssetRegistry) -> None:
        bess = registry.get_bess("BESS_01")
        assert bess.num_strings == 2
        assert bess.total_cells == 96

    def test_get_genset(self, registry: AssetRegistry) -> None:
        genset = registry.get_genset("GEN_01")
        assert genset.total_units == 2

    def test_get_pv(self, registry: AssetRegistry) -> None:
        pv = registry.get_pv("PV_01")
        assert pv.total_units == 10

    def test_get_missing_asset_raises(self, registry: AssetRegistry) -> None:
        with pytest.raises(KeyError):
            registry.get_bess("NONEXISTENT")

    def test_registry_exposes_mode(self, registry: AssetRegistry) -> None:
        assert registry.mode == OperationMode.SIMULATION

    def test_duplicate_bess_id_raises(self) -> None:
        """Manually construct a config with duplicate IDs to test rejection."""
        config = load_config(VALID_CONFIG_PATH)
        # Inject a duplicate by appending the same BESS again
        config_data = config.model_dump()
        config_data["bess_units"].append(config_data["bess_units"][0])
        dup_config = MicrogridConfig.model_validate(config_data)
        with pytest.raises(ValueError, match="Duplicate BESS ID"):
            AssetRegistry(dup_config)
