"""Phase 5 acceptance at the API (Analytics Workbench): every result carries its context, the time
basis toggle changes the applicable views, record types are never mixed by accident, exports carry
the watermark and metadata; plus territory, privacy, evidence snapshots, pattern reviews and notes."""

import csv
import io
import re
import uuid
import zlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

MakeUser = Callable[..., tuple[str, str]]
Q = "region=PDY&period_from=2026-04-01&period_to=2026-06-30"
SECTIONS = {
    "overview": "",
    "location": "&unit=HEX&hex_size_m=500",
    "case-trends": "",
    "comparison": "&compare=STATION&a=PDY-S01&b=PDY-S02",
    "data-quality": "",
}
META_KEYS = {
    "filters_applied",
    "record_count",
    "excluded_count",
    "excluded_reasons",
    "data_freshness",
    "method",
    "method_parameters",
    "caveats",
    "classification",
    "environment",
    "title",
    "explanation",
    "data_quality_note",
}


def as_user(sub: str, **kw: Any) -> dict[str, str]:
    return bearer(mint(sub, **kw))


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


async def get(api: httpx.AsyncClient, sub: str, section: str, extra: str = "") -> dict[str, Any]:
    r = await api.get(f"/api/v1/analytics/{section}?{Q}{extra}", headers=as_user(sub))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.mark.parametrize("section", list(SECTIONS))
async def test_every_result_shows_filters_counts_freshness_method_and_caveats(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested, section: str
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/analytics/{section}?{Q}{SECTIONS[section]}",
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert set(meta) >= META_KEYS
    assert meta["filters_applied"]["period"] == "2026-04-01/2026-06-30"
    assert meta["filters_applied"]["record_type"] == "CASE"
    assert meta["record_count"] > 0
    assert meta["data_freshness"]
    assert meta["method"]
    assert meta["caveats"]
    assert meta["classification"] == "RESTRICTED"
    assert [x["action"] for x in audit_rows(rid)] == ["ANALYTICS_QUERY"]


async def test_record_count_is_cases_only_unless_combined_is_chosen(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """No chart mixes incompatible record types by accident."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    window = "i.occurrence_start_at >= '2026-04-01 00:00+05:30' AND i.occurrence_start_at < '2026-07-01 00:00+05:30'"
    base = "SELECT count(*) FROM core.incident_record i JOIN core.organizational_unit r ON r.id = i.region_id WHERE r.code = 'PDY' AND i.access_classification <> 'HIGHLY_RESTRICTED' AND "
    cases = sql(ingested.database, base + window + " AND i.record_type = 'CASE'")[0][0]
    everything = sql(ingested.database, base + window)[0][0]
    assert everything > cases
    default = await get(api, analyst, "overview")
    assert default["meta"]["record_count"] == cases
    combined = await get(api, analyst, "overview", "&record_type=COMBINED")
    assert combined["meta"]["record_count"] == everything
    assert any("combined because you chose" in c for c in combined["meta"]["caveats"])
    assert not any("combined because you chose" in c for c in default["meta"]["caveats"])
    calls = await get(api, analyst, "case-trends", "&record_type=ERSS_CALL")
    assert calls["data"]["status"]["available"] is False


async def test_time_basis_toggle_changes_the_applicable_views(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    occ = await get(api, analyst, "case-trends", "&grain=day")
    rep = await get(api, analyst, "case-trends", "&grain=day&time_basis=REPORTED")
    assert occ["meta"]["filters_applied"]["time_basis"] == "occurrence"
    assert rep["meta"]["filters_applied"]["time_basis"] == "reported"
    assert occ["data"]["day_hour"]["counts"] != rep["data"]["day_hour"]["counts"]
    assert occ["data"]["series"] != rep["data"]["series"]
    window = "AND i.{col} >= '2026-04-01 00:00+05:30' AND i.{col} < '2026-07-01 00:00+05:30'"
    base = "SELECT count(*) FROM core.incident_record i JOIN core.organizational_unit r ON r.id = i.region_id WHERE r.code = 'PDY' AND i.record_type = 'CASE' AND i.access_classification <> 'HIGHLY_RESTRICTED' "
    assert (
        occ["meta"]["record_count"]
        == sql(ingested.database, base + window.format(col="occurrence_start_at"))[0][0]
    )
    assert (
        rep["meta"]["record_count"]
        == sql(ingested.database, base + window.format(col="reported_at"))[0][0]
    )
    assert any("occurrence time are not included" in c for c in occ["meta"]["caveats"])


async def test_territory_and_section_rules(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get(
        "/api/v1/analytics/overview?region=KKL", headers={**as_user(analyst), "X-Request-ID": rid}
    )
    assert r.status_code == 403
    assert [x["result"] for x in audit_rows(rid)] == ["DENY"]
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    assert (
        await api.get(f"/api/v1/analytics/data-quality?{Q}", headers=as_user(steward))
    ).status_code == 200
    assert (
        await api.get(f"/api/v1/analytics/overview?{Q}", headers=as_user(steward))
    ).status_code == 403
    viewer, _ = make_user(Role.DEMO_USER)
    assert (
        await api.get(f"/api/v1/analytics/overview?{Q}&status=CLOSED", headers=as_user(viewer))
    ).status_code == 403
    assert (
        await api.get(
            f"/api/v1/analytics/overview?{Q}&category=CRIMES_AGAINST_WOMEN", headers=as_user(viewer)
        )
    ).status_code == 403
    assert (
        await api.get(f"/api/v1/analytics/overview?{Q}&bogus=1", headers=as_user(analyst))
    ).status_code == 422
    assert (await api.get("/api/v1/analytics/civic", headers=as_user(analyst))).status_code == 503


async def test_sensitive_records_count_only_combined_for_aggregate_roles(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    viewer, _ = make_user(Role.DEMO_USER)
    body = await get(api, viewer, "overview")
    codes = {c["code"] for c in body["data"]["categories"]}
    assert not codes & {"CRIMES_AGAINST_WOMEN", "CRIMES_AGAINST_CHILDREN"}
    combined = next((c["count"] for c in body["data"]["categories"] if c["code"] == "SENSITIVE"), 0)
    assert combined == 0 or combined >= 3
    assert body["meta"]["record_count"] == sum(c["count"] for c in body["data"]["categories"])
    assert body["meta"]["suppressed"]
    locations = await get(api, viewer, "location", "&unit=HEX&hex_size_m=250")
    assert all(f["properties"]["count"] >= 1 for f in locations["data"]["map"]["features"])


async def test_location_trends_state_unit_method_and_unavailable_views(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    body = await get(api, analyst, "location", "&unit=HEX&hex_size_m=500")
    p = body["meta"]["method_parameters"]
    assert p["spatial_unit"] == "HEX"
    assert p["cell_size_m"] == 500
    assert p["crs"].startswith("EPSG:")
    assert body["data"]["road_segments"]["available"] is False
    broad = await get(
        api,
        analyst,
        "location",
        "&unit=HEX&record_type=COMBINED&period_from=2024-10-01&period_to=2026-09-30",
    )
    assert broad["meta"]["record_count"] >= 200
    assert broad["data"]["kde"]["available"] is True, broad["data"]["kde"]["reason"]
    assert broad["data"]["kde"]["map"]["features"]
    assert body["data"]["persistence"]
    assert {x["status"] for x in body["data"]["persistence"]} <= {
        "IN_BOTH",
        "CURRENT_ONLY",
        "COMPARISON_ONLY",
    }
    assert all(r["weeks"] >= 3 for r in body["data"]["repeat_locations"])
    total = sum(f["properties"]["count"] for f in body["data"]["map"]["features"])
    assert (
        total + body["meta"]["excluded_reasons"].get("no_usable_location", 0)
        == body["meta"]["record_count"]
    )
    tiny = await get(
        api,
        analyst,
        "location",
        "&unit=HEX&station=PDY-S01&category=NARCOTICS&period_from=2026-06-01",
    )
    assert tiny["data"]["kde"]["available"] is False
    assert "Density needs" in tiny["data"]["kde"]["reason"]
    beats = await get(api, analyst, "location", "&unit=BEAT")
    assert all(f["properties"]["kind"] == "beat" for f in beats["data"]["map"]["features"])


async def test_comparison_kinds(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY", "KKL"))
    for extra in (
        "&compare=REGION&a=PDY&b=KKL",
        "&compare=PERIOD",
        "&compare=YEAR",
        "&compare=CATEGORY&a=PROPERTY_THEFT&b=VEHICLE_THEFT",
        "&compare=BEAT&a=PDY-S01-B1&b=PDY-S01-B2",
    ):
        body = await get(api, analyst, "comparison", extra)
        d = body["data"]
        assert d["difference"]["difference"] == d["a"]["count"] - d["b"]["count"]
        assert d["rate"]["available"] is False
        assert body["meta"]["filters_applied"]["compare"]["kind"]
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    r = await api.get(
        f"/api/v1/analytics/comparison?{Q}&compare=STATION&a=PDY-S01&b=PDY-S02",
        headers=as_user(officer),
    )
    assert r.status_code == 403  # officers have no analytics.compare


async def test_exports_need_permission_and_a_fresh_sign_in_and_carry_watermark_and_metadata(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, analyst_id = make_user(Role.CRIME_ANALYST, ("PDY",))
    body = {
        "format": "CSV",
        "section": "overview",
        "purpose": "BRIEFING",
        "params": {"region": "PDY", "period_from": "2026-04-01", "period_to": "2026-06-30"},
    }
    no_purpose = {k: v for k, v in body.items() if k != "purpose"}
    assert (
        await api.post("/api/v1/reports", json=no_purpose, headers=as_user(analyst))
    ).status_code == 422
    stale = await api.post(
        "/api/v1/reports", json=body, headers=as_user(analyst, auth_age=timedelta(minutes=30))
    )
    assert stale.status_code == 403
    assert stale.json()["type"].endswith("reauthentication-required")
    leader, _ = make_user(Role.SENIOR_LEADERSHIP)
    assert (
        await api.post("/api/v1/reports", json=body, headers=as_user(leader))
    ).status_code == 403
    rid = str(uuid.uuid4())
    created = await api.post(
        "/api/v1/reports", json=body, headers={**as_user(analyst), "X-Request-ID": rid}
    )
    assert created.status_code == 201, created.text
    assert [x["action"] for x in audit_rows(rid)] == [
        "EXPORT_REQUEST",
        "EVIDENCE_SNAPSHOT_CREATE",
        "EXPORT_GENERATED",
    ] or [x["action"] for x in audit_rows(rid)] == ["EXPORT_REQUEST", "EXPORT_GENERATED"]
    report = created.json()
    rid = str(uuid.uuid4())
    download = await api.get(
        f"/api/v1/reports/{report['id']}/download",
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert "attachment" in download.headers["content-disposition"]
    assert [x["action"] for x in audit_rows(rid)] == ["EXPORT_DOWNLOAD"]
    text = download.content.decode("utf-8-sig")
    for needle in (
        "INTERNAL / RESTRICTED",
        "Requested by",
        "Generated (IST)",
        "Active filters",
        "Classification",
        "Source and refresh",
        "Data quality",
        "Method",
        "Evidence snapshot",
        "expires 24 hours",
    ):
        assert needle in text
    rows = list(csv.reader(io.StringIO(text)))
    assert all(not cell.startswith(("=", "+", "@")) for row in rows for cell in row)
    other, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    assert (
        await api.get(f"/api/v1/reports/{report['id']}/download", headers=as_user(other))
    ).status_code == 404
    expires = sql(
        ingested.database,
        "SELECT expires_at - created_at FROM core.report_export WHERE id = %s",
        report["id"],
    )[0][0]
    assert expires == timedelta(hours=24)
    pdf = await api.post(
        "/api/v1/reports",
        json={**body, "format": "PDF", "section": "case_trends"},
        headers=as_user(analyst),
    )
    assert pdf.status_code == 201, pdf.text
    content = (
        await api.get(f"/api/v1/reports/{pdf.json()['id']}/download", headers=as_user(analyst))
    ).content
    assert content.startswith(b"%PDF-1.4")
    assert content.rstrip().endswith(b"%%EOF")
    streams = [zlib.decompress(m) for m in re.findall(rb"stream\n(.*?)\nendstream", content, re.S)]
    page_text = b"".join(streams).decode("latin-1")
    assert page_text.count("INTERNAL / RESTRICTED") >= 2 * len(
        streams
    )  # watermark + header on every page
    assert "Requested by" in page_text
    listed = (await api.get("/api/v1/reports", headers=as_user(analyst))).json()["data"]
    assert {r["id"] for r in listed} >= {report["id"], pdf.json()["id"]}
    del analyst_id


async def test_pattern_review_needs_evidence_and_describes_places_not_people(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    params = {
        "region": "PDY",
        "period_from": "2026-04-01",
        "period_to": "2026-06-30",
        "unit": "HEX",
        "hex_size_m": "500",
    }
    snap = await api.post(
        "/api/v1/evidence-snapshots",
        json={"section": "location", "params": params},
        headers=as_user(analyst),
    )
    assert snap.status_code == 201, snap.text
    snapshot_id = snap.json()["id"]
    stored = (
        await api.get(f"/api/v1/evidence-snapshots/{snapshot_id}", headers=as_user(analyst))
    ).json()
    assert re.fullmatch(r"[0-9a-f]{64}", stored["content_sha256"])
    cells = sorted(stored["data"]["map"]["features"], key=lambda f: -f["properties"]["count"])[:2]
    keys = [c["properties"]["key"] for c in cells]
    review = {
        "review_type": "HOTSPOT",
        "evidence_snapshot_id": snapshot_id,
        "cell_keys": keys,
        "narrative": "Two adjacent cells hold the highest recorded property counts this quarter.",
        "recommended_next_step": "Joint inspection review of lighting and patrol timing.",
    }
    for bad in (
        "The suspect is known to frequent the market.",
        "Call 98765 43210 for details about the area.",
    ):
        assert (
            await api.post(
                "/api/v1/pattern-reviews",
                json={**review, "narrative": bad},
                headers=as_user(analyst),
            )
        ).status_code == 422
    rid = str(uuid.uuid4())
    created = await api.post(
        "/api/v1/pattern-reviews", json=review, headers={**as_user(analyst), "X-Request-ID": rid}
    )
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["record_count"] == sum(c["properties"]["count"] for c in cells)
    assert out["baseline_count"] == sum(c["properties"]["comparison_count"] for c in cells)
    assert out["analyst_status"] == "DRAFT"
    assert out["baseline_start"] == "2025-12-31"  # same length (91 days) immediately before
    assert out["quality_summary"]["data_quality_note"]
    assert [x["action"] for x in audit_rows(rid)] == ["PATTERN_REVIEW_CREATE"]
    assert (
        await api.post(
            "/api/v1/pattern-reviews",
            json={**review, "cell_keys": ["PDY:500:1:1"]},
            headers=as_user(analyst),
        )
    ).status_code == 422
    overview = await api.post(
        "/api/v1/evidence-snapshots",
        json={"section": "overview", "params": params | {"unit": "HEX"}},
        headers=as_user(analyst),
    )
    assert (
        await api.post(
            "/api/v1/pattern-reviews",
            json={**review, "evidence_snapshot_id": overview.json()["id"]},
            headers=as_user(analyst),
        )
    ).status_code == 422
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    assert (
        await api.post("/api/v1/pattern-reviews", json=review, headers=as_user(officer))
    ).status_code == 403
    geometry = sql(
        ingested.database,
        "SELECT GeometryType(area_geometry) FROM core.pattern_review WHERE id = %s",
        out["id"],
    )
    assert geometry == [("MULTIPOLYGON",)]


async def test_analysis_notes_attach_to_evidence(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    snap = await api.post(
        "/api/v1/evidence-snapshots",
        json={
            "section": "case_trends",
            "params": {"region": "PDY", "period_from": "2026-04-01", "period_to": "2026-06-30"},
        },
        headers=as_user(analyst),
    )
    note = {
        "subject_type": "EVIDENCE_SNAPSHOT",
        "subject_id": snap.json()["id"],
        "note": "Evening peak persists.",
        "visibility": "RESTRICTED",
    }
    assert (await api.post("/api/v1/notes", json=note, headers=as_user(analyst))).status_code == 201
    outsider, _ = make_user(Role.CRIME_ANALYST, ("KKL",))
    assert (
        await api.post("/api/v1/notes", json=note, headers=as_user(outsider))
    ).status_code == 404


async def test_daily_buckets_count_small_sensitive_groups_nowhere(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """ADR-012 per bucket: a day with 1-2 sensitive records counts only its other records."""
    rows = sql(
        ingested.database,
        "SELECT (i.occurrence_start_at AT TIME ZONE 'Asia/Kolkata')::date AS d, "
        "count(*) FILTER (WHERE NOT i.is_sensitive_category), count(*) FILTER (WHERE i.is_sensitive_category) "
        "FROM core.incident_record i JOIN core.organizational_unit r ON r.id = i.region_id "
        "WHERE r.code = 'PDY' AND i.record_type = 'CASE' AND i.access_classification <> 'HIGHLY_RESTRICTED' "
        "AND i.occurrence_start_at >= '2026-04-01 00:00+05:30' AND i.occurrence_start_at < '2026-07-01 00:00+05:30' GROUP BY 1",
    )
    if not any(1 <= m <= 2 for _, _, m in rows):
        pytest.skip("no day with 1-2 sensitive cases in the test data")
    # Combined sensitive records are in no daily bucket at all (only in the headline total).
    expected = {d.isoformat(): u for d, u, _m in rows}
    viewer, _ = make_user(Role.DEMO_USER)
    body = await get(api, viewer, "case-trends", "&grain=day")
    got = {p["bucket"]: p["count"] for p in body["data"]["series"]}
    assert {k: v for k, v in got.items() if v} == {k: v for k, v in expected.items() if v}


def test_the_worker_deletes_expired_exports_only(make_user: MakeUser, ingested: Ingested) -> None:
    """The stated download-expiry policy: the worker's hourly job removes expired exports."""
    from crimemap_worker.exports import PURGE_EXPORTS

    _, user = make_user(Role.CRIME_ANALYST, ("PDY",))
    with psycopg.connect(ingested.database.migrator) as conn:
        snapshot = conn.execute(
            "INSERT INTO core.evidence_snapshot (section, params, data, meta, content_sha256, classification, region_id, region_ids, created_by) "
            "SELECT 'overview', '{}', '{}', '{}', repeat('a', 64), 'RESTRICTED', id, ARRAY[id], %s FROM core.organizational_unit WHERE code = 'PDY' RETURNING id",
            [user],
        ).fetchone()[0]  # type: ignore[index]
        ids = []
        for created, expires in (
            ("now() - interval '25 hours'", "now() - interval '1 hour'"),
            ("now()", "now() + interval '24 hours'"),
        ):
            ids.append(
                conn.execute(
                    "INSERT INTO core.report_export (format, section, filename, content, content_sha256, classification, metadata, "
                    "evidence_snapshot_id, region_id, created_by, created_at, expires_at) "
                    f"SELECT 'CSV', 'overview', 'x.csv', 'x'::bytea, repeat('b', 64), 'RESTRICTED', '{{}}', %s, id, %s, {created}, {expires} "
                    "FROM core.organizational_unit WHERE code = 'PDY' RETURNING id",
                    [snapshot, user],
                ).fetchone()[0]  # type: ignore[index]
            )
    with psycopg.connect(ingested.database.worker) as conn:
        deleted = conn.execute(str(PURGE_EXPORTS)).fetchone()[0]  # type: ignore[index]
    assert deleted >= 1
    left = {
        str(r[0])
        for r in sql(ingested.database, "SELECT id FROM core.report_export WHERE id = ANY(%s)", ids)
    }
    assert left == {str(ids[1])}


# --- Phase 5 audit regressions ---------------------------------------------------------------------------------

LOCATION = {
    "region": "PDY",
    "period_from": "2026-04-01",
    "period_to": "2026-06-30",
    "unit": "HEX",
    "hex_size_m": "500",
}


async def test_totals_minus_buckets_reveal_nothing_but_the_combined_figure(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Audit 1: record_count minus the bucket sums used to expose 1-2 hidden sensitive records."""
    viewer, _ = make_user(Role.DEMO_USER)
    for section, extra, key in (
        ("case-trends", "&grain=week", "series"),
        ("case-trends", "&grain=day", "calendar"),
    ):
        body = await get(api, viewer, section, extra)
        combined = next(
            (c["count"] for c in body["data"].get("categories", []) if c["code"] == "SENSITIVE"),
            None,
        )
        buckets = sum(p["count"] for p in body["data"][key])
        gap = body["meta"]["record_count"] - buckets
        assert gap == 0 or gap >= 3, (section, extra, gap)
        assert combined is None
    over = await get(api, viewer, "overview")
    combined = next((c["count"] for c in over["data"]["categories"] if c["code"] == "SENSITIVE"), 0)
    assert (
        over["meta"]["record_count"] - sum(p["count"] for p in over["data"]["trend"]["points"])
        == combined
    )
    loc = await get(api, viewer, "location", "&unit=HEX&hex_size_m=250")
    cells = sum(f["properties"]["count"] for f in loc["data"]["map"]["features"])
    gap = (
        loc["meta"]["record_count"]
        - cells
        - loc["meta"]["excluded_reasons"].get("no_usable_location", 0)
    )
    assert gap == 0 or gap >= 3


async def test_snapshots_are_readable_only_with_the_access_that_produced_them(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Audit 2 and 3: section, compare permission, sensitive access and every covered unit."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY", "KKL"))
    overview = await api.post(
        "/api/v1/evidence-snapshots",
        json={"section": "overview", "params": {"region": "PDY"}},
        headers=as_user(analyst),
    )
    steward, _ = make_user(Role.DATA_STEWARD, ("PDY",))
    assert (
        await api.get(
            f"/api/v1/evidence-snapshots/{overview.json()['id']}", headers=as_user(steward)
        )
    ).status_code == 404
    note = {
        "subject_type": "EVIDENCE_SNAPSHOT",
        "subject_id": overview.json()["id"],
        "note": "check",
        "visibility": "RESTRICTED",
    }
    assert (await api.post("/api/v1/notes", json=note, headers=as_user(steward))).status_code == 404
    colleague, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    assert (
        await api.get(
            f"/api/v1/evidence-snapshots/{overview.json()['id']}", headers=as_user(colleague)
        )
    ).status_code == 200
    comparison = await api.post(
        "/api/v1/evidence-snapshots",
        json={
            "section": "comparison",
            "params": {"region": "PDY", "compare": "REGION", "a": "PDY", "b": "KKL"},
        },
        headers=as_user(analyst),
    )
    assert comparison.status_code == 201, comparison.text
    assert (
        await api.get(
            f"/api/v1/evidence-snapshots/{comparison.json()['id']}", headers=as_user(colleague)
        )
    ).status_code == 404
    both, _ = make_user(Role.CRIME_ANALYST, ("PDY", "KKL"))
    assert (
        await api.get(
            f"/api/v1/evidence-snapshots/{comparison.json()['id']}", headers=as_user(both)
        )
    ).status_code == 200


async def test_station_scoped_snapshots_and_exports_work(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Audit 4 and 5: an SHO's region-wide snapshot (was 500) and station export (was 403)."""
    sho, _ = make_user(Role.SHO, stations=("PDY-S01",))
    snap = await api.post(
        "/api/v1/evidence-snapshots",
        json={"section": "overview", "params": {"region": "PDY"}},
        headers=as_user(sho),
    )
    assert snap.status_code == 201, snap.text
    body = {
        "format": "CSV",
        "section": "overview",
        "purpose": "BRIEFING",
        "params": {"region": "PDY", "station": ["PDY-S01"]},
    }
    exported = await api.post("/api/v1/reports", json=body, headers=as_user(sho))
    assert exported.status_code == 201, exported.text
    other = {**body, "params": {"region": "PDY", "station": ["PDY-S02"]}}
    assert (await api.post("/api/v1/reports", json=other, headers=as_user(sho))).status_code == 403


async def test_pattern_review_cells_must_be_distinct_and_comparisons_must_differ(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """Audit 6 and 12."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    snap = await api.post(
        "/api/v1/evidence-snapshots",
        json={"section": "location", "params": LOCATION},
        headers=as_user(analyst),
    )
    key = (
        snap.json()["meta"]["filters_applied"]
        and (
            await api.get(
                f"/api/v1/evidence-snapshots/{snap.json()['id']}", headers=as_user(analyst)
            )
        ).json()["data"]["map"]["features"][0]["properties"]["key"]
    )
    review = {
        "review_type": "HOTSPOT",
        "evidence_snapshot_id": snap.json()["id"],
        "cell_keys": [key, key],
        "narrative": "The same cell listed twice must be refused by the API.",
        "recommended_next_step": "Review it.",
    }
    assert (
        await api.post("/api/v1/pattern-reviews", json=review, headers=as_user(analyst))
    ).status_code == 422
    r = await api.get(
        f"/api/v1/analytics/comparison?{Q}&compare=REGION&a=PDY&b=PDY", headers=as_user(analyst)
    )
    assert r.status_code == 422
