#!/usr/bin/env python
"""Entry point for the Microgrid Digital Twin.

Usage:
    uv run python main.py <config_path>

Example:
    uv run python main.py simulation.json
"""

import logging
import sys
from pathlib import Path

# Ensure src/ is importable for the top-level supervisor module
sys.path.insert(0, str(Path(__file__).parent / "src"))

from supervisor import Supervisor  # noqa: E402


def main() -> None:
    """Parse arguments and run the supervisor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        print(
            "Usage: uv run python main.py <config_path>",
            file=sys.stderr,
        )
        sys.exit(1)

    config_path = Path(sys.argv[1])
    supervisor = Supervisor(config_path)
    supervisor.run()


if __name__ == "__main__":
    main()
