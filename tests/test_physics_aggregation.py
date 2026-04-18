import numpy as np

from engine.physics import aggregate_voltages


def test_aggregate_voltages():
    # Setup test topology: 2 strings, 3 packs per string, 4 cells per pack
    # total_cells = 2 * 3 * 4 = 24
    num_strings = 2
    packs_per_string = 3
    cells_per_pack = 4
    total_cells = num_strings * packs_per_string * cells_per_pack

    # Initialize all cells to exactly 3.0V
    voltages = np.full(total_cells, 3.0, dtype=np.float64)

    pack_v, string_v, sys_v = aggregate_voltages(
        voltages, num_strings, packs_per_string, cells_per_pack
    )

    # 4 cells per pack * 3.0V = 12.0V per pack
    assert pack_v.shape == (2, 3)
    np.testing.assert_array_equal(pack_v, np.full((2, 3), 12.0))

    # 3 packs per string * 12.0V = 36.0V per string
    assert string_v.shape == (2,)
    np.testing.assert_array_equal(string_v, np.full(2, 36.0))

    # parallel strings average -> 36.0V
    assert sys_v == 36.0
