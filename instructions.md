# Technical Instructions & Mandatory Rules
1. **Architectural Integrity (Mechanical Sympathy)**
    - **Data-Oriented Design:** Logic MUST be separated from state. Keep state in contiguous NumPy arrays within Shared Memory (SHM). Avoid "Object Forests" (e.g., thousands of Cell objects).
    - **Vectorization over Loops:** Explicit loops over asset units (cells, packs) are FORBIDDEN in the physics engine. All math must be vectorized using NumPy to ensure cache locality.
    - **Zero-Copy IPC:** Inter-process communication must happen via multiprocessing.shared_memory. Do not "pickle" or pass large data objects between processes.
2. **Configuration & Validation (Control Plane)**
    - **Pydantic Enforcement:** Use Pydantic models strictly for the "Control Plane" (validating `simulation.json` and manufacturer metadata).
    - **No Hardcoding:** All hardware specs and network parameters (IPs, Modbus registers) must be validated via Pydantic and never hardcoded in logic.
    - **Mode Selection:** The application must support a clear switch between `SIMULATION` (synthetic data generation) and `TWIN` (hardware telemetry ingestion via Modbus/CAN).
3. **Application Behavior**
    - **Configuration Dependency:** The system must remain idle and NOT provide "default" simulation data. If a valid configuration file is missing, raise a `MissingConfigurationError`.
    - **Input Strictness:** Do not generate sample data files unless explicitly requested. The system must fail fast if user-provided input is invalid.
4. **Code Quality & Standards**
    - **Modular Layout:** The root directory is for configuration only. All execution logic must reside in `/src` sub-modules (`/core`, `/engine`, `/drivers`, `/services`).
    - **Typing & Documentation:** Strict Python type hints are mandatory. Every class and function requires a Google-style docstring.
    - **Packaging:** Use `astral uv` for all dependency and environment management.
    - **Best Practices:** Adhere to PEP 8, DRY, KISS, and the Single Responsibility Principle. Each process (Telemetry, Physics, DB Writer) must have one job.
5. **Logging & Persistence**
    - **Error Handling:** Use structured `try-except` blocks with meaningful logging.
    - **Iteration Tracking:** Every significant architectural change or feature addition must be logged in `iterations.md`.
    - **Non-Blocking I/O:** Logging and database persistence must not block the high-frequency physics loop.
    - **Implementation Plan:** Always show the implementation plan before making any changes.
6. **Version Control**
    - **Implementation Plan:** Always show the implementation plan before making any changes.
    - **Commit:** Commit after approval of code.
    - **Commit Messages:** Commit messages must be in the format "feat: <description>" or "fix: <description>".