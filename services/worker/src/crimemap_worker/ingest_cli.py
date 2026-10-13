"""Ingest a directory of signed batch files, then refresh source health.

    python -m crimemap_worker.ingest_cli <directory> [<directory> ...]

Runs as the crimemap_worker database role. Environment:
    CRIMEMAP_WORKER_DATABASE_URL   asyncpg URL for crimemap_worker
    CRIMEMAP_WORKER_ENVIRONMENT    DEMO | SANDBOX | PRODUCTION, required (demo feeds refused in PRODUCTION)
    CRIMEMAP_DEMO_SIGNING_KEY      shared batch signing key (at least 32 characters)
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from crimemap_api.cli import resolve_environment
from crimemap_worker.ingest import IngestResult, ingest_directory


async def _run(
    directories: list[Path], url: str, key: bytes, environment: str
) -> list[IngestResult]:
    engine = create_async_engine(url, pool_size=2)
    try:
        results: list[IngestResult] = []
        for directory in directories:
            results += await ingest_directory(engine, directory, key, environment=environment)
        return results
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crimemap_worker.ingest_cli")
    parser.add_argument("directories", nargs="+", type=Path)
    args = parser.parse_args(argv)
    url = os.environ.get("CRIMEMAP_WORKER_DATABASE_URL", "")
    key = os.environ.get("CRIMEMAP_DEMO_SIGNING_KEY", "").encode()
    if not url or len(key) < 32:
        print(
            "CRIMEMAP_WORKER_DATABASE_URL and CRIMEMAP_DEMO_SIGNING_KEY (>= 32 chars) must be set",
            file=sys.stderr,
        )
        return 2
    environment = resolve_environment(os.environ.get("CRIMEMAP_WORKER_ENVIRONMENT"))
    started = time.monotonic()
    results = asyncio.run(_run(args.directories, url, key, environment))
    totals = {
        k: sum(getattr(r, k) for r in results)
        for k in (
            "record_count",
            "accepted",
            "duplicates",
            "rejected",
            "superseded",
            "issues_opened",
        )
    }
    statuses = {s: sum(r.status == s for r in results) for s in {r.status for r in results}}
    print(f"batches: {statuses}")
    print(f"records: {totals}")
    print(f"elapsed: {time.monotonic() - started:.1f}s")
    return 1 if any(s.startswith("REJECTED") or s == "FAILED" for s in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())
