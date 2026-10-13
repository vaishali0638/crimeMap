"""Administrative commands, run as the crimemap_migrator role (never by the API process).

    python -m crimemap_api.cli sync-catalogue      # roles/permissions from code into the database
    python -m crimemap_api.cli seed-boundaries      # DEMO/SANDBOX only: synthetic geography
    python -m crimemap_api.cli seed-offence-map     # DEMO/SANDBOX only: fictional code mapping
    python -m crimemap_api.cli seed-demo-identities # DEMO/SANDBOX only
    python -m crimemap_api.cli propose-boundary UNIT FILE.geojson   # new version, needs 2 approvals
    python -m crimemap_api.cli generate-demo OUT_DIR                # signed synthetic batches

The migrator URL is read from CRIMEMAP_MIGRATOR_DATABASE_URL. CRIMEMAP_ENVIRONMENT must be set
explicitly (DEMO, SANDBOX or PRODUCTION; any letter case): an unset or unknown value is refused
rather than assumed to be DEMO, so a misconfigured production host cannot run demo seeders.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from crimemap_api.demo.geography import REGIONS, beat_count, beat_seed, outline_wkt, station_seeds
from crimemap_api.demo_identities import DEMO_IDENTITIES
from crimemap_api.policy.catalogue import PERMISSIONS, ROLE_PERMISSIONS, Role

ROLE_NAMES = {
    Role.SYSTEM_ADMIN: "System administrator",
    Role.DATA_STEWARD: "Data steward / CRB analyst",
    Role.CRIME_ANALYST: "Crime analyst",
    Role.STATION_OFFICER: "Station officer",
    Role.SHO: "SHO / station supervisor",
    Role.DISTRICT_OR_REGION_SUPERVISOR: "District or region supervisor",
    Role.SENIOR_LEADERSHIP: "Senior leadership",
    Role.AUDITOR: "Auditor",
    Role.DEMO_USER: "Demo user (synthetic data only)",
}


def _libpq(url: str) -> str:
    """Accept SQLAlchemy-style URLs (postgresql+driver://) and return a libpq URL."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def sync_catalogue(url: str) -> None:
    """Make core.role / core.permission / core.role_permission equal to the code catalogue."""
    with psycopg.connect(_libpq(url)) as conn, conn.transaction():
        for role in Role:
            conn.execute(
                "INSERT INTO core.role (code, name) VALUES (%s, %s) "
                "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name",
                [role.value, ROLE_NAMES[role]],
            )
        for perm in sorted(PERMISSIONS):
            conn.execute(
                "INSERT INTO core.permission (code) VALUES (%s) ON CONFLICT (code) DO NOTHING",
                [perm],
            )
        conn.execute("DELETE FROM core.role_permission")
        for role, perms in ROLE_PERMISSIONS.items():
            for perm, grant in perms.items():
                conn.execute(
                    "INSERT INTO core.role_permission "
                    "(role_code, permission_code, scope, aggregate_only, reauth, purpose, note) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        role.value,
                        perm,
                        grant.scope.value,
                        grant.aggregate_only,
                        grant.reauth,
                        grant.purpose.value if grant.purpose else None,
                        grant.note,
                    ],
                )
        conn.execute(
            "DELETE FROM core.permission WHERE NOT (code = ANY(%s))", [sorted(PERMISSIONS)]
        )
        conn.execute("DELETE FROM core.role WHERE NOT (code = ANY(%s))", [[r.value for r in Role]])


def seed_demo_identities(url: str, environment: str) -> int:
    """Create demo users and their grants. Refuses to run in PRODUCTION. Idempotent."""
    if environment == "PRODUCTION":
        raise SystemExit("refusing to seed demo identities in PRODUCTION")
    created = 0
    with psycopg.connect(_libpq(url)) as conn, conn.transaction():
        for ident in DEMO_IDENTITIES:
            row = conn.execute(
                "INSERT INTO core.app_user (idp_subject, username, name, is_demo) "
                "VALUES (%s, %s, %s, true) ON CONFLICT (idp_subject) DO UPDATE "
                "SET username = EXCLUDED.username, name = EXCLUDED.name RETURNING id",
                [ident.idp_subject, ident.username, ident.display_name],
            ).fetchone()
            if row is None:
                raise RuntimeError("user upsert returned no row")
            user_id = row[0]
            stations = conn.execute(
                "SELECT coalesce(array_agg(id ORDER BY code), '{}') FROM core.organizational_unit "
                "WHERE unit_type = 'STATION' AND code = ANY(%s)",
                [list(ident.station_codes)],
            ).fetchone()
            station_ids = stations[0] if stations else []
            existing = conn.execute(
                "SELECT id, scope_station_ids FROM core.user_role_grant WHERE user_id = %s "
                "AND role_code = %s AND revoked_at IS NULL",
                [user_id, ident.role.value],
            ).fetchone()
            if existing:
                if station_ids and not existing[1]:
                    # Grant created before stations existed (Phase 1): add the fictional station.
                    conn.execute(
                        "UPDATE core.user_role_grant SET scope_station_ids = %s, updated_at = now() "
                        "WHERE id = %s",
                        [station_ids, existing[0]],
                    )
                continue
            conn.execute(
                "INSERT INTO core.user_role_grant "
                "(user_id, role_code, scope_region_ids, scope_station_ids, grant_reason) "
                "SELECT %s, %s, coalesce(array_agg(id), '{}'), %s, 'DEMO seed identity' "
                "FROM core.organizational_unit WHERE unit_type = 'REGION' AND code = ANY(%s)",
                [user_id, ident.role.value, station_ids, list(ident.region_codes)],
            )
            created += 1
    return created


_VORONOI_CHILDREN = """
-- Voronoi cells of the child seed points, computed in the region's projected CRS (ADR-005) and
-- clipped to the parent polygon; each cell is matched to the seed point it contains.
WITH parent AS (
    SELECT b.geometry AS g4326, ST_Transform(b.geometry, %(crs)s) AS g
    FROM core.current_boundary b WHERE b.org_unit_id = %(parent)s
),
seeds AS (
    SELECT s.code, ST_Transform(ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326), %(crs)s) AS p
    FROM unnest(%(codes)s::text[], %(lons)s::float8[], %(lats)s::float8[]) AS s(code, lon, lat)
),
cells AS (
    -- Extend cells well past the parent (1 km buffer) so that, after reprojection, they still cover
    -- the parent's outermost vertices; they are clipped back to the exact parent below.
    SELECT (ST_Dump(ST_VoronoiPolygons(ST_Collect(seeds.p), 0, ST_Buffer(parent.g, 1000)))).geom AS cell
    FROM seeds, parent GROUP BY parent.g
)
-- Clip in 4326 against the parent's stored geometry, so outer edges match the parent exactly.
SELECT seeds.code,
       ST_AsText(ST_Multi(ST_CollectionExtract(ST_MakeValid(
           ST_Intersection(ST_Transform(cells.cell, 4326), parent.g4326)), 3)))
FROM cells JOIN seeds ON ST_Contains(cells.cell, seeds.p), parent
"""

_BEAT_SEEDS = """
SELECT ST_X(ST_Transform(d.geom, 4326)), ST_Y(ST_Transform(d.geom, 4326))
FROM core.current_boundary b,
     LATERAL ST_Dump(ST_GeneratePoints(ST_Transform(b.geometry, %(crs)s), %(n)s, %(seed)s)) AS d
WHERE b.org_unit_id = %(station)s
ORDER BY d.path
"""


def _upsert_unit(
    conn: psycopg.Connection, unit_type: str, code: str, name: str, parent_id: object
) -> object:
    row = conn.execute(
        "INSERT INTO core.organizational_unit (unit_type, parent_id, name, code, is_demo) "
        "VALUES (%s, %s, %s, %s, true) ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id",
        [unit_type, parent_id, name, code],
    ).fetchone()
    if row is None:
        raise RuntimeError(f"could not upsert {code}")
    return row[0]


def _ensure_boundary(
    conn: psycopg.Connection, unit_id: object, boundary_type: str, wkt: str
) -> bool:
    """Create version 1 for a unit that has no boundary yet. Returns True if created."""
    if conn.execute(
        "SELECT 1 FROM core.geographic_boundary WHERE org_unit_id = %s", [unit_id]
    ).fetchone():
        return False
    conn.execute(
        "INSERT INTO core.geographic_boundary "
        "(org_unit_id, boundary_type, version, geometry, source, approval_status) "
        "VALUES (%s, %s, 1, ST_Multi(ST_GeomFromText(%s, 4326)), %s, 'DEMO_ONLY')",
        [
            unit_id,
            boundary_type,
            wkt,
            "SYNTHETIC - approximate outline, not an official boundary"
            if boundary_type == "REGION"
            else "SYNTHETIC - generated subdivision, not an official jurisdiction",
        ],
    )
    return True


def _children(
    conn: psycopg.Connection, crs: int, parent_id: object, seeds: list[tuple[str, float, float]]
) -> dict[str, str]:
    rows = conn.execute(
        _VORONOI_CHILDREN,
        {
            "crs": crs,
            "parent": parent_id,
            "codes": [s[0] for s in seeds],
            "lons": [s[1] for s in seeds],
            "lats": [s[2] for s in seeds],
        },
    ).fetchall()
    cells = {code: wkt for code, wkt in rows}
    if set(cells) != {s[0] for s in seeds}:
        raise RuntimeError(f"Voronoi subdivision lost seeds under {parent_id}")
    return cells


def seed_boundaries(url: str, environment: str) -> int:
    """Seed the synthetic regions, stations and beats (DEMO/SANDBOX only). Idempotent."""
    if environment == "PRODUCTION":
        raise SystemExit("refusing to seed synthetic boundaries in PRODUCTION")
    created = 0
    with psycopg.connect(_libpq(url)) as conn, conn.transaction():
        for code in REGIONS:
            region = conn.execute(
                "SELECT id, analysis_crs FROM core.organizational_unit WHERE code = %s", [code]
            ).fetchone()
            if region is None:
                raise RuntimeError(f"region {code} missing; run migrations first")
            region_id, crs = region
            created += _ensure_boundary(conn, region_id, "REGION", outline_wkt(code))
            seeds = station_seeds(code)
            station_ids = {
                s.code: _upsert_unit(conn, "STATION", s.code, s.name, region_id) for s in seeds
            }
            missing = [
                s
                for s in seeds
                if not conn.execute(
                    "SELECT 1 FROM core.geographic_boundary WHERE org_unit_id = %s",
                    [station_ids[s.code]],
                ).fetchone()
            ]
            if missing:
                cells = _children(conn, crs, region_id, [(s.code, s.lon, s.lat) for s in seeds])
                for s in missing:
                    created += _ensure_boundary(conn, station_ids[s.code], "STATION", cells[s.code])
            for s in seeds:
                n = beat_count(s.code)
                beat_codes = [f"{s.code}-B{m}" for m in range(1, n + 1)]
                beat_ids = {
                    bc: _upsert_unit(
                        conn,
                        "BEAT",
                        bc,
                        f"{s.name.removesuffix(' (Demo)')} Beat {m} (Demo)",
                        station_ids[s.code],
                    )
                    for m, bc in enumerate(beat_codes, start=1)
                }
                if all(
                    conn.execute(
                        "SELECT 1 FROM core.geographic_boundary WHERE org_unit_id = %s",
                        [beat_ids[bc]],
                    ).fetchone()
                    for bc in beat_codes
                ):
                    continue
                points = conn.execute(
                    _BEAT_SEEDS,
                    {"crs": crs, "n": n, "seed": beat_seed(s.code), "station": station_ids[s.code]},
                ).fetchall()
                cells = _children(
                    conn,
                    crs,
                    station_ids[s.code],
                    [(bc, lon, lat) for bc, (lon, lat) in zip(beat_codes, points, strict=True)],
                )
                for bc in beat_codes:
                    created += _ensure_boundary(conn, beat_ids[bc], "BEAT", cells[bc])
    return created


def seed_offence_map(url: str, environment: str) -> int:
    """Map the generator's fictional classification codes to analytical categories (demo only)."""
    if environment == "PRODUCTION":
        raise SystemExit("refusing to seed the demo offence mapping in PRODUCTION")
    from crimemap_api.demo.generator import CODES, MAPPING_VERSION

    created = 0
    with psycopg.connect(_libpq(url)) as conn, conn.transaction():
        for code, (category, _label) in CODES.items():
            row = conn.execute(
                "INSERT INTO core.offence_category_map (mapping_version, offence_code, analytical_category_id) "
                "SELECT %s, %s, id FROM core.analytical_category WHERE code = %s "
                "ON CONFLICT DO NOTHING RETURNING offence_code",
                [MAPPING_VERSION, code, category],
            ).fetchone()
            created += row is not None
    return created


def propose_boundary(url: str, unit_code: str, geojson_path: Path, environment: str) -> int:
    """Store a new boundary version as PROPOSED. It comes into force only after one SYSTEM_ADMIN and
    one DATA_STEWARD approve it (``POST /api/v1/boundaries/{id}/approvals``). Returns the version."""
    raw = json.loads(geojson_path.read_text(encoding="utf-8"))
    geometry = raw.get("geometry", raw) if raw.get("type") == "Feature" else raw
    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise SystemExit("the boundary must be a GeoJSON Polygon or MultiPolygon")
    source = (
        "SYNTHETIC - proposed demo revision"
        if environment != "PRODUCTION"
        else f"Imported from {geojson_path.name}"
    )
    with psycopg.connect(_libpq(url)) as conn, conn.transaction():
        unit = conn.execute(
            "SELECT o.id, o.unit_type, p.geometry AS parent_geom FROM core.organizational_unit o "
            "LEFT JOIN core.current_boundary p ON p.org_unit_id = o.parent_id "
            "WHERE o.code = %s AND o.unit_type IN ('REGION', 'STATION', 'BEAT')",
            [unit_code],
        ).fetchone()
        if unit is None:
            raise SystemExit(f"unknown region, station or beat {unit_code!r}")
        unit_id, unit_type, parent_geom = unit
        valid, reason = conn.execute(
            "SELECT ST_IsValid(g), ST_IsValidReason(g) FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326) g) t",
            [json.dumps(geometry)],
        ).fetchone()  # type: ignore[misc]
        if not valid:
            raise SystemExit(f"not a valid polygon: {reason}")
        if parent_geom is not None:
            outside = conn.execute(
                "SELECT ST_Area(ST_Difference(g, %s::geometry)) / nullif(ST_Area(g), 0) "
                "FROM (SELECT ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326) g) t",
                [parent_geom, json.dumps(geometry)],
            ).fetchone()[0]  # type: ignore[index]
            if outside is None or outside > 0.001:
                raise SystemExit(f"{unit_code} would lie outside its parent boundary")
        if conn.execute(
            "SELECT 1 FROM core.geographic_boundary WHERE org_unit_id = %s AND approval_status = 'PROPOSED'",
            [unit_id],
        ).fetchone():
            raise SystemExit(f"{unit_code} already has a version awaiting approval")
        version = conn.execute(
            "INSERT INTO core.geographic_boundary (org_unit_id, boundary_type, version, geometry, source, approval_status) "
            "SELECT %s, %s, coalesce(max(version), 0) + 1, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s, 'PROPOSED' "
            "FROM core.geographic_boundary WHERE org_unit_id = %s RETURNING version",
            [unit_id, unit_type, json.dumps(geometry), source, unit_id],
        ).fetchone()[0]  # type: ignore[index]
        # Same transaction as the proposal: an unaudited proposal cannot exist.
        conn.execute(
            "INSERT INTO audit.audit_event (actor_subject, action, object_type, object_id, request_id, "
            "result, after_meta, environment) VALUES ('cli:crimemap_migrator', 'BOUNDARY_PROPOSED', "
            "'geographic_boundary', %s, %s, 'ALLOW', %s::jsonb, %s)",
            [
                unit_code,
                f"cli:propose-boundary:{unit_code}:{version}",
                json.dumps({"unit": unit_code, "version": version, "file": geojson_path.name}),
                environment,
            ],
        )
    return int(version)


def generate_demo(out_dir: Path, *, records: int, seed: int, batch_size: int) -> list[Path]:
    """Generate signed synthetic batches: the main feed, plus a small legacy feed extracted ten days
    ago so the stale-source rule has something to find. Needs CRIMEMAP_DEMO_SIGNING_KEY."""
    from datetime import UTC, datetime, timedelta

    from crimemap_api.demo.generator import GeneratorConfig, generate, write_batches

    key = os.environ.get("CRIMEMAP_DEMO_SIGNING_KEY", "").encode()
    if len(key) < 32:
        raise SystemExit("CRIMEMAP_DEMO_SIGNING_KEY must be set (at least 32 characters)")
    now = datetime.now(UTC)
    main_files = write_batches(
        generate(GeneratorConfig(records=records, seed=seed)),
        out_dir / "demo_synthetic",
        key,
        batch_size=batch_size,
        extracted_at=now,
    )
    legacy = write_batches(
        generate(
            GeneratorConfig(records=200, seed=seed + 1, source_system="DEMO_SYNTHETIC_LEGACY")
        ),
        out_dir / "demo_synthetic_legacy",
        key,
        batch_size=batch_size,
        extracted_at=now - timedelta(days=10),
        source_system="DEMO_SYNTHETIC_LEGACY",
    )
    return main_files + legacy


ENVIRONMENTS = ("DEMO", "SANDBOX", "PRODUCTION")


def resolve_environment(value: str | None) -> str:
    """Normalise an environment name; refuse missing or unknown values (never default to DEMO)."""
    normalised = (value or "").strip().upper()
    if normalised not in ENVIRONMENTS:
        raise SystemExit(
            f"the environment must be set to one of {', '.join(ENVIRONMENTS)} (got {value!r})"
        )
    return normalised


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crimemap_api.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync-catalogue")
    sub.add_parser("seed-boundaries")
    sub.add_parser("seed-offence-map")
    sub.add_parser("seed-demo-identities")
    propose = sub.add_parser("propose-boundary")
    propose.add_argument("unit_code")
    propose.add_argument("geojson", type=Path)
    gen = sub.add_parser("generate-demo")
    gen.add_argument("out_dir", type=Path)
    gen.add_argument("--records", type=int, default=200_000)
    gen.add_argument("--seed", type=int, default=20261012)
    gen.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args(argv)
    environment = resolve_environment(os.environ.get("CRIMEMAP_ENVIRONMENT"))

    if args.command == "generate-demo":
        if environment == "PRODUCTION":
            raise SystemExit("refusing to generate synthetic data in PRODUCTION")
        files = generate_demo(
            args.out_dir, records=args.records, seed=args.seed, batch_size=args.batch_size
        )
        print(f"{len(files)} signed synthetic batch files written to {args.out_dir}")
        return 0

    url = os.environ.get("CRIMEMAP_MIGRATOR_DATABASE_URL")
    if not url:
        print("CRIMEMAP_MIGRATOR_DATABASE_URL is not set", file=sys.stderr)
        return 2
    if args.command == "sync-catalogue":
        sync_catalogue(url)
        print("catalogue synchronised")
    elif args.command == "seed-boundaries":
        print(
            f"synthetic boundaries seeded ({seed_boundaries(url, environment)} new boundary versions)"
        )
    elif args.command == "seed-offence-map":
        print(f"demo offence mapping seeded ({seed_offence_map(url, environment)} new codes)")
    elif args.command == "propose-boundary":
        version = propose_boundary(url, args.unit_code, args.geojson, environment)
        print(f"{args.unit_code} version {version} proposed; it needs two approvals to take effect")
    else:
        n = seed_demo_identities(url, environment)
        print(f"demo identities seeded ({n} new grants)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
