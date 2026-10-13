"""Synthetic demo geography for the four separated regions (ADR-013).

Region outlines are SIMPLIFIED, HAND-DRAWN APPROXIMATIONS of each region's general extent — not
official boundaries, not survey data, and not suitable for any operational use. Station and beat
names are fictional. Station polygons are Voronoi cells of the seed points below, clipped to the
region outline and computed in PostGIS in the region's projected CRS (ADR-005); beats subdivide each
station the same way. Everything is deterministic so tests and demos are reproducible.
"""

import itertools
import math
import random
from dataclasses import dataclass

Ring = list[tuple[float, float]]  # (lon, lat), closed (first == last)


@dataclass(frozen=True)
class Region:
    code: str
    name: str
    station_count: int
    outline: Ring


@dataclass(frozen=True)
class StationSeed:
    code: str
    name: str
    lon: float
    lat: float


REGIONS: dict[str, Region] = {
    "PDY": Region(
        "PDY",
        "Puducherry",
        10,
        [
            (79.860, 12.030),
            (79.790, 12.040),
            (79.700, 11.990),
            (79.630, 11.950),
            (79.640, 11.880),
            (79.700, 11.820),
            (79.780, 11.800),
            (79.820, 11.860),
            (79.835, 11.920),
            (79.850, 11.980),
            (79.860, 12.030),
        ],
    ),
    "KKL": Region(
        "KKL",
        "Karaikal",
        5,
        [
            (79.855, 11.010),
            (79.780, 11.000),
            (79.720, 10.960),
            (79.715, 10.880),
            (79.760, 10.830),
            (79.845, 10.840),
            (79.860, 10.920),
            (79.855, 11.010),
        ],
    ),
    "MAH": Region(
        "MAH",
        "Mahe",
        2,
        [
            (75.530, 11.712),
            (75.548, 11.716),
            (75.562, 11.706),
            (75.558, 11.692),
            (75.540, 11.688),
            (75.528, 11.696),
            (75.530, 11.712),
        ],
    ),
    "YAN": Region(
        "YAN",
        "Yanam",
        2,
        [
            (82.185, 16.748),
            (82.215, 16.752),
            (82.240, 16.742),
            (82.238, 16.722),
            (82.210, 16.715),
            (82.188, 16.725),
            (82.185, 16.748),
        ],
    ),
}


def point_in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """Ray-casting point-in-polygon test (planar; adequate for these small regions)."""
    inside = False
    for (x1, y1), (x2, y2) in itertools.pairwise(ring):
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def bbox(ring: Ring) -> tuple[float, float, float, float]:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return min(lons), min(lats), max(lons), max(lats)


def station_seeds(region_code: str) -> list[StationSeed]:
    """Deterministic, well-spread station seed points inside the region outline."""
    region = REGIONS[region_code]
    rng = random.Random(f"crimemap-demo-stations-{region_code}")  # noqa: S311  # nosec B311
    min_lon, min_lat, max_lon, max_lat = bbox(region.outline)
    diagonal = math.dist((min_lon, min_lat), (max_lon, max_lat))
    min_gap = diagonal / (2.2 * math.sqrt(region.station_count))
    points: list[tuple[float, float]] = []
    attempts = 0
    while len(points) < region.station_count:
        attempts += 1
        if attempts % 2000 == 0:
            min_gap *= 0.9  # relax if the shape is tight
        lon = rng.uniform(min_lon, max_lon)
        lat = rng.uniform(min_lat, max_lat)
        if not point_in_ring(lon, lat, region.outline):
            continue
        if all(math.dist((lon, lat), p) >= min_gap for p in points):
            points.append((lon, lat))
    # Number stations north-to-south, then west-to-east, so codes read naturally on a map.
    points.sort(key=lambda p: (-round(p[1], 3), p[0]))
    return [
        StationSeed(
            code=f"{region_code}-S{i:02d}",
            name=f"{region.name} Demo Station {i:02d} (Demo)",
            lon=round(lon, 6),
            lat=round(lat, 6),
        )
        for i, (lon, lat) in enumerate(points, start=1)
    ]


def beat_count(station_code: str) -> int:
    """2-4 beats per station, deterministic per station code."""
    return random.Random(f"crimemap-demo-beats-{station_code}").randint(2, 4)  # noqa: S311  # nosec B311


def beat_seed(station_code: str) -> int:
    """Positive integer seed for PostGIS ST_GeneratePoints (beat seed points)."""
    return sum(ord(c) * (i + 1) for i, c in enumerate(station_code)) % 2_000_000_000 + 1


def outline_wkt(region_code: str) -> str:
    ring = REGIONS[region_code].outline
    coords = ", ".join(f"{lon} {lat}" for lon, lat in ring)
    return f"MULTIPOLYGON((({coords})))"
