"""Send a hand-written SQL block verbatim on Alembic's connection.

``op.execute`` routes text through SQLAlchemy, which treats ``:name`` as bind parameters and rewrites
``%``. Function bodies and ``format()`` templates need the SQL exactly as written, so migrations use
the raw psycopg cursor of the same connection (and therefore the same transaction).
"""

from alembic import op


def run_sql(block: str) -> None:
    dbapi_connection = op.get_bind().connection.dbapi_connection
    if dbapi_connection is None:
        raise RuntimeError("no DBAPI connection available")
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(block)
    finally:
        cursor.close()
