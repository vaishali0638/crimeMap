"""Synthetic boundaries: valid, versioned, tiling, deterministic, idempotent (ADR-005, ADR-013)."""

import psycopg
import pytest

from crimemap_api.cli import seed_boundaries
from crimemap_api.demo.geography import REGIONS, beat_count, station_seeds

from .conftest import Database

pytestmark = pytest.mark.integration


def q(database: Database, sql: str, *params: object) -> list[tuple[object, ...]]:
    with psycopg.connect(database.app) as conn:
        return conn.execute(sql, list(params)).fetchall()


def test_each_region_has_one_current_synthetic_boundary(seeded: Database) -> None:
    rows = q(
        seeded,
        "SELECT code, count(*), bool_and(approval_status = 'DEMO_ONLY'), bool_and(source LIKE 'SYNTHETIC%%') "
        "FROM core.current_boundary WHERE unit_type = 'REGION' GROUP BY code ORDER BY code",
    )
    assert rows == [(c, 1, True, True) for c in ("KKL", "MAH", "PDY", "YAN")]


def test_station_and_beat_counts_match_the_plan(seeded: Database) -> None:
    stations = dict(
        q(
            seeded,
            "SELECT p.code, count(*) FROM core.organizational_unit s "
            "JOIN core.organizational_unit p ON p.id = s.parent_id "
            "WHERE s.unit_type = 'STATION' GROUP BY p.code",
        )
    )
    assert stations == {code: r.station_count for code, r in REGIONS.items()}
    beats = dict(
        q(
            seeded,
            "SELECT s.code, count(*) FROM core.organizational_unit b "
            "JOIN core.organizational_unit s ON s.id = b.parent_id "
            "WHERE b.unit_type = 'BEAT' GROUP BY s.code",
        )
    )
    expected = {s.code: beat_count(s.code) for code in REGIONS for s in station_seeds(code)}
    assert beats == expected


def test_all_current_boundaries_are_valid_multipolygons(seeded: Database) -> None:
    rows = q(
        seeded,
        "SELECT count(*), bool_and(ST_IsValid(geometry)), bool_and(GeometryType(geometry) = 'MULTIPOLYGON'), "
        "bool_and(ST_SRID(geometry) = 4326), min(ST_Area(geometry::geography)) FROM core.current_boundary",
    )
    count, valid, multi, srid, min_area = rows[0]
    assert count == 4 + 19 + sum(beat_count(s.code) for c in REGIONS for s in station_seeds(c))
    assert (valid, multi, srid) == (True, True, True)
    assert float(min_area) > 50_000  # every unit is at least 0.05 km²


@pytest.mark.parametrize(
    ("parent_type", "child_type"), [("REGION", "STATION"), ("STATION", "BEAT")]
)
def test_children_tile_their_parent_without_gaps_or_overlaps(
    seeded: Database, parent_type: str, child_type: str
) -> None:
    rows = q(
        seeded,
        """
        WITH parents AS (SELECT org_unit_id, geometry g FROM core.current_boundary WHERE unit_type = %s),
             kids AS (SELECT o.parent_id, b.geometry g FROM core.current_boundary b
                      JOIN core.organizational_unit o ON o.id = b.org_unit_id WHERE b.unit_type = %s)
        -- Planar area in the stored CRS: topology is computed with straight edges in lon/lat, so a
        -- geodesic (::geography) area would give slivers a spurious area of ~1e-5 (edges ~0.25 m
        -- off a great circle over several km) even when the tiling is exact. Found in Phase 2.
        SELECT p.org_unit_id,
               ST_Area(ST_SymDifference(p.g, ST_Union(k.g))) / ST_Area(p.g) AS mismatch,
               (sum(ST_Area(k.g)) - ST_Area(ST_Union(k.g))) / ST_Area(p.g) AS overlap
        FROM parents p JOIN kids k ON k.parent_id = p.org_unit_id GROUP BY p.org_unit_id, p.g
        """,
        parent_type,
        child_type,
    )
    assert rows
    for _, mismatch, overlap in rows:
        assert float(mismatch) < 1e-9
        assert abs(float(overlap)) < 1e-9


def test_each_station_contains_its_seed_point(seeded: Database) -> None:
    for code in REGIONS:
        for seed in station_seeds(code):
            [(contains,)] = q(
                seeded,
                "SELECT ST_Contains(geometry, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) "
                "FROM core.current_boundary WHERE code = %s",
                seed.lon,
                seed.lat,
                seed.code,
            )
            assert contains is True


def test_demo_units_are_flagged_and_named_as_demo(seeded: Database) -> None:
    rows = q(
        seeded,
        "SELECT bool_and(is_demo), bool_and(name LIKE '%%(Demo)%%') FROM core.organizational_unit "
        "WHERE unit_type IN ('STATION', 'BEAT')",
    )
    assert rows == [(True, True)]


def test_reseeding_changes_nothing(seeded: Database) -> None:
    before = q(
        seeded,
        "SELECT count(*), md5(string_agg(ST_AsText(geometry), '' ORDER BY code)) FROM core.current_boundary",
    )
    seed_boundaries(seeded.migrator, "DEMO")
    after = q(
        seeded,
        "SELECT count(*), md5(string_agg(ST_AsText(geometry), '' ORDER BY code)) FROM core.current_boundary",
    )
    assert before == after


def test_boundaries_are_refused_in_production(database: Database) -> None:
    with pytest.raises(SystemExit, match="PRODUCTION"):
        seed_boundaries(database.migrator, "PRODUCTION")


def test_station_scoped_demo_users_get_fictional_stations(seeded: Database) -> None:
    rows = q(
        seeded,
        "SELECT u.username, (SELECT array_agg(o.code ORDER BY o.code) FROM core.organizational_unit o "
        "WHERE o.id = ANY(g.scope_station_ids)) FROM core.app_user u JOIN core.user_role_grant g ON g.user_id = u.id "
        "WHERE u.username IN ('demo.officer', 'demo.sho') AND g.revoked_at IS NULL ORDER BY 1",
    )
    assert rows == [("demo.officer", ["PDY-S01"]), ("demo.sho", ["PDY-S01"])]


# --- Proposing a new boundary version (it comes into force only after dual approval) ------------


def _current_geojson(database: Database, code: str) -> str:
    return str(
        q(
            database,
            "SELECT ST_AsGeoJSON(geometry) FROM core.current_boundary WHERE code = %s",
            code,
        )[0][0]
    )


def test_a_proposed_version_is_stored_but_not_in_force(seeded: Database, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from crimemap_api.cli import propose_boundary

    path = tmp_path / "b.geojson"
    path.write_text(_current_geojson(seeded, "KKL-S01-B1"))
    version = propose_boundary(seeded.migrator, "KKL-S01-B1", path, "DEMO")
    assert version >= 2
    rows = q(
        seeded,
        "SELECT b.version, b.approval_status FROM core.geographic_boundary b "
        "JOIN core.organizational_unit o ON o.id = b.org_unit_id WHERE o.code = %s ORDER BY 1",
        "KKL-S01-B1",
    )
    assert rows[-1] == (version, "PROPOSED")
    current = q(seeded, "SELECT version FROM core.current_boundary WHERE code = %s", "KKL-S01-B1")
    assert current == [(version - 1,)]
    with pytest.raises(SystemExit, match="already has a version awaiting approval"):
        propose_boundary(seeded.migrator, "KKL-S01-B1", path, "DEMO")


def test_a_proposal_outside_the_parent_is_refused(seeded: Database, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from crimemap_api.cli import propose_boundary

    path = tmp_path / "far.geojson"
    path.write_text(
        _current_geojson(seeded, "MAH")
    )  # Mahe's outline is not inside a Karaikal station
    with pytest.raises(SystemExit, match="outside its parent"):
        propose_boundary(seeded.migrator, "KKL-S02-B1", path, "DEMO")


def test_an_invalid_geometry_is_refused(seeded: Database, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from crimemap_api.cli import propose_boundary

    path = tmp_path / "bowtie.geojson"
    path.write_text(
        '{"type":"Polygon","coordinates":[[[79.8,10.9],[79.81,10.91],[79.81,10.9],[79.8,10.91],[79.8,10.9]]]}'
    )
    with pytest.raises(SystemExit, match="not a valid polygon"):
        propose_boundary(seeded.migrator, "KKL-S02-B2", path, "DEMO")
