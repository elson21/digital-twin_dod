# src/engine/shadow_twin.py
"""Deep-path heavy simulations (PyBAMM).

NOTE: This module is a stub.  The Shadow Twin runs infrequent,
high-fidelity electrochemical simulations (via PyBAMM) for
State-of-Health (SoH) assessment.  It reads cell state from SHM,
runs a full DAE solve, and writes updated SoH back.

Unlike the fast-path physics engine (NumPy, 10-100 Hz), the shadow
twin runs at much lower frequency (minutes to hours) and is
computationally expensive.

Planned interface:
    ``shadow_twin_loop(config_path, bess_id, interval, shutdown_event)``
"""