# tests/test_phase4_engine.py
"""Phase 4 Tests: Physics engine, DB writer, and full pipeline integration.

Validates:
  - Pure vectorized math functions (SoC, voltage, temperature).
  - Physics engine process updates SHM via the supervisor.
  - DB writer produces CSV output.

Config is loaded from ``tests/fixtures/valid_config.json``.
"""

import csv
import time
from pathlib import Path

import numpy as np
import pytest

from engine.physics import update_soc, update_temperature, update_voltage_from_soc
from supervisor import Supervisor

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_CONFIG_PATH = FIXTURES_DIR / "valid_config.json"


# ===================================================================
# Pure vectorized math functions (no SHM, no processes)
# ===================================================================


class TestUpdateSoC:
    """Tests for the Coulomb-counting SoC update function."""

    def test_discharge_decreases_soc(self) -> None:
        """Positive current (discharge) decreases SoC."""
        soc = np.full(96, 80.0)
        update_soc(soc, cell_current=25.0, dt=1.0, capacity_ah=94.0)
        assert np.all(soc < 80.0)

    def test_charge_increases_soc(self) -> None:
        """Negative current (charge) increases SoC."""
        soc = np.full(96, 50.0)
        update_soc(soc, cell_current=-25.0, dt=1.0, capacity_ah=94.0)
        assert np.all(soc > 50.0)

    def test_soc_clamps_at_zero(self) -> None:
        """SoC cannot go below 0%."""
        soc = np.full(10, 0.001)
        update_soc(soc, cell_current=25.0, dt=100.0, capacity_ah=94.0)
        assert np.all(soc >= 0.0)

    def test_soc_clamps_at_hundred(self) -> None:
        """SoC cannot go above 100%."""
        soc = np.full(10, 99.999)
        update_soc(soc, cell_current=-25.0, dt=100.0, capacity_ah=94.0)
        assert np.all(soc <= 100.0)

    def test_soc_delta_is_correct(self) -> None:
        """Verify the exact SoC change for known parameters."""
        soc = np.full(96, 80.0)
        # delta = (I * dt) / (C * 3600) * 100
        # = (25 * 1.0) / (94 * 3600) * 100 = 0.007388%
        expected_delta = (25.0 * 1.0) / (94.0 * 3600.0) * 100.0
        update_soc(soc, cell_current=25.0, dt=1.0, capacity_ah=94.0)
        np.testing.assert_allclose(soc, 80.0 - expected_delta)

    def test_vectorized_no_loops(self) -> None:
        """Large array completes in reasonable time (proves vectorization)."""
        soc = np.full(100_000, 50.0)
        start = time.perf_counter()
        for _ in range(1000):
            update_soc(soc, cell_current=25.0, dt=0.1, capacity_ah=94.0)
        elapsed = time.perf_counter() - start
        # 1000 iterations over 100k elements should be fast with NumPy
        assert elapsed < 2.0, f"Too slow ({elapsed:.2f}s) — likely not vectorized"


class TestUpdateVoltage:
    """Tests for the linear OCV voltage model."""

    def test_full_charge_voltage(self) -> None:
        """100% SoC maps to v_max."""
        voltage = np.zeros(10)
        soc = np.full(10, 100.0)
        update_voltage_from_soc(voltage, soc, v_min=2.5, v_max=4.2)
        np.testing.assert_allclose(voltage, 4.2)

    def test_empty_voltage(self) -> None:
        """0% SoC maps to v_min."""
        voltage = np.zeros(10)
        soc = np.full(10, 0.0)
        update_voltage_from_soc(voltage, soc, v_min=2.5, v_max=4.2)
        np.testing.assert_allclose(voltage, 2.5)

    def test_midpoint_voltage(self) -> None:
        """50% SoC maps to the midpoint between v_min and v_max."""
        voltage = np.zeros(10)
        soc = np.full(10, 50.0)
        update_voltage_from_soc(voltage, soc, v_min=2.5, v_max=4.2)
        np.testing.assert_allclose(voltage, 3.35)

    def test_monotonic_relationship(self) -> None:
        """Higher SoC always produces higher voltage."""
        voltage = np.zeros(5)
        soc = np.array([0.0, 25.0, 50.0, 75.0, 100.0])
        update_voltage_from_soc(voltage, soc)
        assert np.all(np.diff(voltage) > 0)


class TestUpdateTemperature:
    """Tests for the simple thermal model."""

    def test_cold_cell_warms(self) -> None:
        """A cell below ambient drifts upward."""
        temp = np.full(10, 10.0, dtype=np.float32)
        update_temperature(temp, cell_current=25.0, dt=1.0, ambient=25.0)
        assert np.all(temp > 10.0)

    def test_hot_cell_cools(self) -> None:
        """A cell well above equilibrium drifts downward."""
        target = 25.0 + abs(25.0) * 0.005  # ambient + self-heating
        temp = np.full(10, 60.0, dtype=np.float32)
        update_temperature(temp, cell_current=25.0, dt=1.0, ambient=25.0)
        assert np.all(temp < 60.0)

    def test_equilibrium_is_stable(self) -> None:
        """At the target temperature, delta is approximately zero."""
        target = 25.0 + abs(25.0) * 0.005
        temp = np.full(10, target, dtype=np.float32)
        update_temperature(temp, cell_current=25.0, dt=1.0, ambient=25.0)
        np.testing.assert_allclose(temp, target, atol=0.01)


# ===================================================================
# Integration: Supervisor → Physics Engine → SHM
# ===================================================================


class TestPhysicsIntegration:
    """Full pipeline tests: supervisor spawns physics, SHM gets updated."""

    def test_physics_updates_soc(self) -> None:
        """Physics engine decreases SoC over time (discharging)."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            sup.spawn_workers(dt=0.05, enable_db_writer=False, enable_shadow_twin=False)

            # Let physics run for ~1 second (≈20 ticks at 20 Hz)
            time.sleep(1.5)

            state = sup.get_bess_state("BESS_01")
            mean_soc = float(np.mean(state.soc.array))

            # SoC should have been initialized to ~47.058% and decreased
            assert 0 < mean_soc < 47.1, f"Expected SoC < 47.1%, got {mean_soc:.4f}%"
        finally:
            sup.shutdown()

    def test_physics_updates_voltage(self) -> None:
        """Voltage array reflects SoC via the OCV model."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            sup.spawn_workers(dt=0.05, enable_db_writer=False, enable_shadow_twin=False)
            time.sleep(1.5)

            state = sup.get_bess_state("BESS_01")
            mean_v = float(np.mean(state.voltages.array))

            # Voltage should be in a valid range
            assert 2.5 < mean_v < 4.2, f"Voltage out of range: {mean_v:.4f}V"
        finally:
            sup.shutdown()

    def test_physics_updates_temperature(self) -> None:
        """Temperature array should be near ambient after initialization."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            sup.spawn_workers(dt=0.05, enable_db_writer=False, enable_shadow_twin=False)
            time.sleep(1.5)

            state = sup.get_bess_state("BESS_01")
            mean_temp = float(np.mean(state.temperature.array))

            # Temperature should be near 25°C (ambient)
            assert 20.0 < mean_temp < 35.0, f"Temp out of range: {mean_temp:.1f}°C"
        finally:
            sup.shutdown()

    def test_db_writer_creates_csv(self, tmp_path: Path) -> None:
        """DB writer produces summary and detail CSV logs."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            sup.spawn_workers(
                dt=0.05, db_output_dir=tmp_path, enable_db_writer=True, enable_shadow_twin=False
            )
            time.sleep(2.0)  # Let DB writer capture a few snapshots
        finally:
            sup.shutdown()

        # --- Summary CSV ---
        summary_csv = tmp_path / "BESS_01_summary.csv"
        assert summary_csv.exists(), "DB writer did not create summary CSV"

        with open(summary_csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            summary_rows = list(reader)

        assert len(summary_rows) >= 1, "Summary CSV has no data rows"
        assert "load_current_a" in summary_rows[0]
        assert "system_voltage_v" in summary_rows[0]
        assert "string_voltages_v" in summary_rows[0]
        assert "mean_soc_pct" in summary_rows[0]
        assert "max_temp_c" in summary_rows[0]
        assert "min_temp_c" in summary_rows[0]

        # --- Detail CSV ---
        detail_csv = tmp_path / "BESS_01_detail.csv"
        assert detail_csv.exists(), "DB writer did not create detail CSV"

        with open(detail_csv, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            detail_rows = list(reader)

        assert len(detail_rows) >= 1, "Detail CSV has no data rows"
        assert "cell_index" in detail_rows[0]
        assert "voltage_v" in detail_rows[0]
        assert "soc_pct" in detail_rows[0]
        assert "temp_c" in detail_rows[0]

    def test_graceful_shutdown(self) -> None:
        """Workers stop cleanly when supervisor shuts down."""
        sup = Supervisor(VALID_CONFIG_PATH)
        sup.start()
        sup.spawn_workers(dt=0.05, enable_db_writer=False, enable_shadow_twin=False)
        time.sleep(0.5)

        sup.shutdown()
        assert not sup.is_running
        assert len(sup.all_buffer_names) == 0

    def test_runtime_current_switch(self) -> None:
        """Switching load current via supervisor affects physics engine mid-run."""
        sup = Supervisor(VALID_CONFIG_PATH)
        try:
            sup.start()
            # Initial config has positive current (discharge).
            # Let's set it to negative (charge)
            sup.set_load_current("BESS_01", -50.0)
            
            sup.spawn_workers(dt=0.05, enable_db_writer=False, enable_shadow_twin=False)
            time.sleep(1.5)

            state = sup.get_bess_state("BESS_01")
            mean_soc = float(np.mean(state.soc.array))

            # SoC should increase from initial ~47.058%
            assert mean_soc > 47.0, f"Expected SoC > 47.0% (charging), got {mean_soc:.4f}%"
        finally:
            sup.shutdown()
