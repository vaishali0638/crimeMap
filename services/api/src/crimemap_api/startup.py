"""Start-up safety checks."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def assert_no_demo_data(engine: AsyncEngine, environment: str) -> None:
    """In PRODUCTION, refuse to start while any synthetic row or demo identity exists
    (docs/demo-data.md, policy 5). ``core.demo_data_present()`` sees past row-level security."""
    if environment != "PRODUCTION":
        return
    async with engine.connect() as conn:
        present = (await conn.execute(text("SELECT core.demo_data_present()"))).scalar_one()
    if present:
        raise RuntimeError(
            "refusing to start in PRODUCTION: demo data or demo identities are present"
        )
