#!/usr/bin/env python3
"""Generate signed synthetic demo batches (DEMO / SYNTHETIC DATA only).

    CRIMEMAP_DEMO_SIGNING_KEY=... scripts/generate_demo_data.py OUT_DIR [--records N] [--seed S]

Thin wrapper around `python -m crimemap_api.cli generate-demo` (services/api/src/crimemap_api/cli.py).
Ingest the result with `python -m crimemap_worker.ingest_cli OUT_DIR/demo_synthetic ...`, or run
both in Docker: `scripts/compose.sh --profile app --profile demo-data run --rm demo-data`.
"""

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "services" / "api"
PYTHON = API_DIR / ".venv" / "bin" / "python"

if __name__ == "__main__":
    # Fixed interpreter and module; user arguments are passed as argv items, never to a shell.
    os.execv(str(PYTHON), [str(PYTHON), "-m", "crimemap_api.cli", "generate-demo", *sys.argv[1:]])  # noqa: S606
