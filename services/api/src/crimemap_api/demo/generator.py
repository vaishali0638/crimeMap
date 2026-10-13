"""Synthetic incident generator (docs/demo-data.md). DEMO / SYNTHETIC DATA ONLY.

Produces source records in the canonical contract (crimemap_api.ingest.contract) with realistic
but entirely fictional patterns: weekly/seasonal cycles, hour-of-day profiles per category,
persistent and emerging spatial clusters, reporting delays, status ageing. Data-quality defects are
injected at configured rates and listed in a manifest so tests can prove the pipeline finds them.
No real names, addresses, phone numbers, vehicle numbers, ID values, or real legal sections appear.
"""

import json
import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from crimemap_api.demo.geography import REGIONS, StationSeed, bbox, point_in_ring, station_seeds
from crimemap_api.ingest.signing import FORMAT, sign

IST = timezone(timedelta(hours=5, minutes=30))
GENERATOR_VERSION = "1"
MAPPING_VERSION = "DEMO-2026.1"
DEMO_ACT = "Demo Penal Code (synthetic)"

# Fictional classification codes -> (analytical category, label). Real legal sections are never used.
CODES: dict[str, tuple[str, str]] = {
    "D-101": ("VIOLENCE_AGAINST_PERSON", "Assault (synthetic)"),
    "D-102": ("VIOLENCE_AGAINST_PERSON", "Grievous hurt (synthetic)"),
    "D-201": ("PROPERTY_THEFT", "Theft (synthetic)"),
    "D-202": ("PROPERTY_THEFT", "House-breaking (synthetic)"),
    "D-211": ("VEHICLE_THEFT", "Vehicle theft (synthetic)"),
    "D-301": ("CYBER_FRAUD", "Online fraud (synthetic)"),
    "D-302": ("CYBER_FRAUD", "Identity misuse (synthetic)"),
    "D-401": ("CRIMES_AGAINST_WOMEN", "Harassment (synthetic)"),
    "D-402": ("CRIMES_AGAINST_WOMEN", "Domestic violence (synthetic)"),
    "D-451": ("CRIMES_AGAINST_CHILDREN", "Offence against a child (synthetic)"),
    "D-501": ("NARCOTICS", "Narcotics possession (synthetic)"),
    "D-601": ("PUBLIC_ORDER", "Rioting (synthetic)"),
    "D-602": ("PUBLIC_ORDER", "Public nuisance (synthetic)"),
    "D-701": ("TRAFFIC_ROAD_SAFETY", "Rash driving (synthetic)"),
    "D-702": ("TRAFFIC_ROAD_SAFETY", "Drunk driving (synthetic)"),
    "D-801": ("SPECIAL_LOCAL_LAWS", "Excise violation (synthetic)"),
    "D-802": ("SPECIAL_LOCAL_LAWS", "Gaming law violation (synthetic)"),
    "D-901": ("OTHER", "Other offence (synthetic)"),
    "E-01": ("PUBLIC_ORDER", "Disturbance call"),
    "E-02": ("PROPERTY_THEFT", "Theft report call"),
    "E-03": ("CRIMES_AGAINST_WOMEN", "Distress call"),
    "E-04": ("VIOLENCE_AGAINST_PERSON", "Assault in progress call"),
    "E-05": ("OTHER", "Other assistance call"),
    "A-01": ("TRAFFIC_ROAD_SAFETY", "Road accident"),
    "T-01": ("TRAFFIC_ROAD_SAFETY", "Traffic obstruction"),
}
CATEGORY_OF_CODE: dict[str, str] = {code: cat for code, (cat, _) in CODES.items()}
INVALID_CODES = {"CASE": "D-999", "ERSS_CALL": "E-99", "ACCIDENT": "A-99", "TRAFFIC_EVENT": "T-99"}

RECORD_TYPES = [("CASE", 0.70), ("ERSS_CALL", 0.18), ("ACCIDENT", 0.07), ("TRAFFIC_EVENT", 0.05)]
CASE_CODES = [
    ("D-201", 18),
    ("D-202", 7),
    ("D-211", 9),
    ("D-101", 10),
    ("D-102", 3),
    ("D-301", 8),
    ("D-302", 3),
    ("D-401", 4),
    ("D-402", 3),
    ("D-451", 1.5),
    ("D-501", 5),
    ("D-601", 2),
    ("D-602", 6),
    ("D-701", 7),
    ("D-702", 4),
    ("D-801", 4),
    ("D-802", 2),
    ("D-901", 3),
]
CALL_CODES = [("E-01", 35), ("E-02", 20), ("E-03", 10), ("E-04", 15), ("E-05", 20)]
REGION_WEIGHTS = [("PDY", 0.55), ("KKL", 0.25), ("MAH", 0.08), ("YAN", 0.12)]

# Relative likelihood by hour of day (local time) per category family.
HOURS: dict[str, list[float]] = {
    "night": [6, 6, 6, 5, 4, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 3, 4, 5, 6, 6],
    "evening": [3, 2, 1, 1, 1, 1, 1, 1, 2, 2, 2, 3, 3, 3, 3, 3, 4, 5, 6, 7, 7, 6, 5, 4],
    "rush": [1, 1, 1, 1, 1, 2, 3, 6, 8, 7, 4, 3, 3, 3, 3, 4, 6, 8, 8, 6, 4, 3, 2, 1],
    "daytime": [1, 1, 1, 1, 1, 1, 1, 2, 3, 5, 6, 6, 6, 6, 6, 6, 5, 4, 3, 2, 2, 1, 1, 1],
}
HOUR_FAMILY = {
    "PROPERTY_THEFT": "night",
    "VEHICLE_THEFT": "night",
    "NARCOTICS": "night",
    "VIOLENCE_AGAINST_PERSON": "evening",
    "PUBLIC_ORDER": "evening",
    "CRIMES_AGAINST_WOMEN": "evening",
    "SPECIAL_LOCAL_LAWS": "evening",
    "TRAFFIC_ROAD_SAFETY": "rush",
    "CYBER_FRAUD": "daytime",
    "CRIMES_AGAINST_CHILDREN": "daytime",
    "OTHER": "daytime",
}
# Mean reporting delay (hours) after the occurrence window ends.
REPORT_DELAY_HOURS = {
    "PROPERTY_THEFT": 8,
    "VEHICLE_THEFT": 6,
    "CYBER_FRAUD": 72,
    "CRIMES_AGAINST_WOMEN": 36,
    "CRIMES_AGAINST_CHILDREN": 48,
    "VIOLENCE_AGAINST_PERSON": 3,
    "NARCOTICS": 1,
    "PUBLIC_ORDER": 1,
    "TRAFFIC_ROAD_SAFETY": 1,
    "SPECIAL_LOCAL_LAWS": 2,
    "OTHER": 12,
}
DEFECT_RATES = {
    "missing_location": 0.03,
    "low_confidence_location": 0.05,
    "missing_occurrence_time": 0.02,
    "possible_duplicate": 0.01,
    "invalid_category_mapping": 0.005,
    "outside_jurisdiction": 0.005,
    "schema_invalid": 0.002,
}
VERSION_UPDATE_RATE = 0.02


@dataclass(frozen=True)
class GeneratorConfig:
    records: int = 200_000
    seed: int = 20261012
    end: datetime = datetime(2026, 9, 30, 23, 59, tzinfo=IST)
    months: int = 24
    source_system: str = "DEMO_SYNTHETIC"


@dataclass
class GeneratorOutput:
    records: list[dict[str, Any]]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class Cluster:
    region: str
    lon: float
    lat: float
    sigma_m: float
    categories: tuple[str, ...]
    emerging: bool = False


@dataclass
class _State:
    rng: random.Random
    days: list[date]
    day_cum: list[float]
    stations: dict[str, list[StationSeed]]
    clusters: dict[str, list[Cluster]]
    defects: dict[str, list[str]] = field(default_factory=lambda: {k: [] for k in DEFECT_RATES})
    defective: set[str] = field(default_factory=set)

    def flag(self, defect: str, ref: str) -> None:
        self.defects[defect].append(ref)
        self.defective.add(ref)


def _weighted(rng: random.Random, items: Sequence[tuple[str, float]]) -> str:
    return rng.choices([i for i, _ in items], weights=[w for _, w in items])[0]


def _random_point_in_region(rng: random.Random, region: str) -> tuple[float, float]:
    ring = REGIONS[region].outline
    min_lon, min_lat, max_lon, max_lat = bbox(ring)
    while True:
        lon, lat = rng.uniform(min_lon, max_lon), rng.uniform(min_lat, max_lat)
        if point_in_ring(lon, lat, ring):
            return lon, lat


def _offset(lon: float, lat: float, dx_m: float, dy_m: float) -> tuple[float, float]:
    return lon + dx_m / (111_320 * math.cos(math.radians(lat))), lat + dy_m / 111_320


def _clusters(seed: int) -> dict[str, list[Cluster]]:
    rng = random.Random(f"crimemap-demo-clusters-{seed}")  # noqa: S311  # nosec B311
    plan: dict[str, list[tuple[str, ...]]] = {
        "PDY": [
            ("PROPERTY_THEFT", "VEHICLE_THEFT"),
            ("VIOLENCE_AGAINST_PERSON", "PUBLIC_ORDER"),
            ("TRAFFIC_ROAD_SAFETY",),
            ("NARCOTICS", "SPECIAL_LOCAL_LAWS"),
        ],
        "KKL": [("PROPERTY_THEFT",), ("TRAFFIC_ROAD_SAFETY", "PUBLIC_ORDER")],
        "MAH": [("PROPERTY_THEFT", "PUBLIC_ORDER")],
        "YAN": [("VIOLENCE_AGAINST_PERSON", "PROPERTY_THEFT")],
    }
    clusters: dict[str, list[Cluster]] = {}
    for region, specs in plan.items():
        sigma = 120.0 if region in ("MAH", "YAN") else 350.0
        clusters[region] = [
            Cluster(region, *_random_point_in_region(rng, region), sigma, cats) for cats in specs
        ]
    # One emerging hotspot: property theft in Puducherry, active only in the last 60 days.
    clusters["PDY"].append(
        Cluster(
            "PDY", *_random_point_in_region(rng, "PDY"), 250.0, ("PROPERTY_THEFT",), emerging=True
        )
    )
    return clusters


def _days(cfg: GeneratorConfig, rng: random.Random) -> tuple[list[date], list[float]]:
    end = cfg.end.date()
    start = end - timedelta(days=round(cfg.months * 30.44))
    days, cum, total = [], [], 0.0
    n = (end - start).days + 1
    for i in range(n):
        d = start + timedelta(days=i)
        season = 1 + 0.15 * math.sin(2 * math.pi * (d.month - 3) / 12)
        trend = 0.9 + 0.2 * i / n
        weekly = 1.15 if d.weekday() in (4, 5) else (0.9 if d.weekday() == 6 else 1.0)
        total += season * trend * weekly * rng.uniform(0.9, 1.1)
        days.append(d)
        cum.append(total)
    return days, cum


def _status(rng: random.Random, record_type: str, age_days: float) -> str:
    if record_type == "CASE":
        p_closed = min(0.85, age_days / 400)
        r = rng.random()
        if r < p_closed:
            return "CLOSED"
        if r < p_closed + 0.15:
            return "DETECTED"
        return "UNDER_INVESTIGATION" if age_days > 7 else "REGISTERED"
    if record_type == "ERSS_CALL":
        return "ATTENDED" if age_days > 0.2 else "RECEIVED"
    if record_type == "ACCIDENT":
        return "CLOSED" if age_days > 30 else "REPORTED"
    return "CLEARED" if age_days > 1 else "REPORTED"


def _nearest_station(stations: list[StationSeed], lon: float, lat: float) -> StationSeed:
    return min(stations, key=lambda s: (s.lon - lon) ** 2 + (s.lat - lat) ** 2)


def _outside(rng: random.Random, region: str, lon: float, lat: float) -> tuple[float, float]:
    """Move a point clearly outside every region (offshore, or beyond the enclave)."""
    while True:
        if region == "MAH":
            lon, lat = lon - rng.uniform(0.03, 0.06), lat  # Arabian Sea
        elif region == "YAN":
            lon, lat = lon, lat + rng.uniform(0.05, 0.10)  # north of the enclave
        else:
            lon, lat = lon + rng.uniform(0.04, 0.08), lat  # Bay of Bengal
        if not any(point_in_ring(lon, lat, r.outline) for r in REGIONS.values()):
            return round(lon, 6), round(lat, 6)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _record(state: _State, cfg: GeneratorConfig, seq: int) -> dict[str, Any]:
    rng = state.rng
    region = _weighted(rng, REGION_WEIGHTS)
    record_type = _weighted(rng, RECORD_TYPES)
    if record_type == "CASE":
        code = _weighted(rng, CASE_CODES)
    elif record_type == "ERSS_CALL":
        code = _weighted(rng, CALL_CODES)
    else:
        code = "A-01" if record_type == "ACCIDENT" else "T-01"
    category = CATEGORY_OF_CODE[code]

    # Place: 25% from a matching cluster, otherwise anywhere in the region.
    cluster = None
    candidates = [c for c in state.clusters[region] if category in c.categories]
    if candidates and rng.random() < 0.35:
        cluster = rng.choice(candidates)
    if cluster is not None:
        for _ in range(20):
            lon, lat = _offset(
                cluster.lon,
                cluster.lat,
                rng.gauss(0, cluster.sigma_m),
                rng.gauss(0, cluster.sigma_m),
            )
            if point_in_ring(lon, lat, REGIONS[region].outline):
                break
        else:
            lon, lat = _random_point_in_region(rng, region)
    else:
        lon, lat = _random_point_in_region(rng, region)

    # Time: emerging-cluster incidents happen only in the last 60 days.
    if cluster is not None and cluster.emerging:
        day = cfg.end.date() - timedelta(days=rng.randint(0, 59))
    else:
        day = rng.choices(state.days, cum_weights=state.day_cum)[0]
    hour = rng.choices(range(24), weights=HOURS[HOUR_FAMILY[category]])[0]
    occ_start = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59), tzinfo=IST)
    if record_type == "ERSS_CALL":
        occ_end = occ_start + timedelta(minutes=rng.randint(0, 20))
        reported = occ_end + timedelta(minutes=rng.randint(1, 15))
    else:
        occ_end = occ_start + timedelta(minutes=rng.randint(0, 180))
        reported = occ_end + timedelta(hours=rng.expovariate(1 / REPORT_DELAY_HOURS[category]))
    occ_start, occ_end, reported = (min(t, cfg.end) for t in (occ_start, occ_end, reported))
    age_days = (cfg.end - reported).total_seconds() / 86_400

    station = _nearest_station(state.stations[region], lon, lat)
    ref = f"DEMO-{region}-{reported.year}-{seq:06d}"
    precision = _weighted(rng, [("EXACT", 0.72), ("APPROXIMATE", 0.21), ("GRID", 0.07)])
    confidence = {"EXACT": (0.85, 1.0), "APPROXIMATE": (0.5, 0.8), "GRID": (0.4, 0.6)}[precision]
    residential_p = {
        "VIOLENCE_AGAINST_PERSON": 0.3,
        "CRIMES_AGAINST_WOMEN": 0.6,
        "CRIMES_AGAINST_CHILDREN": 0.5,
    }.get(category, 0.5 if code == "D-202" else 0.05)
    sensitive = category in ("CRIMES_AGAINST_WOMEN", "CRIMES_AGAINST_CHILDREN")
    highly = record_type == "CASE" and rng.random() < 0.005
    record: dict[str, Any] = {
        "source_system": cfg.source_system,
        "source_record_reference": ref,
        "source_version": 1,
        "record_type": record_type,
        "official_offence_act": DEMO_ACT if record_type == "CASE" else None,
        "official_offence_section": code if record_type == "CASE" else None,
        "official_offence_name": CODES[code][1] if record_type == "CASE" else None,
        "event_type_code": None if record_type == "CASE" else code,
        "occurrence_start_at": _iso(occ_start),
        "occurrence_end_at": _iso(occ_end),
        "reported_at": _iso(reported),
        "reporting_station_code": station.code,
        "status": _status(rng, record_type, age_days),
        "location": {
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "text": None,
            "locality": f"{station.code} demo locality {rng.randint(1, 12):02d}",
            "precision": precision,
            "method": "SOURCE_COORDINATE",
            "confidence": round(rng.uniform(*confidence), 2),
            "is_residential": rng.random() < residential_p,
        },
        "sensitivity_classification": "HIGHLY_RESTRICTED" if highly else "RESTRICTED",
        "restricted": (
            {"victim_reference_token": f"SYNTHETIC-REF-{seq:06d}", "witness_reference_token": None}  # nosec B105
            if sensitive or highly
            else None
        ),
    }
    _inject_defects(state, record, region)
    return record


def _inject_defects(state: _State, record: dict[str, Any], region: str) -> None:
    rng, ref, loc = state.rng, record["source_record_reference"], record["location"]
    if rng.random() < DEFECT_RATES["missing_location"]:
        loc.update(lat=None, lon=None, precision="UNKNOWN", method="NONE", confidence=None)
        state.flag("missing_location", ref)
    elif rng.random() < DEFECT_RATES["outside_jurisdiction"]:
        loc["lon"], loc["lat"] = _outside(rng, region, loc["lon"], loc["lat"])
        state.flag("outside_jurisdiction", ref)
    elif rng.random() < DEFECT_RATES["low_confidence_location"]:
        loc.update(
            confidence=round(rng.uniform(0.1, 0.39), 2), precision="APPROXIMATE", method="SUGGESTED"
        )
        state.flag("low_confidence_location", ref)
    if rng.random() < DEFECT_RATES["missing_occurrence_time"]:
        record.update(occurrence_start_at=None, occurrence_end_at=None)
        state.flag("missing_occurrence_time", ref)
    if rng.random() < DEFECT_RATES["invalid_category_mapping"]:
        bad = INVALID_CODES[record["record_type"]]
        record[
            "official_offence_section" if record["record_type"] == "CASE" else "event_type_code"
        ] = bad
        state.flag("invalid_category_mapping", ref)
    if rng.random() < DEFECT_RATES["schema_invalid"]:
        if rng.random() < 0.5:
            record["reported_at"] = None
        else:
            record["record_type"] = "UNKNOWN_TYPE"
        state.flag("schema_invalid", ref)


def _duplicate(state: _State, original: dict[str, Any], seq: int) -> dict[str, Any]:
    """A near-identical record under a new reference (same incident reported twice)."""
    rng = state.rng
    dup: dict[str, Any] = json.loads(json.dumps(original))
    region = dup["source_record_reference"].split("-")[1]
    reported = datetime.fromisoformat(dup["reported_at"]) + timedelta(minutes=rng.randint(5, 50))
    dup["reported_at"] = _iso(reported)
    dup["source_record_reference"] = f"DEMO-{region}-{reported.year}-{seq:06d}"
    lon, lat = _offset(
        dup["location"]["lon"], dup["location"]["lat"], rng.uniform(-8, 8), rng.uniform(-8, 8)
    )
    dup["location"].update(lon=round(lon, 6), lat=round(lat, 6))
    start = datetime.fromisoformat(dup["occurrence_start_at"]) + timedelta(
        minutes=rng.randint(0, 10)
    )
    dup["occurrence_start_at"] = _iso(start)
    dup["occurrence_end_at"] = _iso(max(start, datetime.fromisoformat(dup["occurrence_end_at"])))
    if dup.get("restricted"):
        dup["restricted"] = {
            "victim_reference_token": f"SYNTHETIC-REF-{seq:06d}",
            "witness_reference_token": None,  # nosec B105
        }
    return dup


def generate(cfg: GeneratorConfig) -> GeneratorOutput:
    rng = random.Random(cfg.seed)  # noqa: S311  # nosec B311
    days, day_cum = _days(cfg, rng)
    state = _State(
        rng=rng,
        days=days,
        day_cum=day_cum,
        stations={code: station_seeds(code) for code in REGIONS},
        clusters=_clusters(cfg.seed),
    )
    records: list[dict[str, Any]] = []
    clean: list[int] = []  # indices of defect-free records, usable as duplicate sources
    for seq in range(1, cfg.records + 1):
        if clean and rng.random() < DEFECT_RATES["possible_duplicate"]:
            record = _duplicate(state, records[rng.choice(clean)], seq)
            state.flag("possible_duplicate", record["source_record_reference"])
        else:
            record = _record(state, cfg, seq)
            if record["source_record_reference"] not in state.defective:
                clean.append(len(records))
        records.append(record)

    # Version updates: open cases that later change status (change detection).
    open_cases = [
        r
        for r in records
        if r["record_type"] == "CASE"
        and r["source_record_reference"] not in state.defective
        and r["status"] in ("REGISTERED", "UNDER_INVESTIGATION")
    ]
    updates: list[str] = []
    for r in rng.sample(open_cases, min(len(open_cases), round(cfg.records * VERSION_UPDATE_RATE))):
        v2 = json.loads(json.dumps(r))
        v2.update(source_version=2, status=rng.choice(["DETECTED", "CLOSED"]))
        records.append(v2)
        updates.append(r["source_record_reference"])

    daily = Counter((r["reported_at"] or "unknown")[:10] for r in records)
    manifest = {
        "generator": {"version": GENERATOR_VERSION, "seed": cfg.seed, "records": cfg.records},
        "source_system": cfg.source_system,
        "mapping_version": MAPPING_VERSION,
        "window_end": cfg.end.isoformat(),
        "defects": {**state.defects, "version_update": updates},
        "daily_totals": dict(sorted(daily.items())),
    }
    return GeneratorOutput(records=records, manifest=manifest)


def write_batches(
    output: GeneratorOutput,
    out_dir: Path,
    key: bytes,
    *,
    batch_size: int = 5000,
    extracted_at: datetime,
    source_system: str = "DEMO_SYNTHETIC",
) -> list[Path]:
    """Write signed batch files (and the manifest) to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = output.manifest["generator"]["seed"]
    paths = []
    for n, start in enumerate(range(0, len(output.records), batch_size), start=1):
        records = output.records[start : start + batch_size]
        envelope = {
            "format": FORMAT,
            "source_system": source_system,
            # Unique per generation run: seed, size and extraction time are part of the reference, so
            # a re-run with other settings is never mistaken for an already-processed batch.
            "batch_ref": (
                f"{source_system}-{seed}-{len(output.records)}-"
                f"{extracted_at.astimezone(UTC):%Y%m%dT%H%M%S}-{n:04d}"
            ),
            "extracted_at": extracted_at.astimezone(UTC).isoformat(),
            "record_count": len(records),
            "records": records,
        }
        path = out_dir / f"{source_system.lower()}-{n:04d}.json"
        path.write_text(
            json.dumps({"envelope": envelope, "signature": sign(envelope, key)}), encoding="utf-8"
        )
        paths.append(path)
    (out_dir / f"{source_system.lower()}-manifest.json").write_text(
        json.dumps(output.manifest, indent=1), encoding="utf-8"
    )
    return paths
