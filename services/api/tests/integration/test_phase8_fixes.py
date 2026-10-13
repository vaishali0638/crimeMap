"""Defects found during Phase 8 (end-to-end and security testing), each with its regression test."""

import psycopg
import pytest

from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration


def source_issue(database: Database) -> str:
    """An open stale-feed issue: about a source system, so it has no region or station."""
    with psycopg.connect(database.superuser, autocommit=True) as conn:
        row = conn.execute(
            "INSERT INTO core.data_quality_issue (subject_type, subject_id, issue_type, severity, "
            "detected_by, rule_code, detail) SELECT 'SOURCE_SYSTEM', id, 'STALE_SOURCE', 'HIGH', "
            "'RULE', 'DQ-SRC-001', '{}'::jsonb FROM core.source_system WHERE code = 'DEMO_SYNTHETIC_LEGACY' "
            "ON CONFLICT (subject_type, subject_id, rule_code) DO UPDATE SET state = 'OPEN' "
            "RETURNING id::text, region_id, station_id"
        ).fetchone()
    assert row is not None
    assert row[1] is None
    assert row[2] is None
    return str(row[0])


async def test_a_region_steward_can_open_the_history_of_a_source_level_issue_they_can_list(
    api, make_user, ingested: Ingested, database: Database
) -> None:  # type: ignore[no-untyped-def]
    # Found by the Phase 8 browser run: the queue lists source-level issues (no territory) to every
    # dq.manage holder, but their History link answered 404 to a region-scoped steward.
    issue = source_issue(database)
    sub, _ = make_user(Role.DATA_STEWARD, regions=("PDY",))
    headers = bearer(mint(sub))
    listed = await api.get(
        "/api/v1/dq/issues", params={"subject_type": "SOURCE_SYSTEM", "limit": 100}, headers=headers
    )
    assert listed.status_code == 200, listed.text
    assert issue in {i["id"] for i in listed.json()["data"]}
    history = await api.get(
        "/api/v1/workflow-history",
        params={"object_type": "DQ_ISSUE", "object_id": issue},
        headers=headers,
    )
    assert history.status_code == 200, history.text


async def test_source_level_issue_history_still_needs_dq_manage(
    api, make_user, ingested: Ingested, database: Database
) -> None:  # type: ignore[no-untyped-def]
    issue = source_issue(database)
    sub, _ = make_user(Role.CRIME_ANALYST, regions=("PDY",))
    history = await api.get(
        "/api/v1/workflow-history",
        params={"object_type": "DQ_ISSUE", "object_id": issue},
        headers=bearer(mint(sub)),
    )
    assert history.status_code == 404
