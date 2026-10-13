#!/usr/bin/env python3
"""Seed the synthetic demo boundaries and the fictional offence-code mapping (refused in PRODUCTION).

    CRIMEMAP_MIGRATOR_DATABASE_URL=... scripts/seed_boundaries.py

Thin wrapper around `python -m crimemap_api.cli seed-boundaries` and `seed-offence-map`; the Compose
`migrate` job runs the same commands automatically outside PRODUCTION.
"""

import subprocess
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "services" / "api"
PYTHON = API_DIR / ".venv" / "bin" / "python"

if __name__ == "__main__":
    for command in ("seed-boundaries", "seed-offence-map"):
        result = subprocess.run([str(PYTHON), "-m", "crimemap_api.cli", command], check=False)  # noqa: S603 - fixed argv
        if result.returncode:
            sys.exit(result.returncode)
