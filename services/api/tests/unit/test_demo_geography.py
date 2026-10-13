"""Synthetic demo geography: four separated regions with fictional stations."""

import math

import pytest

from crimemap_api.demo.geography import (
    REGIONS,
    beat_count,
    point_in_ring,
    station_seeds,
)


def ring_area_km2(ring: list[tuple[float, float]]) -> float:
    """Approximate polygon area (equirectangular) for sanity checks."""
    lat0 = math.radians(sum(lat for _, lat in ring) / len(ring))
    xs = [math.radians(lon) * math.cos(lat0) * 6371 for lon, _ in ring]
    ys = [math.radians(lat) * 6371 for _, lat in ring]
    return abs(sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(len(ring) - 1))) / 2


def test_four_regions_with_planned_station_counts() -> None:
    assert {code: r.station_count for code, r in REGIONS.items()} == {
        "PDY": 10,
        "KKL": 5,
        "MAH": 2,
        "YAN": 2,
    }


@pytest.mark.parametrize("code", ["PDY", "KKL", "MAH", "YAN"])
def test_region_outline_is_closed_and_plausibly_sized(code: str) -> None:
    ring = REGIONS[code].outline
    assert ring[0] == ring[-1]
    area = ring_area_km2(ring)
    # Real areas are roughly PDY 290, KKL 160, MAH 9, YAN 30 km²; synthetic outlines stay in range.
    expected = {"PDY": (150, 600), "KKL": (80, 350), "MAH": (4, 20), "YAN": (12, 60)}[code]
    assert expected[0] <= area <= expected[1]


def test_regions_are_geographically_separated() -> None:
    def centre(code: str) -> tuple[float, float]:
        ring = REGIONS[code].outline[:-1]
        return sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)

    assert centre("MAH")[0] < 76 < 79 < centre("PDY")[0] < 80 < centre("YAN")[0]
    assert centre("KKL")[1] < centre("PDY")[1] < centre("YAN")[1]


@pytest.mark.parametrize("code", ["PDY", "KKL", "MAH", "YAN"])
def test_station_seeds_are_inside_their_region(code: str) -> None:
    seeds = station_seeds(code)
    assert len(seeds) == REGIONS[code].station_count
    assert all(point_in_ring(s.lon, s.lat, REGIONS[code].outline) for s in seeds)


def test_station_seeds_are_deterministic() -> None:
    assert station_seeds("PDY") == station_seeds("PDY")


def test_station_codes_and_names_are_fictional_and_labelled() -> None:
    seeds = station_seeds("KKL")
    assert [s.code for s in seeds] == [f"KKL-S{n:02d}" for n in range(1, 6)]
    assert all("(Demo)" in s.name for s in seeds)


def test_station_seeds_are_spread_out() -> None:
    seeds = station_seeds("PDY")
    nearest = min(
        math.dist((a.lon, a.lat), (b.lon, b.lat))
        for i, a in enumerate(seeds)
        for b in seeds[i + 1 :]
    )
    assert nearest > 0.01  # ~1 km


def test_beat_counts_are_two_to_four_and_deterministic() -> None:
    counts = [beat_count(s.code) for s in station_seeds("PDY")]
    assert all(2 <= c <= 4 for c in counts)
    assert counts == [beat_count(s.code) for s in station_seeds("PDY")]


def test_point_in_ring() -> None:
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    assert point_in_ring(0.5, 0.5, square)
    assert not point_in_ring(1.5, 0.5, square)
