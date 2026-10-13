"""Regression tests for the Phase 3 audit findings (docs/phase-3-audit.md). Each test names the
finding it guards; each was run against the unfixed code and failed there (see the audit report)."""

import math
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

MakeUser = Callable[..., tuple[str, str]]
PERIOD = "period_from=2024-10-01&period_to=2026-09-30"
IST = ZoneInfo("Asia/Kolkata")


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


async def features(api: httpx.AsyncClient, sub: str, query: str) -> dict[str, Any]:
    r = await api.get(f"/api/v1/map/features?{PERIOD}&{query}", headers=as_user(sub))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _record(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "OPERATIONAL_OBSERVATION",
        "title": "Crowd at junction",
        "occurred_at": "2026-09-20T19:30:00+05:30",
        "location": None,
        "locality": "PDY-S01 demo locality 03",
        "region": "PDY",
        "station": "PDY-S01",
        "precision": "APPROXIMATE",
        "confidence": 0.6,
        "reason": "Seen on patrol",
        "evidence_reference": "patrol log 12",
        "visibility": "RESTRICTED",
    }
    body.update(overrides)
    return body


def _point_in(database: Database, unit_code: str) -> dict[str, float]:
    lon, lat = sql(
        database,
        "SELECT ST_X(ST_PointOnSurface(geometry)), ST_Y(ST_PointOnSurface(geometry)) "
        "FROM core.current_boundary WHERE code = %s",
        unit_code,
    )[0]
    return {"lon": lon, "lat": lat}


# --- API findings ----------------------------------------------------------------------------------


async def test_a_view_box_cannot_locate_the_exact_point_of_a_snapped_record(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """API-1 (critical): the bbox used to test the exact point while returning the snapped one."""
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    points = await features(api, officer, "region=PDY&aggregation=POINTS")
    feature = points["features"][0]
    record, (slon, slat) = feature["properties"]["id"], feature["geometry"]["coordinates"]
    elon, elat = sql(
        ingested.database,
        "SELECT ST_X(geometry), ST_Y(geometry) FROM core.incident_location WHERE incident_id = %s",
        record,
    )[0]
    if abs(elon - slon) < 1e-4 and abs(elat - slat) < 1e-4:
        pytest.skip("this record's exact point is its cell centre")
    d = 2e-5
    around_exact = await features(
        api,
        officer,
        f"region=PDY&station=PDY-S01&aggregation=POINTS&bbox={elon - d},{elat - d},{elon + d},{elat + d}",
    )
    assert record not in {f["properties"]["id"] for f in around_exact["features"]}
    around_snapped = await features(
        api,
        officer,
        f"region=PDY&station=PDY-S01&aggregation=POINTS&bbox={slon - d},{slat - d},{slon + d},{slat + d}",
    )
    assert record in {f["properties"]["id"] for f in around_snapped["features"]}


async def test_combined_callers_cannot_filter_on_one_sensitive_category(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    """API-3: one sensitive category on its own is not 'combined'."""
    viewer, _ = make_user(Role.DEMO_USER)
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/map/features?{PERIOD}&region=PDY&aggregation=HEX&category=CRIMES_AGAINST_WOMEN",
        headers={**as_user(viewer), "X-Request-ID": rid},
    )
    assert r.status_code == 403
    assert [x["result"] for x in audit_rows(rid)] == ["DENY"]
    meta = (await api.get("/api/v1/filters/metadata", headers=as_user(viewer))).json()
    assert not [c for c in meta["categories"] if c["is_sensitive"]]
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    meta = (await api.get("/api/v1/filters/metadata", headers=as_user(analyst))).json()
    assert "CRIMES_AGAINST_WOMEN" in {c["code"] for c in meta["categories"]}


async def test_sensitive_records_are_not_counted_in_location_buckets(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """API-3: masked records used to be added to the no-location and outside counts unthresholded."""
    viewer, _ = make_user(Role.DEMO_USER)
    body = await features(api, viewer, "region=PDY&aggregation=HEX")
    no_location = sql(
        ingested.database,
        "SELECT count(*) FROM core.incident_record i JOIN core.organizational_unit r ON r.id = i.region_id "
        "LEFT JOIN core.incident_location l ON l.incident_id = i.id WHERE r.code = 'PDY' "
        "AND l.geometry IS NULL AND NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
        "AND i.occurrence_start_at >= '2024-10-01 00:00+05:30' AND i.occurrence_start_at < '2026-10-01 00:00+05:30'",
    )[0][0]
    assert body["summary"]["excluded"]["no_location"] == no_location


async def test_a_far_future_period_is_a_validation_error_not_a_crash(
    api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    """API-9: date overflow returned 500."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    r = await api.get(
        "/api/v1/map/features?region=PDY&period_from=9999-12-01&period_to=9999-12-31",
        headers=as_user(analyst),
    )
    assert r.status_code == 422


async def test_hour_precision_is_the_ist_hour(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """API-10: truncation in UTC gave officers half-hours."""
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    record = (await features(api, officer, "region=PDY&aggregation=POINTS"))["features"][0][
        "properties"
    ]["id"]
    summary = (
        await api.get(f"/api/v1/incidents/{record}/summary", headers=as_user(officer))
    ).json()
    assert summary["time_precision"] == "HOUR"
    occurred = datetime.fromisoformat(summary["occurred_from"]).astimezone(IST)
    assert (occurred.minute, occurred.second) == (0, 0)


def _sensitive_incident(database: Database, station: str) -> str:
    rows = sql(
        database,
        "SELECT i.id::text FROM core.incident_record i JOIN core.organizational_unit s ON s.id = i.station_id "
        "JOIN core.incident_location l ON l.incident_id = i.id WHERE s.code = %s AND i.is_sensitive_category "
        "AND i.access_classification <> 'HIGHLY_RESTRICTED' AND l.geometry IS NOT NULL ORDER BY i.id LIMIT 1",
        station,
    )
    if not rows:
        pytest.skip("no located sensitive incident in this station")
    return str(rows[0][0])


async def test_records_about_a_sensitive_incident_need_sensitive_access(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    """API-4: referencing a sensitive incident was an existence oracle and leaked it via listings."""
    incident = _sensitive_incident(ingested.database, "PDY-S01")
    point = _point_in(ingested.database, "PDY-S01-B1")
    body = _record(
        kind="LOCATION_CORRECTION_REQUEST",
        subject_incident_id=incident,
        location=point,
        locality=None,
    )
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    rid = str(uuid.uuid4())
    refused = await api.post(
        "/api/v1/operational-records", json=body, headers={**as_user(officer), "X-Request-ID": rid}
    )
    assert refused.status_code == 404
    assert [x["result"] for x in audit_rows(rid)] == ["DENY"]
    random = await api.post(
        "/api/v1/operational-records",
        json={**body, "subject_incident_id": str(uuid.uuid4())},
        headers=as_user(officer),
    )
    assert random.status_code == 404  # indistinguishable
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    created = await api.post("/api/v1/operational-records", json=body, headers=as_user(analyst))
    assert created.status_code == 201, created.text
    listed = (
        await api.get("/api/v1/operational-records?limit=100", headers=as_user(officer))
    ).json()["data"]
    assert created.json()["id"] not in {r["id"] for r in listed}
    other, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    listed = (
        await api.get("/api/v1/operational-records?limit=100", headers=as_user(other))
    ).json()["data"]
    assert created.json()["id"] in {r["id"] for r in listed}


async def test_a_record_may_only_refer_to_an_incident_in_its_own_region(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """DB-5 / API-4: a PDY record pointing at a KKL incident."""
    kkl = sql(
        ingested.database,
        "SELECT i.id::text FROM core.incident_record i JOIN core.organizational_unit r ON r.id = i.region_id "
        "WHERE r.code = 'KKL' AND NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' LIMIT 1",
    )[0][0]
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY", "KKL"))
    r = await api.post(
        "/api/v1/operational-records",
        json=_record(
            kind="LOCATION_CORRECTION_REQUEST",
            subject_incident_id=kkl,
            location=_point_in(ingested.database, "PDY-S01-B1"),
            locality=None,
        ),
        headers=as_user(analyst),
    )
    assert r.status_code == 404


async def test_record_points_are_snapped_for_callers_without_exact_access(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """API-4: operational-record points were exact for everyone in the station."""
    point = _point_in(ingested.database, "PDY-S01-B2")
    author, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    created = await api.post(
        "/api/v1/operational-records", json=_record(location=point), headers=as_user(author)
    )
    assert created.status_code == 201, created.text
    record = created.json()["id"]

    async def location_for(sub: str) -> dict[str, Any]:
        rows = (
            await api.get("/api/v1/operational-records?limit=100", headers=as_user(sub))
        ).json()["data"]
        return next(r for r in rows if r["id"] == record)

    own = await location_for(author)
    assert own["location"] == pytest.approx(point)
    colleague, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    theirs = await location_for(colleague)
    assert theirs["precision"] == "GRID"
    assert theirs["location"] != pytest.approx(point)
    layer = (
        await api.get("/api/v1/map/operational-records?region=PDY", headers=as_user(colleague))
    ).json()["features"]
    lon, lat = next(f for f in layer if f["properties"]["id"] == record)["geometry"]["coordinates"]
    assert (lat, lon) == pytest.approx((theirs["location"]["lat"], theirs["location"]["lon"]))


async def test_records_layer_refuses_other_territory_and_follows_the_period(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    """API-6 and web finding 11."""
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    rid = str(uuid.uuid4())
    r = await api.get(
        "/api/v1/map/operational-records?region=KKL",
        headers={**as_user(officer), "X-Request-ID": rid},
    )
    assert r.status_code == 403
    assert [x["result"] for x in audit_rows(rid)] == ["DENY"]
    created = await api.post(
        "/api/v1/operational-records",
        json=_record(location=_point_in(ingested.database, "PDY-S01-B1")),
        headers=as_user(officer),
    )
    record = created.json()["id"]

    async def layer(query: str) -> set[str]:
        body = (
            await api.get(
                f"/api/v1/map/operational-records?region=PDY&{query}", headers=as_user(officer)
            )
        ).json()
        return {f["properties"]["id"] for f in body["features"]}

    assert record in await layer("period_from=2026-09-20&period_to=2026-09-20")
    assert record not in await layer("period_from=2026-09-21&period_to=2026-09-30")


def _hex_key(region: str, lon: float, lat: float, crs: int, size: int, database: Database) -> str:
    x, y = sql(
        database,
        "SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s) p) t",
        lon,
        lat,
        crs,
    )[0]
    q, r = (math.sqrt(3) / 3 * x - y / 3) / size, (2 / 3 * y) / size
    cy = -q - r
    rq, rr, ry = round(q), round(r), round(cy)
    if abs(rq - q) > abs(ry - cy) and abs(rq - q) > abs(rr - r):
        rq = -ry - rr
    elif abs(ry - cy) <= abs(rr - r):
        rr = -rq - ry
    return f"{region}:{size}:{rq}:{rr}"


async def test_cell_notes_belong_to_the_station_holding_the_cell(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    """API-7: cell notes had no territory check and station officers could not use them."""
    point = _point_in(ingested.database, "PDY-S01")
    crs = sql(
        ingested.database, "SELECT analysis_crs FROM core.organizational_unit WHERE code = 'PDY'"
    )[0][0]
    key = _hex_key("PDY", point["lon"], point["lat"], crs, 250, ingested.database)
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    note = {
        "subject_type": "CELL",
        "subject_key": key,
        "note": "busy junction",
        "visibility": "RESTRICTED",
    }
    created = await api.post("/api/v1/notes", json=note, headers=as_user(officer))
    assert created.status_code == 201, created.text
    listed = await api.get(
        f"/api/v1/notes?subject_type=CELL&subject_key={key}", headers=as_user(officer)
    )
    assert [n["note"] for n in listed.json()["data"]] == ["busy junction"]
    for outsider in (
        make_user(Role.STATION_OFFICER, stations=("PDY-S02",))[0],
        make_user(Role.CRIME_ANALYST, ("KKL",))[0],
    ):
        rid = str(uuid.uuid4())
        r = await api.get(
            f"/api/v1/notes?subject_type=CELL&subject_key={key}",
            headers={**as_user(outsider), "X-Request-ID": rid},
        )
        assert r.status_code == 404
        assert [x["result"] for x in audit_rows(rid)] == ["DENY"]


async def test_search_locality_counts_leave_out_sensitive_records(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """API-5: locality counts minus visible points revealed sensitive counts."""
    rows = sql(
        ingested.database,
        "SELECT l.locality, count(*) FILTER (WHERE NOT i.is_sensitive_category) FROM core.incident_location l "
        "JOIN core.incident_record i ON i.id = l.incident_id JOIN core.organizational_unit s ON s.id = i.station_id "
        "WHERE s.code = 'PDY-S01' AND i.access_classification <> 'HIGHLY_RESTRICTED' AND l.locality IS NOT NULL "
        "GROUP BY 1 HAVING bool_or(i.is_sensitive_category) ORDER BY 1 LIMIT 1",
    )
    if not rows:
        pytest.skip("no PDY-S01 locality with sensitive records")
    locality, expected = rows[0]
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    body = (await api.get(f"/api/v1/search?q={locality}", headers=as_user(officer))).json()
    hit = next(h for h in body["localities"] if h["locality"] == locality)
    assert hit["records"] == expected


async def test_search_offers_categories_and_dates_and_redacts_identifiers(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Spec gap G2 (category, date range) and API-8 (redaction)."""
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    body = (await api.get("/api/v1/search?q=2026-07", headers=as_user(officer))).json()
    assert body["period"] == {"period_from": "2026-07-01", "period_to": "2026-07-31"}
    body = (await api.get("/api/v1/search?q=theft", headers=as_user(officer))).json()
    assert body["categories"]
    body = (await api.get("/api/v1/search?q=women", headers=as_user(officer))).json()
    assert not body["categories"]  # sensitive categories only with sensitive access
    rid = str(uuid.uuid4())
    await api.get(
        "/api/v1/search?q=%2B91-98765-43210", headers={**as_user(officer), "X-Request-ID": rid}
    )
    with psycopg.connect(ingested.database.superuser) as conn:
        recorded = conn.execute(
            "SELECT filters::text FROM audit.audit_event WHERE request_id = %s", [rid]
        ).fetchall()
    assert recorded
    assert "98765" not in str(recorded)
    assert "redacted" in str(recorded)


async def test_flagging_again_after_resolution_reopens_the_issue(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """API-11: a resolved user flag blocked any later flag with 409."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    record = (await features(api, analyst, "region=PDY&aggregation=POINTS"))["features"][2][
        "properties"
    ]["id"]
    flag = {"issue_type": "MISSING_OCCURRENCE_TIME", "note": "time looks wrong"}
    first = await api.post(
        f"/api/v1/incidents/{record}/data-issues", json=flag, headers=as_user(analyst)
    )
    assert first.status_code == 201, first.text
    with psycopg.connect(ingested.database.migrator) as conn:
        conn.execute(
            "UPDATE core.data_quality_issue SET state = 'RESOLVED', resolution_note = 'fixed' WHERE id = %s",
            [first.json()["id"]],
        )
    again = await api.post(
        f"/api/v1/incidents/{record}/data-issues", json=flag, headers=as_user(analyst)
    )
    assert again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    assert sql(
        ingested.database,
        "SELECT state FROM core.data_quality_issue WHERE id = %s",
        first.json()["id"],
    ) == [("OPEN",)]


# --- database findings -------------------------------------------------------------------------------


def _app(database: Database, user_id: str) -> psycopg.Connection[Any]:
    region = sql(database, "SELECT id::text FROM core.organizational_unit WHERE code = 'PDY'")[0][0]
    conn = psycopg.connect(database.app)
    conn.execute(
        "SELECT set_config('app.user_id', %s, false), set_config('app.region_ids', %s, false)",
        [user_id, region],
    )
    return conn


_INSERT_RECORD = (
    "INSERT INTO core.operational_record (record_kind, title, occurred_at, locality, precision_level, "
    "confidence, reason, visibility_classification, review_status, region_id, created_by) "
    "SELECT %s, 'title', now(), 'somewhere', 'UNKNOWN', 0.5, 'reason', 'RESTRICTED', %s, id, %s "
    "FROM core.organizational_unit WHERE code = 'PDY' RETURNING id"
)


def test_the_database_starts_records_unreviewed_and_by_the_session_user(
    make_user: MakeUser, ingested: Ingested
) -> None:
    """DB-2: the app role could insert APPROVED records or forge the author."""
    _, me = make_user(Role.CRIME_ANALYST, ("PDY",))
    _, someone = make_user(Role.CRIME_ANALYST, ("PDY",))
    for kind, status, author in (
        ("APPROVED_EVENT_MARKER", "APPROVED", me),
        ("APPROVED_EVENT_MARKER", "NOT_REQUIRED", me),
        ("OPERATIONAL_OBSERVATION", "NOT_REQUIRED", someone),
    ):
        with _app(ingested.database, me) as conn, pytest.raises(psycopg.Error):
            conn.execute(_INSERT_RECORD, [kind, status, author])
    with _app(ingested.database, me) as conn:
        assert conn.execute(
            _INSERT_RECORD, ["OPERATIONAL_OBSERVATION", "NOT_REQUIRED", me]
        ).fetchone()


def test_the_database_decides_a_record_once_by_another_session_user(
    make_user: MakeUser, ingested: Ingested
) -> None:
    """DB-2: PENDING -> NOT_REQUIRED, or approval with a forged or missing reviewer."""
    _, author = make_user(Role.CRIME_ANALYST, ("PDY",))
    _, reviewer = make_user(Role.CRIME_ANALYST, ("PDY",))
    with _app(ingested.database, author) as conn:
        row = conn.execute(_INSERT_RECORD, ["APPROVED_EVENT_MARKER", "PENDING", author]).fetchone()
        assert row is not None
        record = row[0]
    bad = (
        (
            "UPDATE core.operational_record SET review_status = 'NOT_REQUIRED' WHERE id = %s",
            [record],
        ),
        ("UPDATE core.operational_record SET review_status = 'APPROVED' WHERE id = %s", [record]),
        (
            "UPDATE core.operational_record SET review_status = 'APPROVED', reviewed_by = %s WHERE id = %s",
            [author, record],
        ),
    )
    for statement, params in bad:
        with _app(ingested.database, reviewer) as conn, pytest.raises(psycopg.Error):
            conn.execute(statement, params)
    with _app(ingested.database, reviewer) as conn:
        conn.execute(
            "UPDATE core.operational_record SET review_status = 'APPROVED', reviewed_by = %s WHERE id = %s",
            [reviewer, record],
        )
    assert sql(
        ingested.database,
        "SELECT review_status, reviewed_by::text, reviewed_at IS NOT NULL FROM core.operational_record WHERE id = %s",
        record,
    ) == [("APPROVED", reviewer, True)]


def test_the_api_role_can_only_insert_user_flags(make_user: MakeUser, ingested: Ingested) -> None:
    """DB-3: the app role could insert RULE/AI issues and squat rule codes."""
    _, me = make_user(Role.CRIME_ANALYST, ("PDY",))
    incident, region = sql(
        ingested.database,
        "SELECT i.id::text, i.region_id::text FROM core.incident_record i JOIN core.organizational_unit r "
        "ON r.id = i.region_id WHERE r.code = 'PDY' AND NOT i.is_sensitive_category "
        "AND i.access_classification <> 'HIGHLY_RESTRICTED' LIMIT 1",
    )[0]
    insert = (
        "INSERT INTO core.data_quality_issue (incident_id, subject_type, subject_id, issue_type, severity, "
        "detected_by, rule_code, region_id) VALUES (%s, 'INCIDENT', %s, 'OTHER', 'HIGH', %s, %s, %s)"
    )
    for detected_by, rule in (("RULE", "DQ-LOC-001"), ("AI", "AI-X"), ("USER", "DQ-LOC-001")):
        with _app(ingested.database, me) as conn, pytest.raises(psycopg.Error):
            conn.execute(insert, [incident, incident, detected_by, rule, region])
    with _app(ingested.database, me) as conn:
        conn.execute(insert, [incident, incident, "USER", "USER-OTHER", region])


def test_a_location_write_waits_for_a_concurrent_incident_move(ingested: Ingested) -> None:
    """DB-1: the scope copy could be computed from a row being moved/reclassified concurrently."""
    incident = sql(
        ingested.database,
        "SELECT incident_id::text FROM core.incident_location WHERE geometry IS NOT NULL LIMIT 1",
    )[0][0]
    with (
        psycopg.connect(ingested.database.migrator) as mover,
        psycopg.connect(ingested.database.migrator) as writer,
    ):
        # Any update locks the incident row (a scope change would also lock the location row
        # through the propagation trigger, which would hide what this test is about).
        mover.execute(
            "UPDATE core.incident_record SET access_classification = access_classification WHERE id = %s",
            [incident],
        )
        writer.execute("SET lock_timeout = '300ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            writer.execute(
                "UPDATE core.incident_location SET locality = locality WHERE incident_id = %s",
                [incident],
            )
        writer.rollback()
        mover.rollback()


async def test_review_queue_badge_counts_what_the_caller_may_decide(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Spec gap G6: the Review Queue badge count."""
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    supervisor, _ = make_user(Role.DISTRICT_OR_REGION_SUPERVISOR, ("PDY",))
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))

    async def counts(sub: str) -> dict[str, Any]:
        r = await api.get("/api/v1/review-queue/summary", headers=as_user(sub))
        assert r.status_code == 200, r.text
        return r.json()  # type: ignore[no-any-return]

    before = await counts(supervisor)
    created = await api.post(
        "/api/v1/operational-records",
        json=_record(kind="APPROVED_EVENT_MARKER", title="Temple festival"),
        headers=as_user(officer),
    )
    assert created.status_code == 201, created.text
    after = await counts(supervisor)
    assert after["operational_records"] == before["operational_records"] + 1
    steward_counts = await counts(steward)
    assert steward_counts["data_quality"] > 0
    pending = (
        await api.get(
            "/api/v1/operational-records?review_status=PENDING&limit=100", headers=as_user(steward)
        )
    ).json()["data"]
    assert steward_counts["operational_records"] == len(pending)  # corrections only, like the queue
    assert (await counts(officer)) == {
        "data_quality": None,
        "operational_records": None,
        "more": False,
        "pattern_reviews": None,
        "actions": 0,  # Phase 6: actions assigned to them for completion
        "outcomes": None,
        "ai_insights": None,  # Phase 7: officers do not review AI insights
    }
