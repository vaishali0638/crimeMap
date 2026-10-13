"""Phase 7: responsible AI assistance (ADR-037).

Acceptance (spec Phase 7):
- AI output always includes evidence and limitations.
- AI cannot create an action, export data, or modify a case.
- Analyst validation/dismissal is required and audited.
- Model registry and kill switch exist.
- Forecasting remains disabled unless AI rollout gates are passed.
"""

import asyncio
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import psycopg
import pytest
from crimemap_worker.ai import run_detectors
from sqlalchemy.ext.asyncio import create_async_engine

from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

MakeUser = Callable[..., tuple[str, str]]
CRAFT = "AI-CRAFT-"


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


async def arun(database: Database, **kw: Any) -> dict[str, int]:
    engine = create_async_engine(database.ai_async)  # the ai-worker container's login
    try:
        return await run_detectors(engine, environment="DEMO", **kw)
    finally:
        await engine.dispose()


def run(database: Database, **kw: Any) -> dict[str, int]:
    return asyncio.run(arun(database, **kw))


COLS = (
    "source_system_id, record_type, official_offence_act, official_offence_section, official_offence_name, "
    "analytical_category_id, occurrence_start_at, occurrence_end_at, status_code, reporting_station_id, "
    "investigating_unit_id, occurrence_jurisdiction_id, region_id, station_id, beat_id, "
    "sensitivity_classification, access_classification, is_sensitive_category, record_quality_status, is_demo"
)
RECENT = COLS.replace(
    "occurrence_start_at", "%s::timestamptz - (n %% 20) * interval '1 day'"
).replace("occurrence_end_at", "NULL")
LATER = COLS.replace("occurrence_start_at", "occurrence_start_at + interval '10 minutes'")
INSERT_LOCATIONLESS = (
    f"INSERT INTO core.incident_record (source_record_reference, source_version, reported_at, {COLS}) "
    f"SELECT %s || n, 1, %s::timestamptz - (n %% 20) * interval '1 day', {RECENT} "
    "FROM (SELECT * FROM core.incident_record WHERE station_id = %s AND NOT is_sensitive_category "
    "AND access_classification <> 'HIGHLY_RESTRICTED' LIMIT 1) i, generate_series(1, 40) n"
)
INSERT_COPY = (
    f"INSERT INTO core.incident_record (source_record_reference, source_version, reported_at, {COLS}) "
    f"SELECT %s, 1, reported_at + interval '10 minutes', {LATER} FROM core.incident_record WHERE id = %s"
)


def craft(database: Database) -> None:
    """Planted inputs, so each detector has something to find in the test data:
    - 40 recent records at one PDY station without a usable location (data-quality anomaly);
    - a copy of one recent located case 30 m and 10 minutes away, as another report (duplicate)."""
    with psycopg.connect(database.migrator) as conn:
        done = conn.execute(
            "SELECT 1 FROM core.incident_record WHERE source_record_reference LIKE %s LIMIT 1",
            [CRAFT + "%"],
        ).fetchone()
        if done:
            return
        row = conn.execute(
            "SELECT i.station_id, max(i.reported_at) FROM core.incident_record i JOIN core.organizational_unit r "
            "ON r.id = i.region_id WHERE r.code = 'PDY' AND i.station_id IS NOT NULL AND NOT i.is_sensitive_category "
            "GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        station, as_of = row
        conn.execute(INSERT_LOCATIONLESS, [CRAFT + "LOC-", as_of, as_of, station])
        conn.execute(
            "INSERT INTO core.incident_location (incident_id, precision_level, geocode_method, locality, region_id, "
            "station_id, access_classification) SELECT id, 'UNKNOWN', 'NONE', 'Craft locality', region_id, station_id, "
            "access_classification FROM core.incident_record WHERE source_record_reference LIKE %s",
            [CRAFT + "LOC-%"],
        )
        src = conn.execute(
            "SELECT i.id FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id "
            "JOIN core.organizational_unit r ON r.id = i.region_id WHERE r.code = 'PDY' AND l.geometry IS NOT NULL "
            "AND i.record_type = 'CASE' AND NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
            "AND i.occurrence_start_at IS NOT NULL ORDER BY i.occurrence_start_at DESC LIMIT 1"
        ).fetchone()
        assert src is not None
        conn.execute(INSERT_COPY, [CRAFT + "DUP", src[0]])
        conn.execute(
            "INSERT INTO core.incident_location (incident_id, geometry, locality, precision_level, confidence_score, "
            "geocode_method, region_id, station_id, access_classification) "
            "SELECT d.id, ST_Project(l.geometry::geography, 30, radians(90))::geometry, l.locality, l.precision_level, "
            "l.confidence_score, l.geocode_method, l.region_id, l.station_id, l.access_classification "
            "FROM core.incident_record d, core.incident_location l WHERE d.source_record_reference = %s "
            "AND l.incident_id = %s",
            [CRAFT + "DUP", src[0]],
        )


@pytest.fixture(scope="module")
def insights(ingested: Ingested) -> Iterator[dict[str, int]]:
    craft(ingested.database)
    yield run(ingested.database, region="PDY")
    # The planted records would change other tests' counts (ingest, lineage): remove them.
    with psycopg.connect(ingested.database.migrator) as conn:
        conn.execute(
            "DELETE FROM core.incident_location WHERE incident_id IN "
            "(SELECT id FROM core.incident_record WHERE source_record_reference LIKE %s)",
            [CRAFT + "%"],
        )
        conn.execute(
            "DELETE FROM core.incident_record WHERE source_record_reference LIKE %s", [CRAFT + "%"]
        )


def first(database: Database, insight_type: str, state: str = "PENDING") -> str:
    return sql(
        database,
        "SELECT i.id::text FROM core.ai_insight i JOIN core.organizational_unit r ON r.id = i.region_id "
        "WHERE i.insight_type = %s AND i.state = %s AND r.code = 'PDY' ORDER BY i.created_at, i.id LIMIT 1",
        insight_type,
        state,
    )[0][0]


async def test_ai_output_always_includes_evidence_and_limitations(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, insights: dict[str, int]
) -> None:
    for model in (
        "dq-anomaly",
        "duplicate-suggest",
        "geocode-suggest",
        "emerging-pattern",
        "cluster-explain",
    ):
        assert insights.get(model, 0) >= 1, (model, insights)
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    listing = (
        await api.get("/api/v1/ai/insights?region=PDY&limit=200", headers=as_user(analyst))
    ).json()
    assert listing["ai_enabled"] is True
    types = {i["insight_type"] for i in listing["data"]}
    assert types == {"DQ_ANOMALY", "DUPLICATE", "GEOCODE", "EMERGING_PATTERN", "CLUSTER"}
    for t in types:
        item = next(i for i in listing["data"] if i["insight_type"] == t)
        card = (await api.get(f"/api/v1/ai/insights/{item['id']}", headers=as_user(analyst))).json()
        assert card["label"] == "AI-assisted; analyst review required."
        assert card["required_action"] == "Analyst review required."
        for key in ("evidence", "baseline", "exclusions", "geography"):
            assert isinstance(card[key], dict)
        assert card["evidence"]
        assert card["baseline"]
        assert card["limitations"]
        assert card["alternative_explanations"]
        assert card["calibration_note"]
        assert len(card["explanation"]) >= 20
        assert 0 <= card["data_quality_score"] <= 1
        assert 0 <= card["confidence"] <= 1
        assert card["model"].endswith("@1.0.0")
        assert card["history"][0]["to_state"] == "PENDING"
        expected = {"EMERGING_PATTERN": "Polygon", "CLUSTER": "Polygon", "GEOCODE": "Point"}.get(t)
        assert (card["geometry"] or {}).get("type") == expected
        run_out = (
            await api.get(f"/api/v1/ai/runs/{card['input_snapshot_id']}", headers=as_user(analyst))
        ).json()
        assert run_out["status"] == "COMPLETED"
        assert len(run_out["input_sha256"]) == 64
    dup = next(i for i in listing["data"] if i["insight_type"] == "DUPLICATE")
    refs = (await api.get(f"/api/v1/ai/insights/{dup['id']}", headers=as_user(analyst))).json()[
        "references"
    ]
    assert len(refs) == 2
    assert all(r["reference"] for r in refs)  # the analyst may see both records
    # The database refuses an insight without limitations, even from the AI role.
    with psycopg.connect(ingested.database.ai) as conn:
        # A running run of an enabled detector, so only the missing limitations are wrong.
        run_id = conn.execute(
            "INSERT INTO core.ai_run (model_id, region_id, as_of, parameters, status) SELECT model_id, region_id, "
            "current_date, '{}', 'RUNNING' FROM core.ai_insight WHERE insight_type = 'CLUSTER' LIMIT 1 RETURNING id"
        ).fetchone()
        assert run_id is not None
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO core.ai_insight (run_id, model_id, insight_type, dedup_key, region_id, geography, window_start, "
                "window_end, category_label, evidence, baseline, data_quality_score, confidence, confidence_label, "
                "calibration_note, explanation, limitations, alternative_explanations) "
                "SELECT %s, model_id, insight_type, 'no-limits', region_id, geography, window_start, window_end, "
                "category_label, evidence, baseline, data_quality_score, confidence, confidence_label, calibration_note, "
                "explanation, '{}', alternative_explanations FROM core.ai_insight WHERE insight_type = 'CLUSTER' LIMIT 1",
                [run_id[0]],
            )


def test_ai_cannot_create_an_action_export_data_or_modify_a_case(
    ingested: Ingested, insights: dict[str, int]
) -> None:
    del insights
    db = ingested.database
    attempts = [
        "UPDATE core.incident_record SET status_code = 'X' WHERE false",  # the privilege, not the rows
        "UPDATE core.incident_location SET locality = 'X' WHERE false",
        "DELETE FROM core.incident_record WHERE false",
        "INSERT INTO core.operational_action (pattern_review_id) VALUES (gen_random_uuid())",
        "INSERT INTO core.report_export (format) VALUES ('CSV')",
        "INSERT INTO core.analyst_note (subject_type, note) VALUES ('INCIDENT', 'x')",
        "UPDATE core.data_quality_issue SET state = 'RESOLVED' WHERE true",
        "UPDATE core.model_registry SET is_enabled = true WHERE true",
        "UPDATE audit.audit_event SET action = 'X' WHERE false",  # append-only: it may add AI_RUN events
        "SELECT action FROM audit.audit_event LIMIT 1",
        "SELECT official_offence_name FROM core.incident_record LIMIT 1",
        "SELECT * FROM restricted.sensitive_source_detail LIMIT 1",
    ]
    for statement in attempts:
        with psycopg.connect(db.ai) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
    # Phase 7 audit: the AI login cannot become another role, and the worker cannot become it.
    for login, other in (
        (db.ai, "crimemap_worker"),
        (db.ai, "crimemap_app"),
        (db.worker, "crimemap_ai"),
    ):
        with psycopg.connect(login) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SET ROLE {other}")
    with psycopg.connect(db.ai) as conn:
        hidden = conn.execute(
            "SELECT count(*) FROM core.incident_record WHERE is_sensitive_category OR access_classification = 'HIGHLY_RESTRICTED'"
        ).fetchone()
        # Phase 7 audit: nor the locations or data-quality issues of such incidents.
        locations, issues = conn.execute(
            "SELECT (SELECT count(*) FROM core.incident_location), (SELECT count(*) FROM core.data_quality_issue WHERE incident_id IS NOT NULL)"
        ).fetchone()  # type: ignore[misc]
    assert hidden == (0,)  # row-level security: never visible to detectors
    visible = sql(
        db,
        "SELECT (SELECT count(*) FROM core.incident_location l JOIN core.incident_record r ON r.id = l.incident_id "
        "WHERE NOT r.is_sensitive_category AND r.access_classification <> 'HIGHLY_RESTRICTED' "
        "AND l.access_classification IS DISTINCT FROM 'HIGHLY_RESTRICTED'), "
        "(SELECT count(*) FROM core.data_quality_issue q JOIN core.incident_record r ON r.id = q.incident_id "
        "WHERE NOT r.is_sensitive_category AND r.access_classification <> 'HIGHLY_RESTRICTED')",
    )[0]
    assert (locations, issues) == visible
    assert sql(db, "SELECT count(*) > 0 FROM core.incident_record WHERE is_sensitive_category")[0][
        0
    ]


async def test_validation_is_required_audited_and_changes_nothing_else(
    api: httpx.AsyncClient,
    make_user: MakeUser,
    audit_rows: Any,
    ingested: Ingested,
    insights: dict[str, int],
) -> None:
    del insights
    db = ingested.database
    analyst, analyst_id = make_user(Role.CRIME_ANALYST, ("PDY",))
    geocode = first(db, "GEOCODE")
    incident = sql(
        db, "SELECT subject_incident_ids[1]::text FROM core.ai_insight WHERE id = %s", geocode
    )[0][0]
    before = sql(
        db,
        "SELECT ST_AsText(geometry), confidence_score, reviewer_status FROM core.incident_location WHERE incident_id = %s",
        incident,
    )
    actions_before = sql(db, "SELECT count(*) FROM core.operational_action")
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    review = {"decision": "VALIDATE", "note": "Matches the locality; correct at source."}
    assert (
        await api.post(
            f"/api/v1/ai/insights/{geocode}/reviews", json=review, headers=as_user(officer)
        )
    ).status_code == 403
    bad = {"decision": "VALIDATE", "note": "The suspect lives at this locality."}
    assert (
        await api.post(f"/api/v1/ai/insights/{geocode}/reviews", json=bad, headers=as_user(analyst))
    ).status_code == 422
    rid = str(uuid.uuid4())
    r = await api.post(
        f"/api/v1/ai/insights/{geocode}/reviews",
        json=review,
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert r.status_code == 200, r.text
    card = r.json()
    assert (card["state"], card["decision_note"], card["reviewable"]) == (
        "VALIDATED",
        review["note"],
        False,
    )
    assert [h["to_state"] for h in card["history"]] == ["PENDING", "VALIDATED"]
    [event] = audit_rows(rid)
    assert (event["action"], event["actor_user_id"], event["after_meta"]["state"]) == (
        "AI_INSIGHT_REVIEW",
        analyst_id,
        "VALIDATED",
    )
    assert "note" not in event["after_meta"]
    assert (
        await api.post(
            f"/api/v1/ai/insights/{geocode}/reviews", json=review, headers=as_user(analyst)
        )
    ).status_code == 409
    # Validation changed no record and created no action.
    assert (
        sql(
            db,
            "SELECT ST_AsText(geometry), confidence_score, reviewer_status FROM core.incident_location WHERE incident_id = %s",
            incident,
        )
        == before
    )
    assert sql(db, "SELECT count(*) FROM core.operational_action") == actions_before
    # The database allows only the review fields to change, once, by the session user.
    other = first(db, "GEOCODE")
    region = sql(db, "SELECT id::text FROM core.organizational_unit WHERE code = 'PDY'")[0][0]
    _, someone = make_user(Role.CRIME_ANALYST, ("PDY",))
    with psycopg.connect(db.app, autocommit=True) as conn:
        conn.execute(
            "SELECT set_config('app.user_id', %s, false), set_config('app.region_ids', %s, false)",
            [analyst_id, region],
        )
        assert conn.execute(
            "SELECT count(*) FROM core.ai_insight WHERE id = %s", [other]
        ).fetchone() == (1,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # content: no column privilege
            conn.execute(
                "UPDATE core.ai_insight SET explanation = 'An edited explanation of the insight.' WHERE id = %s",
                [other],
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="session user"):  # trigger
            conn.execute(
                "UPDATE core.ai_insight SET state = 'DISMISSED', decision_note = 'forged by another user', "
                "decided_by = %s WHERE id = %s",
                [someone, other],
            )
    # Dismissal works the same way.
    d = await api.post(
        f"/api/v1/ai/insights/{other}/reviews",
        json={"decision": "DISMISS", "note": "Locality text is ambiguous here."},
        headers=as_user(analyst),
    )
    assert d.json()["state"] == "DISMISSED"


async def test_stewards_see_data_quality_insights_only(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, insights: dict[str, int]
) -> None:
    del insights
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    data = (
        await api.get("/api/v1/ai/insights?region=PDY&limit=200", headers=as_user(steward))
    ).json()["data"]
    assert data
    assert {i["insight_type"] for i in data} <= {"DQ_ANOMALY", "DUPLICATE", "GEOCODE"}
    pattern = first(ingested.database, "EMERGING_PATTERN")
    assert (
        await api.get(f"/api/v1/ai/insights/{pattern}", headers=as_user(steward))
    ).status_code == 404
    outsider, _ = make_user(Role.CRIME_ANALYST, ("KKL",))
    assert (
        await api.get(f"/api/v1/ai/insights/{pattern}", headers=as_user(outsider))
    ).status_code == 404
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    assert (await api.get("/api/v1/ai/insights", headers=as_user(officer))).status_code == 403


async def test_model_registry_and_kill_switches(
    api: httpx.AsyncClient,
    make_user: MakeUser,
    audit_rows: Any,
    ingested: Ingested,
    insights: dict[str, int],
) -> None:
    del insights
    db = ingested.database
    admin, _ = make_user(Role.SYSTEM_ADMIN)
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    models = (await api.get("/api/v1/ai/models", headers=as_user(analyst))).json()
    by_code = {m["code"]: m for m in models["data"]}
    assert set(by_code) >= {
        "dq-anomaly",
        "duplicate-suggest",
        "geocode-suggest",
        "emerging-pattern",
        "cluster-explain",
        "briefing-template",
        "forecast-review-priority",
        "copilot-llm",
    }
    for m in models["data"]:
        assert m["intended_use"]
        assert m["prohibited_uses"]
        assert m["limitations"]
        assert m["owner"]
    assert (models["ai_assistance"], models["ai_copilot"], models["ai_forecasting"]) == (
        True,
        False,
        False,
    )
    pattern = by_code["emerging-pattern"]
    off = {"enabled": False, "reason": "Testing the kill switch for patterns."}
    assert (
        await api.post(
            f"/api/v1/ai/models/{pattern['id']}/switch", json=off, headers=as_user(analyst)
        )
    ).status_code == 403
    stale = as_user(admin, auth_age=__import__("datetime").timedelta(minutes=30))
    assert (
        await api.post(f"/api/v1/ai/models/{pattern['id']}/switch", json=off, headers=stale)
    ).status_code == 403
    rid = str(uuid.uuid4())
    r = await api.post(
        f"/api/v1/ai/models/{pattern['id']}/switch",
        json=off,
        headers={**as_user(admin), "X-Request-ID": rid},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_enabled"] is False
    assert [e["action"] for e in audit_rows(rid)] == ["CONFIG_CHANGE"]
    pending = first(db, "EMERGING_PATTERN")
    listing = (
        await api.get("/api/v1/ai/insights?type=EMERGING_PATTERN", headers=as_user(analyst))
    ).json()
    assert pending not in [i["id"] for i in listing["data"]]  # withdrawn from review
    review = {"decision": "VALIDATE", "note": "Reviewed the cell and its baseline."}
    assert (
        await api.post(
            f"/api/v1/ai/insights/{pending}/reviews", json=review, headers=as_user(analyst)
        )
    ).status_code == 409
    assert "emerging-pattern" not in await arun(db, region="PDY", model="emerging-pattern")
    on = {"enabled": True, "reason": "Kill switch test finished; back on."}
    assert (
        await api.post(f"/api/v1/ai/models/{pattern['id']}/switch", json=on, headers=as_user(admin))
    ).json()["is_enabled"]
    # The global switch stops every detector and every review.
    flag = {
        "flag": "ai_assistance",
        "enabled": False,
        "reason": "Testing the global AI kill switch.",
    }
    assert (await api.post("/api/v1/ai/settings", json=flag, headers=as_user(admin))).json()[
        "ai_assistance"
    ] is False
    assert await arun(db, region="PDY") == {}
    assert (
        await api.post(
            f"/api/v1/ai/insights/{pending}/reviews", json=review, headers=as_user(analyst)
        )
    ).status_code == 409
    assert (
        await api.post(
            "/api/v1/ai/settings", json={**flag, "enabled": True}, headers=as_user(admin)
        )
    ).json()["ai_assistance"]


async def test_forecasting_stays_disabled_until_the_rollout_gates_pass(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, insights: dict[str, int]
) -> None:
    del insights
    admin, admin_id = make_user(Role.SYSTEM_ADMIN)
    models = {
        m["code"]: m
        for m in (await api.get("/api/v1/ai/models", headers=as_user(admin))).json()["data"]
    }
    forecast = models["forecast-review-priority"]
    assert (forecast["is_enabled"], forecast["approval_status"], forecast["gates_met"]) == (
        False,
        "DRAFT",
        False,
    )
    on = {"enabled": True, "reason": "Attempt to enable forecasting early."}
    r = await api.post(
        f"/api/v1/ai/models/{forecast['id']}/switch", json=on, headers=as_user(admin)
    )
    assert r.status_code == 409
    for flag in ("ai_forecasting", "ai_copilot"):
        r = await api.post("/api/v1/ai/settings", json={"flag": flag, **on}, headers=as_user(admin))
        assert r.status_code == 409, flag
    # Even approved in the database, an ML model with an unmet gate cannot be switched on.
    with psycopg.connect(ingested.database.migrator) as conn:
        conn.execute(
            "UPDATE core.model_registry SET approval_status = 'APPROVED' WHERE code = 'forecast-review-priority'"
        )
        conn.commit()
    try:
        with psycopg.connect(ingested.database.app) as conn:
            conn.execute("SELECT set_config('app.user_id', %s, false)", [admin_id])
            with pytest.raises(psycopg.Error, match="rollout gate"):
                conn.execute(
                    "UPDATE core.model_registry SET is_enabled = true, changed_by = %s, change_reason = 'gate bypass attempt' "
                    "WHERE code = 'forecast-review-priority'",
                    [admin_id],
                )
    finally:
        with psycopg.connect(ingested.database.migrator) as conn:
            conn.execute(
                "UPDATE core.model_registry SET approval_status = 'DRAFT' WHERE code = 'forecast-review-priority'"
            )
            conn.commit()


async def test_briefings_cite_validated_insights_only_and_the_copilot_is_refused(
    api: httpx.AsyncClient,
    make_user: MakeUser,
    audit_rows: Any,
    ingested: Ingested,
    insights: dict[str, int],
) -> None:
    del insights
    db = ingested.database
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    pending = first(db, "CLUSTER")
    assert (
        await api.post(
            "/api/v1/ai/briefings", json={"insight_ids": [pending]}, headers=as_user(analyst)
        )
    ).status_code == 422
    await api.post(
        f"/api/v1/ai/insights/{pending}/reviews",
        json={"decision": "VALIDATE", "note": "Concentration confirmed on the map."},
        headers=as_user(analyst),
    )
    rid = str(uuid.uuid4())
    b = await api.post(
        "/api/v1/ai/briefings",
        json={"insight_ids": [pending], "title": "Weekly analyst briefing"},
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert b.status_code == 200, b.text
    body = b.json()
    assert body["label"] == "AI-assisted; analyst review required."
    assert [s["heading"] for s in body["sections"]] == [
        "Summary",
        "Findings",
        "Limitations",
        "Alternative explanations",
    ]
    assert body["citations"][0]["insight_id"] == pending
    assert body["citations"][0]["input_snapshot_id"]
    assert "Concentration confirmed on the map." in body["sections"][1]["paragraphs"][0]
    assert [e["action"] for e in audit_rows(rid)] == ["AI_BRIEFING"]
    refused = await api.post(
        "/api/v1/ai/copilot/queries",
        json={"question": "Who is the likely suspect here?"},
        headers=as_user(analyst),
    )
    assert refused.status_code == 422
    disabled = await api.post(
        "/api/v1/ai/copilot/queries",
        json={"question": "Summarise property theft this month"},
        headers=as_user(analyst),
    )
    assert (disabled.status_code, disabled.json()["type"].rsplit("/", 1)[-1]) == (
        503,
        "feature-disabled",
    )


async def test_notes_on_insights_and_the_badge(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, insights: dict[str, int]
) -> None:
    del insights
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    target = first(ingested.database, "DUPLICATE")
    note = await api.post(
        "/api/v1/notes",
        json={
            "subject_type": "AI_INSIGHT",
            "subject_id": target,
            "note": "Checked both reports.",
            "visibility": "RESTRICTED",
        },
        headers=as_user(analyst),
    )
    assert note.status_code == 201, note.text
    outsider, _ = make_user(Role.CRIME_ANALYST, ("KKL",))
    assert (
        await api.get(
            f"/api/v1/notes?subject_type=AI_INSIGHT&subject_id={target}", headers=as_user(outsider)
        )
    ).status_code == 404
    badge = (await api.get("/api/v1/review-queue/summary", headers=as_user(analyst))).json()
    assert badge["ai_insights"] > 0


# --- Phase 7 audit regressions ------------------------------------------------------------------------


def test_feature_flags_are_guarded_in_the_database(make_user: MakeUser, ingested: Ingested) -> None:
    """The API was the only guard: the app role could switch on forecasting or civic data directly."""
    _, admin_id = make_user(Role.SYSTEM_ADMIN)
    with psycopg.connect(ingested.database.app, autocommit=True) as conn:
        conn.execute("SELECT set_config('app.user_id', %s, false)", [admin_id])
        for statement, error in (
            (
                "UPDATE core.feature_flag SET enabled = true, changed_by = %s WHERE key = 'ai_forecasting'",
                "rollout gates",
            ),
            (
                "UPDATE core.feature_flag SET enabled = true, changed_by = %s WHERE key = 'ai_copilot'",
                "rollout gates",
            ),
            (
                "UPDATE core.feature_flag SET enabled = true, changed_by = %s WHERE key = 'civic_context'",
                "AI switches",
            ),
        ):
            with pytest.raises(psycopg.Error, match=error):
                conn.execute(statement, [admin_id])
        _, other = make_user(Role.SYSTEM_ADMIN)
        with pytest.raises(psycopg.Error, match="session user"):
            conn.execute(
                "UPDATE core.feature_flag SET enabled = true, changed_by = %s WHERE key = 'ai_assistance'",
                [other],
            )
    assert sql(
        ingested.database,
        "SELECT enabled FROM core.feature_flag WHERE key IN ('ai_forecasting', 'ai_copilot')",
    ) == [
        (False,),
        (False,),
    ]


def test_the_ai_role_writes_only_for_enabled_detectors(
    ingested: Ingested, insights: dict[str, int]
) -> None:
    """The AI role could write a 'forecast' insight for the draft model, into any run."""
    del insights
    with (
        psycopg.connect(ingested.database.ai) as conn,
        pytest.raises(psycopg.Error, match="approved, enabled"),
    ):
        conn.execute(
            "INSERT INTO core.ai_run (model_id, region_id, as_of, parameters, status) SELECT m.id, r.id, current_date, "
            "'{}', 'RUNNING' FROM core.model_registry m, core.organizational_unit r "
            "WHERE m.code = 'forecast-review-priority' AND r.code = 'PDY'"
        )
    # An insight of another type than its model's, in a running run of that model.
    with psycopg.connect(ingested.database.ai) as conn:
        run_id = conn.execute(
            "INSERT INTO core.ai_run (model_id, region_id, as_of, parameters, status) SELECT m.id, r.id, current_date, "
            "'{}', 'RUNNING' FROM core.model_registry m, core.organizational_unit r "
            "WHERE m.code = 'cluster-explain' AND r.code = 'PDY' RETURNING id"
        ).fetchone()
        assert run_id is not None
        with pytest.raises(psycopg.Error, match="running run"):
            conn.execute(
                "INSERT INTO core.ai_insight (run_id, model_id, insight_type, dedup_key, region_id, geography, window_start, "
                "window_end, category_label, evidence, baseline, data_quality_score, confidence, confidence_label, "
                "calibration_note, explanation, limitations, alternative_explanations) "
                "SELECT %s, m.id, 'EMERGING_PATTERN', 'wrong-type', i.region_id, i.geography, i.window_start, i.window_end, "
                "i.category_label, i.evidence, i.baseline, i.data_quality_score, i.confidence, i.confidence_label, "
                "i.calibration_note, i.explanation, i.limitations, i.alternative_explanations "
                "FROM core.ai_insight i, core.model_registry m WHERE m.code = 'cluster-explain' LIMIT 1",
                [run_id[0]],
            )
    with (
        psycopg.connect(ingested.database.ai) as conn,
        pytest.raises(psycopg.Error, match="running run"),
    ):
        conn.execute(
            "INSERT INTO core.ai_insight (run_id, model_id, insight_type, dedup_key, region_id, geography, window_start, "
            "window_end, category_label, evidence, baseline, data_quality_score, confidence, confidence_label, "
            "calibration_note, explanation, limitations, alternative_explanations) "
            "SELECT run_id, model_id, insight_type, 'into-a-finished-run', region_id, geography, window_start, window_end, "
            "category_label, evidence, baseline, data_quality_score, confidence, confidence_label, calibration_note, "
            "explanation, limitations, alternative_explanations FROM core.ai_insight LIMIT 1"
        )


async def test_auditors_read_insights_and_station_readers_open_their_runs(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, insights: dict[str, int]
) -> None:
    del insights
    auditor, _ = make_user(Role.AUDITOR)
    listing = (await api.get("/api/v1/ai/insights?limit=5", headers=as_user(auditor))).json()
    assert listing["data"], "a global ai.insight.read grant sees insights"
    card = await api.get(
        f"/api/v1/ai/insights/{listing['data'][0]['id']}", headers=as_user(auditor)
    )
    assert card.status_code == 200
    assert card.json()["reviewable"] is False  # auditors read; they do not review
    station, run_id = sql(
        ingested.database,
        "SELECT s.code, i.run_id::text FROM core.ai_insight i JOIN core.organizational_unit s ON s.id = i.station_id "
        "WHERE i.insight_type = 'GEOCODE' LIMIT 1",
    )[0]
    sho, _ = make_user(Role.SHO, stations=(station,))
    assert (await api.get(f"/api/v1/ai/runs/{run_id}", headers=as_user(sho))).status_code == 200
    assert (
        await api.post(
            "/api/v1/ai/briefings",
            json={"insight_ids": [listing["data"][0]["id"]]},
            headers=as_user(sho),
        )
    ).status_code == 403  # briefings are for reviewers


def test_a_failing_detector_is_recorded_and_the_others_still_run(
    monkeypatch: pytest.MonkeyPatch, ingested: Ingested, insights: dict[str, int]
) -> None:
    del insights
    import crimemap_worker.ai as ai

    async def boom(*_: Any) -> Any:
        raise RuntimeError("detector broke")

    monkeypatch.setitem(ai.DETECTORS, "dq-anomaly", boom)
    before = sql(ingested.database, "SELECT count(*) FROM core.ai_run WHERE status = 'COMPLETED'")[
        0
    ][0]
    run(ingested.database, region="PDY")
    failed = sql(
        ingested.database,
        "SELECT r.reason FROM core.ai_run r JOIN core.model_registry m ON m.id = r.model_id "
        "WHERE m.code = 'dq-anomaly' AND r.status = 'FAILED'",
    )
    assert failed == [("RuntimeError",)]
    after = sql(ingested.database, "SELECT count(*) FROM core.ai_run WHERE status = 'COMPLETED'")[
        0
    ][0]
    assert after == before + 4  # the other four detectors ran
    with psycopg.connect(ingested.database.superuser) as conn:
        errors = conn.execute(
            "SELECT count(*) FROM audit.audit_event WHERE action = 'AI_RUN' AND result = 'ERROR' AND reason = 'RuntimeError'"
        ).fetchone()
    assert errors == (1,)
