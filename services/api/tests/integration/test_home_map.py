"""Phase 3 acceptance at the API (Home Map): territory scope (HM-2), consistent counts, legend and
status bar (HM-1), field masking by role (HM-3), precision reduction (ADR-020), small-cell
suppression (ADR-012), search, operational records with no FIR path (HM-4, HM-5), notes and
user-flagged data issues. Uses the shared synthetic session dataset."""

import re
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role
from crimemap_api.routes.map import SUPPRESSION_LABEL

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

MakeUser = Callable[..., tuple[str, str]]
PERIOD = "period_from=2024-10-01&period_to=2026-09-30"


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


async def features(api: httpx.AsyncClient, sub: str, query: str, **kw: Any) -> dict[str, Any]:
    r = await api.get(f"/api/v1/map/features?{PERIOD}&{query}", headers=as_user(sub, **kw))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# --- filter metadata ------------------------------------------------------------------------------


async def test_filter_metadata_offers_only_the_callers_territory(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    body = (await api.get("/api/v1/filters/metadata", headers=as_user(officer))).json()
    assert [r["code"] for r in body["regions"]] == ["PDY"]
    assert [s["code"] for s in body["stations"]] == ["PDY-S01"]
    assert all(b["station_code"] == "PDY-S01" for b in body["beats"])
    assert "POINTS" in body["aggregations"]
    assert body["exact_locations"] is False
    analyst, _ = make_user(Role.CRIME_ANALYST, ("KKL", "MAH"))
    body = (await api.get("/api/v1/filters/metadata?region=MAH", headers=as_user(analyst))).json()
    assert [r["code"] for r in body["regions"]] == ["KKL", "MAH"]
    assert {s["code"] for s in body["stations"]} == {"MAH-S01", "MAH-S02"}
    assert body["exact_locations"] is True
    assert {c["code"] for c in body["categories"]} >= {"PROPERTY_THEFT", "CRIMES_AGAINST_WOMEN"}
    viewer, _ = make_user(Role.DEMO_USER)
    body = (await api.get("/api/v1/filters/metadata", headers=as_user(viewer))).json()
    assert body["aggregations"] == ["HEX", "BEAT"]
    assert [r["code"] for r in body["regions"]] == ["KKL", "MAH", "PDY", "YAN"]


# --- HM-2: territory scope ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "region=KKL&aggregation=HEX",
        "region=PDY&station=PDY-S02&aggregation=POINTS",
        "region=PDY&beat=PDY-S02-B1",
    ],
)
async def test_out_of_territory_filters_are_refused_and_audited(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested, query: str
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/map/features?{PERIOD}&{query}", headers={**as_user(officer), "X-Request-ID": rid}
    )
    assert r.status_code == 403
    assert [(x["action"], x["result"]) for x in audit_rows(rid)] == [("AUTHZ_DENIED", "DENY")]


async def test_station_officer_sees_only_their_station(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    body = await features(api, officer, "region=PDY&aggregation=POINTS")
    station = sql(
        ingested.database, "SELECT id::text FROM core.organizational_unit WHERE code = 'PDY-S01'"
    )[0][0]
    ids = [f["properties"]["id"] for f in body["features"]]
    assert ids
    stations = {
        r[0]
        for r in sql(
            ingested.database,
            "SELECT station_id::text FROM core.incident_record WHERE id::text = ANY(%s)",
            ids,
        )
    }
    assert stations == {station}
    assert (
        body["summary"]["selected"]
        == sql(
            ingested.database,
            "SELECT count(*) FROM core.incident_record WHERE station_id = %s AND access_classification <> 'HIGHLY_RESTRICTED' "
            "AND occurrence_start_at >= '2024-10-01 00:00+05:30' AND occurrence_start_at < '2026-10-01 00:00+05:30'",
            station,
        )[0][0]
    )


async def test_records_of_another_territory_are_not_found(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    kkl = sql(
        ingested.database,
        "SELECT i.id::text FROM core.incident_record i JOIN core.organizational_unit o "
        "ON o.id = i.region_id WHERE o.code = 'KKL' AND NOT i.is_sensitive_category LIMIT 1",
    )[0][0]
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/incidents/{kkl}/summary", headers={**as_user(analyst), "X-Request-ID": rid}
    )
    assert r.status_code == 404
    assert [x["result"] for x in audit_rows(rid)] == ["DENY"]
    assert (await api.get(f"/api/v1/incidents/{kkl}", headers=as_user(analyst))).status_code == 404


# --- HM-1: counts, legend and status bar agree ---------------------------------------------------


@pytest.mark.parametrize("aggregation", ["POINTS", "HEX", "BEAT"])
async def test_counts_legend_and_status_bar_are_consistent(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, aggregation: str
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    body = await features(api, analyst, f"region=PDY&aggregation={aggregation}")
    s, x = body["summary"], body["summary"]["excluded"]
    assert body["aggregation"]["applied"] == aggregation
    assert s["selected"] == s["usable_locations"] + x["no_location"] + x["outside_jurisdiction"]
    if aggregation == "POINTS":
        assert s["shown"] == len(body["features"])
        assert (
            s["usable_locations"]
            == s["shown"]
            + x["aggregate_only_residential"]
            + x["aggregate_only_sensitive"]
            + x["outside_view"]
        )
    else:
        assert s["shown"] == sum(f["properties"]["count"] for f in body["features"])
        assert s["usable_locations"] == s["shown"]
    assert sum(item["count"] for item in body["legend"]) == s["shown"]
    assert s["selected"] > 0
    assert s["shown"] > 0


async def test_filters_change_the_counts(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    everything = await features(api, analyst, "region=PDY&aggregation=HEX")
    theft = await features(api, analyst, "region=PDY&aggregation=HEX&category=PROPERTY_THEFT")
    confident = await features(api, analyst, "region=PDY&aggregation=HEX&min_confidence=0.8")
    assert 0 < theft["summary"]["selected"] < everything["summary"]["selected"]
    assert [i["code"] for i in theft["legend"]] == ["PROPERTY_THEFT"]
    assert confident["summary"]["excluded"]["no_location"] == 0
    assert confident["summary"]["selected"] < everything["summary"]["selected"]
    reported = await features(api, analyst, "region=PDY&aggregation=HEX&time_basis=REPORTED")
    assert reported["period"]["basis"] == "REPORTED"


# --- ADR-020: precision -----------------------------------------------------------------------------


def _coordinates(body: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {f["properties"]["id"]: tuple(f["geometry"]["coordinates"]) for f in body["features"]}  # type: ignore[misc]


async def test_exact_points_only_for_roles_with_exact_location_permission(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    q = "region=PDY&station=PDY-S01&aggregation=POINTS"
    exact = await features(api, analyst, q)
    snapped = await features(api, officer, q)
    assert exact["location_precision"] == "EXACT"
    assert snapped["location_precision"] == "SNAPPED_250M"
    stored = dict(
        sql(
            ingested.database,
            "SELECT incident_id::text, ARRAY[ST_X(geometry), ST_Y(geometry)] FROM core.incident_location "
            "WHERE incident_id::text = ANY(%s)",
            list(_coordinates(exact)),
        )
    )
    assert all(
        abs(c[0] - stored[i][0]) < 1e-9 and abs(c[1] - stored[i][1]) < 1e-9
        for i, c in _coordinates(exact).items()
    )
    moved = [
        i
        for i, c in _coordinates(snapped).items()
        if abs(c[0] - stored[i][0]) > 1e-9 or abs(c[1] - stored[i][1]) > 1e-9
    ]
    assert len(moved) > 0.9 * len(_coordinates(snapped))
    assert all(f["properties"]["precision"] == "SNAPPED_250M" for f in snapped["features"])


async def test_steward_gets_exact_points_only_under_the_dq_review_purpose(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    q = "region=PDY&station=PDY-S02&aggregation=POINTS"
    assert (await features(api, steward, q))["location_precision"] == "SNAPPED_250M"
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/map/features?{PERIOD}&{q}&purpose=DQ_REVIEW",
        headers={**as_user(steward), "X-Request-ID": rid},
    )
    body = r.json()
    assert [x["purpose"] for x in audit_rows(rid)] == ["DQ_REVIEW"]
    # The purpose unlocks exact points for records under geocode review only (audit finding).
    under_review = {
        str(row[0])
        for row in sql(
            ingested.database,
            "SELECT incident_id FROM core.data_quality_issue WHERE state IN ('OPEN', 'ASSIGNED') "
            "AND issue_type IN ('LOW_CONFIDENCE_LOCATION', 'MISSING_LOCATION', 'OUTSIDE_JURISDICTION') "
            "AND incident_id IS NOT NULL",
        )
    }
    exact = {
        f["properties"]["id"] for f in body["features"] if f["properties"]["precision"] == "EXACT"
    }
    shown = {f["properties"]["id"] for f in body["features"]}
    assert exact
    assert exact == shown & under_review
    assert body["location_precision"] == "MIXED"


async def test_residential_and_highly_restricted_records_are_never_points(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY", "KKL", "MAH", "YAN"))
    ids: set[str] = set()
    for region in ("PDY", "KKL", "MAH", "YAN"):
        ids |= set(
            _coordinates(await features(api, analyst, f"region={region}&aggregation=POINTS"))
        )
    bad = sql(
        ingested.database,
        "SELECT count(*) FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id "
        "WHERE i.id::text = ANY(%s) AND (l.is_residential OR i.access_classification = 'HIGHLY_RESTRICTED')",
        list(ids),
    )
    assert ids
    assert bad == [(0,)]


# --- ADR-012: aggregate-only roles and sensitive categories ---------------------------------------


async def test_aggregate_roles_get_no_points_and_small_sensitive_cells_are_suppressed(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    viewer, _ = make_user(Role.DEMO_USER)
    r = await api.get(
        f"/api/v1/map/features?{PERIOD}&region=PDY&aggregation=POINTS", headers=as_user(viewer)
    )
    assert r.status_code == 403
    body = await features(api, viewer, "region=PDY&aggregation=HEX&hex_size_m=250")
    codes: set[str] = set()
    for f in body["features"]:
        sensitive = f["properties"]["sensitive"]
        assert sensitive["count"] is None or sensitive["count"] >= 3
        codes |= {c["code"] for c in f["properties"]["categories"]}
    assert not codes & {"CRIMES_AGAINST_WOMEN", "CRIMES_AGAINST_CHILDREN"}
    # No count of what was left out, ever: 0 and 2 must look the same (audit finding).
    assert body["summary"]["excluded"]["suppressed_small_cells"] == {
        "count": None,
        "label": SUPPRESSION_LABEL,
    }


# Non-sensitive, visible records of one beat in PERIOD by occurrence (what a masked caller may count).
_VISIBLE_IN_BEAT = (
    "SELECT count(*) FROM core.incident_record i JOIN core.organizational_unit b ON b.id = i.beat_id "
    "WHERE b.code = %s AND NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
    "AND i.occurrence_start_at >= '2024-10-01 00:00+05:30' AND i.occurrence_start_at < '2026-10-01 00:00+05:30'"
)
_TINY_SENSITIVE_BEAT = (
    "SELECT b.code, st.code FROM core.incident_record i JOIN core.organizational_unit b ON b.id = i.beat_id "
    "JOIN core.organizational_unit st ON st.id = i.station_id JOIN core.organizational_unit r ON r.id = i.region_id "
    "WHERE r.code = 'PDY' AND i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
    "AND i.occurrence_start_at >= '2024-10-01 00:00+05:30' AND i.occurrence_start_at < '2026-10-01 00:00+05:30' "
    "GROUP BY 1, 2 HAVING count(*) BETWEEN 1 AND 2 LIMIT 1"
)


async def test_a_tiny_sensitive_selection_is_not_counted_at_all(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Narrowing filters must not reveal 1-2 sensitive records through the status-bar totals."""
    viewer, _ = make_user(Role.DEMO_USER)
    row = sql(ingested.database, _TINY_SENSITIVE_BEAT)
    if not row:
        pytest.skip("no beat with 1-2 sensitive records in the test dataset")
    beat = row[0][0]
    body = await features(api, viewer, f"region=PDY&aggregation=BEAT&beat={beat}")
    assert body["summary"]["selected"] == sql(ingested.database, _VISIBLE_IN_BEAT, beat)[0][0]
    assert body["summary"]["excluded"]["suppressed_small_cells"]["label"] == SUPPRESSION_LABEL


async def test_station_officer_sees_sensitive_categories_only_in_aggregate(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    points = await features(api, officer, "region=PDY&aggregation=POINTS")
    sensitive = {
        r[0]
        for r in sql(
            ingested.database,
            "SELECT id::text FROM core.incident_record WHERE is_sensitive_category",
        )
    }
    assert not set(_coordinates(points)) & sensitive
    assert points["summary"]["excluded"]["aggregate_only_sensitive"] >= 0


# --- HM-3: popup field masking -------------------------------------------------------------------

POPUP_FIELDS = {
    "id",
    "reference",
    "category_code",
    "category_label",
    "offence_section",
    "offence_name",
    "event_type",
    "occurred_from",
    "occurred_to",
    "reported_at",
    "locality",
    "location_precision",
    "location_confidence",
    "station",
    "region",
    "status",
    "time_precision",
    "source_system",
}


async def test_popup_shows_only_permitted_fields_and_never_coordinates(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    point = (await features(api, officer, "region=PDY&aggregation=POINTS"))["features"][0][
        "properties"
    ]["id"]
    full = (await api.get(f"/api/v1/incidents/{point}/summary", headers=as_user(analyst))).json()
    hourly = (await api.get(f"/api/v1/incidents/{point}/summary", headers=as_user(officer))).json()
    assert set(full) == POPUP_FIELDS == set(hourly)
    for body in (full, hourly):
        text = str(body)
        assert "lat" not in body
        assert "lon" not in body
        assert "geometry" not in text
        assert "victim" not in text
        assert "witness" not in text
    assert full["time_precision"] == "MINUTE"
    assert hourly["time_precision"] == "HOUR"
    assert hourly["reported_at"] is None or re.search(r"T\d\d:00:00", hourly["reported_at"])


async def test_sensitive_record_popup_is_withheld_without_sensitive_access(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sensitive = sql(
        ingested.database,
        "SELECT i.id::text FROM core.incident_record i JOIN core.organizational_unit o ON o.id = i.station_id "
        "WHERE o.code = 'PDY-S01' AND i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' LIMIT 1",
    )
    if not sensitive:
        pytest.skip("no sensitive record at PDY-S01 in the test dataset")
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    assert (
        await api.get(f"/api/v1/incidents/{sensitive[0][0]}/summary", headers=as_user(officer))
    ).status_code == 404
    assert (
        await api.get(f"/api/v1/incidents/{sensitive[0][0]}/summary", headers=as_user(sho))
    ).status_code == 200


async def test_permitted_details_are_audited_and_add_quality_facts(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    point = (await features(api, analyst, "region=PDY&aggregation=POINTS"))["features"][0][
        "properties"
    ]["id"]
    rid = str(uuid.uuid4())
    body = (
        await api.get(
            f"/api/v1/incidents/{point}", headers={**as_user(analyst), "X-Request-ID": rid}
        )
    ).json()
    assert {
        "record_type",
        "quality_status",
        "open_issues",
        "source_version",
        "beat",
        "reporting_station",
    } <= set(body)
    assert [(x["action"], x["object_type"]) for x in audit_rows(rid)] == [
        ("RECORD_VIEW", "incident")
    ]


# --- search ------------------------------------------------------------------------------------------


async def test_search_finds_references_units_and_localities_in_scope(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    ref = sql(
        ingested.database,
        "SELECT i.source_record_reference FROM core.incident_record i JOIN core.organizational_unit o "
        "ON o.id = i.station_id WHERE o.code = 'PDY-S01' AND NOT i.is_sensitive_category "
        "AND i.access_classification <> 'HIGHLY_RESTRICTED' LIMIT 1",
    )[0][0]
    other = sql(
        ingested.database,
        "SELECT i.source_record_reference FROM core.incident_record i JOIN core.organizational_unit o "
        "ON o.id = i.station_id WHERE o.code = 'KKL-S01' LIMIT 1",
    )[0][0]
    body = (await api.get(f"/api/v1/search?q={ref}", headers=as_user(officer))).json()
    assert [r["reference"] for r in body["incidents"]] == [ref]
    assert (await api.get(f"/api/v1/search?q={other}", headers=as_user(officer))).json()[
        "incidents"
    ] == []
    units = (await api.get("/api/v1/search?q=PDY-S01", headers=as_user(officer))).json()["units"]
    assert "PDY-S01" in {u["code"] for u in units}
    localities = (await api.get("/api/v1/search?q=demo locality", headers=as_user(officer))).json()[
        "localities"
    ]
    assert localities
    assert all(loc["station_code"] == "PDY-S01" for loc in localities)
    rid = str(uuid.uuid4())
    await api.get("/api/v1/search?q=9876543210", headers={**as_user(officer), "X-Request-ID": rid})
    [event] = audit_rows(rid)
    assert event["action"] == "SEARCH"
    assert "9876543210" not in str(event)


# --- HM-4 / HM-5: operational records ---------------------------------------------------------


def _record(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "OPERATIONAL_OBSERVATION",
        "title": "Street light out near market",
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


async def test_there_is_no_fir_kind(api: httpx.AsyncClient, make_user: MakeUser) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    for kind in ("FIR", "CASE", "INCIDENT"):
        r = await api.post(
            "/api/v1/operational-records", json=_record(kind=kind), headers=as_user(officer)
        )
        assert r.status_code == 422


async def test_operational_record_is_audited_and_review_kinds_enter_the_queue(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    before = sql(ingested.database, "SELECT count(*), max(updated_at) FROM core.incident_record")
    rid = str(uuid.uuid4())
    r = await api.post(
        "/api/v1/operational-records",
        json=_record(),
        headers={**as_user(officer), "X-Request-ID": rid},
    )
    assert r.status_code == 201, r.text
    assert r.json()["review_status"] == "NOT_REQUIRED"
    assert [x["action"] for x in audit_rows(rid)] == ["OPERATIONAL_RECORD_CREATE"]
    marker = await api.post(
        "/api/v1/operational-records",
        json=_record(kind="APPROVED_EVENT_MARKER", title="Temple festival"),
        headers=as_user(officer),
    )
    assert marker.json()["review_status"] == "PENDING"
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    queue = (
        await api.get("/api/v1/operational-records?review_status=PENDING", headers=as_user(sho))
    ).json()["data"]
    assert marker.json()["id"] in {q["id"] for q in queue}
    assert (
        sql(ingested.database, "SELECT count(*), max(updated_at) FROM core.incident_record")
        == before
    )


async def test_review_needs_a_different_person_and_the_right_role(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    sho, _sho_id = make_user(Role.SHO, stations=("PDY-S01",))
    created = (
        await api.post(
            "/api/v1/operational-records",
            json=_record(kind="APPROVED_EVENT_MARKER"),
            headers=as_user(sho),
        )
    ).json()
    url = f"/api/v1/operational-records/{created['id']}/reviews"
    own = await api.post(url, json={"decision": "APPROVE", "note": "fine"}, headers=as_user(sho))
    assert own.status_code == 409
    assert own.json()["type"].endswith("separation-of-duties")
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    assert (
        await api.post(url, json={"decision": "APPROVE", "note": "fine"}, headers=as_user(steward))
    ).status_code == 403
    other_sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    ok = await api.post(
        url,
        json={"decision": "APPROVE", "note": "checked with beat officer"},
        headers=as_user(other_sho),
    )
    assert ok.status_code == 200
    assert ok.json()["review_status"] == "APPROVED"
    again = await api.post(
        url, json={"decision": "REJECT", "note": "changed my mind"}, headers=as_user(other_sho)
    )
    assert again.status_code == 409


async def test_location_correction_needs_an_incident_and_a_point_and_a_steward_may_review_it(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    incident = (await features(api, officer, "region=PDY&aggregation=POINTS"))["features"][0][
        "properties"
    ]["id"]
    missing = await api.post(
        "/api/v1/operational-records",
        json=_record(kind="LOCATION_CORRECTION_REQUEST"),
        headers=as_user(officer),
    )
    assert missing.status_code == 422
    point = sql(
        ingested.database,
        "SELECT ST_X(ST_PointOnSurface(geometry)), ST_Y(ST_PointOnSurface(geometry)) FROM core.current_boundary WHERE code = 'PDY-S01-B1'",
    )[0]
    created = await api.post(
        "/api/v1/operational-records",
        json=_record(
            kind="LOCATION_CORRECTION_REQUEST",
            subject_incident_id=incident,
            location={"lat": point[1], "lon": point[0]},
            locality=None,
        ),
        headers=as_user(officer),
    )
    assert created.status_code == 201, created.text
    assert created.json()["station"] == "PDY-S01"
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    ok = await api.post(
        f"/api/v1/operational-records/{created.json()['id']}/reviews",
        json={"decision": "APPROVE", "note": "matches the locality"},
        headers=as_user(steward),
    )
    assert ok.status_code == 200


async def test_records_outside_the_callers_territory_are_refused(
    api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    r = await api.post(
        "/api/v1/operational-records",
        json=_record(station="PDY-S02", locality="elsewhere"),
        headers=as_user(officer),
    )
    assert r.status_code == 403


async def test_civic_context_is_disabled_and_action_completion_needs_an_action(
    api: httpx.AsyncClient, make_user: MakeUser
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    civic = await api.post(
        "/api/v1/operational-records",
        json=_record(kind="CIVIC_CONTEXT_REFERENCE"),
        headers=as_user(officer),
    )
    assert civic.status_code == 503
    action = await api.post(
        "/api/v1/operational-records",
        json=_record(kind="ACTION_COMPLETION"),
        headers=as_user(officer),
    )
    assert action.status_code == 422


async def test_no_route_can_create_or_change_an_official_case_record(
    api: httpx.AsyncClient,
) -> None:
    """HM-5: the only write under /incidents is the data-issue flag, which creates a DQ issue."""
    from crimemap_api.deps import route_requirements

    writes = [
        k
        for k in route_requirements(api.app)
        if k.split(" ")[0] in {"POST", "PUT", "PATCH", "DELETE"}
    ]  # type: ignore[attr-defined]
    incident_writes = [k for k in writes if k.split(" ")[1].startswith("/api/v1/incidents")]
    assert incident_writes == ["POST /api/v1/incidents/{incident_id}/data-issues"]


# --- notes and data-issue flags --------------------------------------------------------------------


async def test_analyst_notes_and_data_issue_flags(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    incident = (await features(api, analyst, "region=PDY&aggregation=POINTS"))["features"][0][
        "properties"
    ]["id"]
    r = await api.post(
        "/api/v1/notes",
        json={
            "subject_type": "INCIDENT",
            "subject_id": incident,
            "note": "<b>check</b> the time",
            "visibility": "RESTRICTED",
        },
        headers=as_user(analyst),
    )
    assert r.status_code == 201
    notes = (
        await api.get(
            f"/api/v1/notes?subject_type=INCIDENT&subject_id={incident}", headers=as_user(analyst)
        )
    ).json()["data"]
    assert [n["note"] for n in notes][
        -1
    ] == "<b>check</b> the time"  # stored as text; the UI escapes it
    other, _ = make_user(Role.CRIME_ANALYST, ("KKL",))
    assert (
        await api.get(
            f"/api/v1/notes?subject_type=INCIDENT&subject_id={incident}", headers=as_user(other)
        )
    ).status_code == 404
    rid = str(uuid.uuid4())
    flag = await api.post(
        f"/api/v1/incidents/{incident}/data-issues",
        json={"issue_type": "POSSIBLE_DUPLICATE", "note": "same as yesterday"},
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert flag.status_code == 201, flag.text
    assert [x["action"] for x in audit_rows(rid)] == ["DQ_ISSUE_FLAG"]
    again = await api.post(
        f"/api/v1/incidents/{incident}/data-issues",
        json={"issue_type": "POSSIBLE_DUPLICATE", "note": "again"},
        headers=as_user(analyst),
    )
    assert again.status_code == 409
    detected = sql(
        ingested.database,
        "SELECT detected_by, rule_code FROM core.data_quality_issue WHERE id = %s",
        flag.json()["id"],
    )
    assert detected == [("USER", "USER-POSSIBLE_DUPLICATE")]


# --- audit ---------------------------------------------------------------------------------------------


async def test_map_queries_are_audited_with_filters_and_counts(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/map/features?{PERIOD}&region=PDY&aggregation=HEX&category=PROPERTY_THEFT",
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    [event] = audit_rows(rid)
    assert event["action"] == "MAP_QUERY"
    assert event["after_meta"]["selected"] == r.json()["summary"]["selected"]


def test_location_scope_copies_always_match_their_incident(ingested: Ingested) -> None:
    """The location's RLS uses copied scope columns (ADR-032); they must never diverge (a region move
    is checked in test_phase2_audit.py::test_issues_move_with_an_incident_that_changes_region)."""
    mismatched = sql(
        ingested.database,
        "SELECT count(*) FROM core.incident_location l JOIN core.incident_record i ON i.id = l.incident_id "
        "WHERE l.region_id IS DISTINCT FROM i.region_id OR l.station_id IS DISTINCT FROM i.station_id "
        "OR l.access_classification IS DISTINCT FROM i.access_classification",
    )
    assert mismatched == [(0,)]


async def test_non_case_records_show_an_event_type_not_an_offence_section(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Found in Phase 3 browser testing: an ERSS call's event code appeared as an offence section."""
    call = sql(
        ingested.database,
        "SELECT i.id::text, i.official_offence_section FROM core.incident_record i "
        "JOIN core.organizational_unit o ON o.id = i.region_id WHERE o.code = 'PDY' AND i.record_type = 'ERSS_CALL' "
        "AND NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' LIMIT 1",
    )[0]
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    body = (await api.get(f"/api/v1/incidents/{call[0]}/summary", headers=as_user(analyst))).json()
    assert body["offence_section"] is None
    assert body["offence_name"] is None
    assert body["event_type"] == call[1]


async def test_a_stewards_review_queue_lists_only_location_corrections(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    await api.post(
        "/api/v1/operational-records",
        json=_record(kind="APPROVED_EVENT_MARKER", title="Fair"),
        headers=as_user(officer),
    )
    incident = (await features(api, officer, "region=PDY&aggregation=POINTS"))["features"][1][
        "properties"
    ]["id"]
    point = sql(
        ingested.database,
        "SELECT ST_X(ST_PointOnSurface(geometry)), ST_Y(ST_PointOnSurface(geometry)) "
        "FROM core.current_boundary WHERE code = 'PDY-S01-B2'",
    )[0]
    correction = await api.post(
        "/api/v1/operational-records",
        json=_record(
            kind="LOCATION_CORRECTION_REQUEST",
            subject_incident_id=incident,
            location={"lat": point[1], "lon": point[0]},
            locality=None,
        ),
        headers=as_user(officer),
    )
    assert correction.status_code == 201, correction.text
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    queue = (
        await api.get("/api/v1/operational-records?review_status=PENDING", headers=as_user(steward))
    ).json()["data"]
    assert queue
    assert {q["kind"] for q in queue} == {"LOCATION_CORRECTION_REQUEST"}


async def test_a_tiny_sensitive_selection_is_not_revealed_by_point_counts(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Points mode: 1-2 sensitive records must not show up as 'aggregate only: 2'."""
    row = sql(ingested.database, _TINY_SENSITIVE_BEAT)
    if not row:
        pytest.skip("no beat with 1-2 sensitive records in the test dataset")
    beat, station = row[0]
    officer, _ = make_user(Role.STATION_OFFICER, stations=(station,))
    body = await features(api, officer, f"region=PDY&aggregation=POINTS&beat={beat}")
    assert body["summary"]["selected"] == sql(ingested.database, _VISIBLE_IN_BEAT, beat)[0][0]
    assert body["summary"]["excluded"]["aggregate_only_sensitive"] == 0
    assert body["summary"]["excluded"]["suppressed_small_cells"]["label"] == SUPPRESSION_LABEL
