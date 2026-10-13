"""Alembic environment: runs migrations as the crimemap_migrator role (schema owner).

Migrations use the synchronous psycopg driver so that hand-written SQL blocks (functions, triggers,
grants) can be sent verbatim — see ``migrations/sql.py``. The API itself uses asyncpg.
"""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def _url() -> str:
    raw = os.environ.get("CRIMEMAP_MIGRATOR_DATABASE_URL")
    if not raw:
        raise RuntimeError("CRIMEMAP_MIGRATOR_DATABASE_URL is not set")
    return make_url(raw).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) mode is not supported; migrations run online only")

engine = create_engine(_url())
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
