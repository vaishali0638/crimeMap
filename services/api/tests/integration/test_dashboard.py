"""Phase 4 acceptance at the API (PY Dashboard): region filtering is consistent across the station
list and the boards (PD-1), station details match the map (PD-3), every board carries its context
(PD-4), the rate board fails safely without an approved denominator (PD-5), neutral wording (PD-6),
plus territory, sensitive-category and audit rules shared with the Home Map."""

import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from crimemap_api.policy.catalogue import Role
from crimemap_api.routes.dashboard import BOARD_TITLES, RATE_UNAVAILABLE
from crimemap_api.routes.map import SUPPRESSION_LABEL

from ..support import bearer, mint
from .conftest import Database, Ingested

pytestmark = pytest.mark.integration

MakeUser = Callable[..., tuple[str, str]]
PERIOD = "period_from=2026-01-01&period_to=2026-06-30"
BOARDS = list(BOARD_TITLES)
PROHIBITED = re.compile(r"\b(dangerous|worst|criminal|high-crime)\b", re.IGNORECASE)


def as_user(sub: str) -> dict[str, str]:
    return bearer(mint(sub))


def sql(database: Database, query: str, *params: object) -> list[tuple[Any, ...]]:
    with psycopg.connect(database.migrator) as conn:
        return conn.execute(query, list(params)).fetchall()


async def board(api: httpx.AsyncClient, sub: str, name: str, query: str = "") -> dict[str, Any]:
    r = await api.get(f"/api/v1/dashboard/trends/{name}?{PERIOD}&{query}", headers=as_user(sub))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_station_list_offers_only_the_callers_territory(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    body = (await api.get("/api/v1/dashboard/stations", headers=as_user(officer))).json()
    assert [r["code"] for r in body["regions"]] == ["PDY"]
    assert [s["code"] for s in body["stations"]] == ["PDY-S01"]
    assert body["data_label"] == "DEMO / SYNTHETIC DATA"
    leader, _ = make_user(Role.SENIOR_LEADERSHIP)
    body = (await api.get("/api/v1/dashboard/stations", headers=as_user(leader))).json()
    assert [r["code"] for r in body["regions"]] == ["KKL", "MAH", "PDY", "YAN"]
    assert len(body["stations"]) == 19
    assert not {"CRIMES_AGAINST_WOMEN", "CRIMES_AGAINST_CHILDREN"} & {
        c["code"] for c in body["categories"]
    }
    auditor, _ = make_user(Role.AUDITOR)
    assert (
        await api.get("/api/v1/dashboard/stations", headers=as_user(auditor))
    ).status_code == 403


async def test_region_filter_is_consistent_across_boards(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """PD-1: one region's station, region and time boards add up to the same selection."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY", "KKL"))
    stations = await board(api, analyst, "station-rate", "region=KKL")
    regions = await board(api, analyst, "region", "region=KKL")
    hours = await board(api, analyst, "time", "region=KKL")
    total = regions["rows"][0]["count"]
    assert total > 0
    assert [r["key"] for r in regions["rows"]] == ["KKL"]
    assert total == sum(r["count"] for r in hours["rows"])
    unassigned = sql(
        ingested.database,
        "SELECT count(*) FROM core.incident_record i JOIN core.organizational_unit r ON r.id = i.region_id "
        "WHERE r.code = 'KKL' AND i.station_id IS NULL AND i.access_classification <> 'HIGHLY_RESTRICTED' "
        "AND i.occurrence_start_at >= '2026-01-01 00:00+05:30' AND i.occurrence_start_at < '2026-07-01 00:00+05:30'",
    )[0][0]
    assert sum(r["count"] for r in stations["rows"]) == total - unassigned
    assert {r["key"].split("-")[0] for r in stations["rows"]} == {"KKL"}
    listed = (await api.get("/api/v1/dashboard/stations", headers=as_user(analyst))).json()
    kkl = {s["code"] for s in listed["stations"] if s["region_code"] == "KKL"}
    assert {r["key"] for r in stations["rows"]} == kkl  # every station, including zero rows
    both = await board(api, analyst, "region", "region=ALL")
    assert {r["key"] for r in both["rows"]} == {"PDY", "KKL"}


async def test_board_counts_match_the_map_selection(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """The dashboard reads the same selection as the Home Map (ADR-032)."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    region = await board(api, analyst, "region", "region=PDY&category=PROPERTY_THEFT")
    r = await api.get(
        f"/api/v1/map/features?{PERIOD}&region=PDY&aggregation=BEAT&category=PROPERTY_THEFT",
        headers=as_user(analyst),
    )
    assert region["rows"][0]["count"] == r.json()["summary"]["selected"]


@pytest.mark.parametrize("name", BOARDS)
async def test_every_board_shows_its_context(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested, name: str
) -> None:
    """PD-4: period, category, time basis, scope, definition, quality caveat, refresh, notice."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    body = await board(api, analyst, name, "region=PDY")
    assert body["title"] == BOARD_TITLES[name]
    assert body["period"] == {
        "period_from": "2026-01-01",
        "period_to": "2026-06-30",
        "basis": "OCCURRENCE",
    }
    assert body["category"] == "All categories"
    assert body["scope"]["label"]
    assert body["measure"]
    assert body["comparison"]
    assert body["quality"]["caveat"]
    assert body["quality"]["usable_location_pct"] is not None
    assert body["last_refreshed_at"]
    assert "audit" in body["notice"]
    assert "Export" in body["notice"]
    assert body["data_label"] == "DEMO / SYNTHETIC DATA"
    assert body["rows"]
    ranks = [r["rank"] for r in body["rows"]]
    assert sorted(ranks) == list(range(1, len(ranks) + 1))
    assert not PROHIBITED.search(str(body))  # PD-6


async def test_rate_board_fails_safely_without_a_denominator(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """PD-5 / ADR-014: no approved denominator (an unapproved one does not count)."""
    assert sql(
        ingested.database,
        "SELECT count(*) FROM core.denominator WHERE approval_status = 'APPROVED'",
    ) == [(0,)]
    with psycopg.connect(ingested.database.migrator) as conn:
        conn.execute(
            "INSERT INTO core.denominator (org_unit_id, measure, value, period_start, period_end, source, approval_status) "
            "SELECT id, 'POPULATION', 50000, '2026-01-01', '2026-12-31', 'TEST ONLY - proposed', 'PROPOSED' "
            "FROM core.organizational_unit WHERE code LIKE 'PDY-S%%'"
        )
    try:
        analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
        body = await board(api, analyst, "station-rate", "region=PDY")
    finally:
        with psycopg.connect(ingested.database.migrator) as conn:
            conn.execute("DELETE FROM core.denominator WHERE source = 'TEST ONLY - proposed'")
    assert body["rate"]["available"] is False
    assert RATE_UNAVAILABLE in body["rate"]["label"]
    assert all(r["rate"] is None and r["rate_label"] == RATE_UNAVAILABLE for r in body["rows"])
    counts = [r["count"] for r in body["rows"]]
    assert counts == sorted(counts, reverse=True)  # ranked by count instead


async def test_rate_board_uses_an_approved_denominator_when_one_exists(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """The rate code path, exercised with a clearly labelled synthetic test denominator (ADR-014)."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("YAN",))
    stations = [
        r[0]
        for r in sql(
            ingested.database, "SELECT id FROM core.organizational_unit WHERE code LIKE 'YAN-S%%'"
        )
    ]
    with psycopg.connect(ingested.database.migrator) as conn:
        for sid in stations:
            conn.execute(
                "INSERT INTO core.denominator (org_unit_id, measure, value, period_start, period_end, source, approval_status) "
                "VALUES (%s, 'POPULATION', 50000, '2026-01-01', '2026-12-31', 'TEST ONLY - synthetic', 'APPROVED')",
                [sid],
            )
    try:
        body = await board(api, analyst, "station-rate", "region=YAN")
        assert body["rate"]["available"] is True
        for r in body["rows"]:
            assert r["rate"] == round(r["count"] / 50000 * 100_000, 1)
    finally:
        with psycopg.connect(ingested.database.migrator) as conn:
            conn.execute("DELETE FROM core.denominator WHERE source = 'TEST ONLY - synthetic'")


async def test_station_details_match_the_map_and_respect_case_access(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    """PD-3: counts match the API's map selection; case status counts only with summary access."""
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/dashboard/stations/PDY-S01?{PERIOD}",
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert r.status_code == 200, r.text
    details = r.json()
    features = await api.get(
        f"/api/v1/map/features?{PERIOD}&region=PDY&station=PDY-S01&aggregation=BEAT",
        headers=as_user(analyst),
    )
    assert details["selected"] == features.json()["summary"]["selected"]
    assert details["selected"] == sum(c["count"] for c in details["categories"])
    assert details["cases"] is not None
    assert details["jurisdiction"]["beats"] >= 1
    assert "Synthetic" in details["jurisdiction"]["label"]
    assert [x["action"] for x in audit_rows(rid)] == ["ANALYTICS_QUERY"]
    leader, _ = make_user(Role.SENIOR_LEADERSHIP)
    aggregate_only = (
        await api.get(f"/api/v1/dashboard/stations/PDY-S01?{PERIOD}", headers=as_user(leader))
    ).json()
    assert aggregate_only["cases"] is None
    assert aggregate_only["sensitive"]["count"] is None or aggregate_only["sensitive"]["count"] >= 3


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dashboard/stations/PDY-S02",
        "/api/v1/dashboard/trends/region?region=KKL",
        "/api/v1/dashboard/trends/time?station=PDY-S02",
        "/api/v1/dashboard/stations/PDY-S99",
    ],
)
async def test_out_of_territory_requests_are_refused_and_audited(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested, path: str
) -> None:
    officer, _ = make_user(Role.STATION_OFFICER, stations=("PDY-S01",))
    rid = str(uuid.uuid4())
    sep = "&" if "?" in path else "?"
    r = await api.get(f"{path}{sep}{PERIOD}", headers={**as_user(officer), "X-Request-ID": rid})
    assert r.status_code == 403
    assert [x["result"] for x in audit_rows(rid)] == ["DENY"]


async def test_combined_callers_cannot_rank_one_sensitive_category(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    leader, _ = make_user(Role.SENIOR_LEADERSHIP)
    r = await api.get(
        f"/api/v1/dashboard/trends/location?{PERIOD}&category=CRIMES_AGAINST_WOMEN",
        headers=as_user(leader),
    )
    assert r.status_code == 403


async def test_small_sensitive_groups_are_not_counted_in_board_rows(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """ADR-012 per row: a station with 1-2 sensitive records counts only its other records."""
    row = sql(
        ingested.database,
        "SELECT s.code FROM core.incident_record i JOIN core.organizational_unit s ON s.id = i.station_id "
        "WHERE i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
        "AND i.occurrence_start_at >= '2026-01-01 00:00+05:30' AND i.occurrence_start_at < '2026-07-01 00:00+05:30' "
        "GROUP BY 1 HAVING count(*) BETWEEN 1 AND 2 LIMIT 1",
    )
    if not row:
        pytest.skip("no station with 1-2 sensitive records in the period")
    station = row[0][0]
    expected = sql(
        ingested.database,
        "SELECT count(*) FROM core.incident_record i JOIN core.organizational_unit s ON s.id = i.station_id "
        "WHERE s.code = %s AND NOT i.is_sensitive_category AND i.access_classification <> 'HIGHLY_RESTRICTED' "
        "AND i.occurrence_start_at >= '2026-01-01 00:00+05:30' AND i.occurrence_start_at < '2026-07-01 00:00+05:30'",
        station,
    )[0][0]
    leader, _ = make_user(Role.SENIOR_LEADERSHIP)
    body = await board(api, leader, "station-rate", f"region={station.split('-')[0]}")
    assert next(r["count"] for r in body["rows"] if r["key"] == station) == expected
    details = (
        await api.get(f"/api/v1/dashboard/stations/{station}?{PERIOD}", headers=as_user(leader))
    ).json()
    assert details["selected"] == expected
    assert details["sensitive"] == {"count": None, "label": SUPPRESSION_LABEL}


async def test_trend_boards_are_audited_with_filters(
    api: httpx.AsyncClient, make_user: MakeUser, audit_rows: Any, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    rid = str(uuid.uuid4())
    r = await api.get(
        f"/api/v1/dashboard/trends/top-spot?{PERIOD}&region=PDY&category=PROPERTY_THEFT",
        headers={**as_user(analyst), "X-Request-ID": rid},
    )
    assert r.status_code == 200
    [event] = audit_rows(rid)
    assert event["action"] == "ANALYTICS_QUERY"
    assert event["object_type"] == "trend_board"
    body = r.json()
    assert len(body["rows"]) <= 20
    assert body["total_rows"] >= len(body["rows"])
    first = body["rows"][0]
    assert first["view_on_map"]["kind"] == "cell"
    assert first["label"].startswith("Cell near ")


async def test_month_and_week_boards_are_chronological_with_partial_buckets(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    analyst, _ = make_user(Role.CRIME_ANALYST, ("PDY",))
    months = await board(api, analyst, "month", "region=PDY")
    assert [r["key"] for r in months["rows"]] == [f"2026-0{m}-01" for m in range(1, 7)]
    assert not any(r["partial"] for r in months["rows"])
    assert all(r["previous"] is not None for r in months["rows"])
    weeks = await board(api, analyst, "week", "region=PDY")
    assert weeks["rows"][0]["partial"]  # 2026-01-01 is a Thursday
    assert sorted(r["rank"] for r in weeks["rows"]) == list(range(1, len(weeks["rows"]) + 1))
    region = await board(api, analyst, "region", "region=PDY")
    assert sum(r["count"] for r in months["rows"]) == region["rows"][0]["count"]


def test_dashboard_source_uses_neutral_wording() -> None:
    """PD-6 for every string the dashboard can send (the web catalogue is scanned separately)."""
    from crimemap_api.routes import dashboard

    assert not PROHIBITED.search(Path(dashboard.__file__).read_text(encoding="utf-8"))


async def test_wording_is_neutral_in_every_board_and_label(
    api: httpx.AsyncClient, make_user: MakeUser, ingested: Ingested
) -> None:
    """PD-6 at the API."""
    leader, _ = make_user(Role.SENIOR_LEADERSHIP)
    for name in BOARDS:
        assert not PROHIBITED.search(str(await board(api, leader, name)))
