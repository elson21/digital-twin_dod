# Microgrid Digital Twin Walkthrough

Welcome to the **High-Performance Python Microgrid Digital Twin** architecture project! This platform executes dynamic constraints bridging lightning-fast `Numpy` memory tracking structures natively alongside a heavy PyBAMM non-blocking processor execution pool.

## Table of Contents
1. [Core Features](#core-features)
2. [CLI Execution Parameters](#cli-execution-parameters)
3. [Configuration Mapping](#configuration-mapping)
4. [Monitoring Data Output](#monitoring-data-output)

## Core Features
This system applies DOD (Data-Oriented Design) paradigms over a custom-written engine framework consisting largely of three primary worker pools:

### ⚡ 1. The Physics "Hot Path" Engine
Located in `src/engine/physics.py`, this is our **high-frequency loop (10-100Hz)**. It actively binds parallel strings using fully vectorized zero-copy matrices over continuous SHM. Current constraint checks rely solely on `np.clip` bounds with completely branchless execution formats guaranteeing strict cache-locality hits without `GC` pauses natively across 1,000+ local BESS cell structures simultaneously.

### 🔋 2. PyBAMM "Shadow Twin" (Heavy Path)
Found in `src/engine/shadow_twin.py`. Differential equation solving with native `Casadi` requires excessive cycle bandwidth. The supervisor forks a decoupled Shadow Twin parameterizing `SPMe (Single Particle Model with electrolyte bounds)`. Without utilizing native block-thread queues, the logic natively checks strict atomic `epoch` flags dynamically mapped over memory schemas bypassing local context switching logic!

### 📊 3. Batched `db_writer` Persistence (CSV Matrix)
Residing within `src/services/db_writer.py`. The Database writer uses `np.reshape` hierarchy topology matrices executing strictly fast memory `memcpy` loops mapping exactly 10 loops interval before dynamically stringifying logic avoiding overhead bounds blockages to parallel files completely decoupled!

---

## CLI Execution Parameters

> [!TIP]
> The engine defaults to mapping its base state on the file parameters defined natively underneath `tests/fixtures` if unmapped.

Execution is built robustly on top of `argparse`. Simply invoke `main.py` through your `python` or `uv` environments directly:

```bash
uv run python main.py [CONFIG]
```

### Display Help Menu
Use standard CLI flags to parse definitions programmatically:
```bash
uv run python main.py --help
```

*Output:*
```text
usage: main.py [-h] [config]

Microgrid Digital Twin Orchestrator - High-Performance DOD implementation mapping PyBAMM heavily.

positional arguments:
  config      Path to the system JSON configuration file. (default: src\config\user\simulation.json)

options:
  -h, --help  show this help message and exit
```

### Passing Configurations Override
Simply trail execution with your custom JSON mapping definitions safely:
```bash
uv run python main.py my_custom_config.json
```

---

## Configuration Mapping

You define your entire asset network simply using standard hierarchical formats!
Configurations reside inherently inside `src/config/templates` and define string layouts dynamically mapping parameters natively:

```json
{
  "unit_id": "BESS_01",
  "total_capacity_kwh": 500.0,
  "nominal_voltage_v": 800.0,
  "load_current_a": 50.0,
  "topology": {
    "num_strings": 2,
    "packs_per_string": 4,
    "cells_per_pack": 12
  },
  "initial_state": {
    "strategy": "scalar",
    "voltage_v": 3.8,
    "temperature_c": 25.0
  }
}
```

> [!CAUTION]
> Avoid altering parallel topology boundaries on active memory tracking instances mapped via SHM pools unless the parent process guarantees garbage destruction logic execution successfully (`sup.shutdown()`)! 

---

## Monitoring Data Output

Outputs stream immediately alongside the runtime. Navigate towards your mapping directories natively constructed. Assuming standard parameterizations, check the directories inside `output/`:

- **Summary Dashboard Files (`BESS_01_summary.csv`)**: Contains System level aggregate bounds tracking exactly overall topology limits (`system_voltage_v` parallel strings alongside individual macro constraint variances like `min_temp` and global layout metrics).
- **Detail Metrics (`BESS_01_detail.csv`)**: A micro-second resolution matrix strictly mapping index logic arrays dynamically constructed against parallel node execution instances.
