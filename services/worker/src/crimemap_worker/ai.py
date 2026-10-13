"""AI-assisted detectors (Phase 7, ADR-037). Explainable statistics and rules, no black box.

Each detector runs per region, in its own transaction, as the database role ``crimemap_ai``
(its own login, used only by the ai-worker container). That role can read only the approved inputs — never highly restricted or
sensitive-category records (row-level security) — and can write only AI runs and insight cards. It
has no grant on actions, exports, notes or case updates, so whatever this code does, AI cannot
create an action, export data or change a case.

Every insight states its evidence and baseline, a data-quality score and exclusions, a confidence
with a calibration note, a plain explanation, limitations and alternative explanations, and awaits
analyst review. Insights concern places, time windows and records, never people.

The reference date ("as of") is the latest record date in the region: the demo data is historical.
A production deployment would use today's date.

python -m crimemap_worker.ai run [--region PDY] [--model emerging-pattern]
"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from crimemap_api.audit.events import AuditContext, AuditEvent, write_independent
from crimemap_api.logging_config import configure_logging

log = logging.getLogger("crimemap.worker.ai")

REVIEW = "AI-assisted; analyst review required."


@dataclass
class Card:
    """One insight card (columns of core.ai_insight)."""

    dedup_key: str
    station_id: UUID | None
    geography: dict[str, Any]
    window_start: date
    window_end: date
    category_id: UUID | None
    category_label: str
    evidence: dict[str, Any]
    baseline: dict[str, Any]
    data_quality_score: float
    exclusions: dict[str, Any]
    confidence: float
    confidence_label: str
    calibration_note: str
    explanation: str
    limitations: list[str]
    alternatives: list[str]
    incident_ids: list[UUID] = field(default_factory=list)
    geometry: tuple[str, dict[str, Any]] | None = None  # ("point" | "hex" | "circle", parameters)


@dataclass
class Region:
    id: UUID
    code: str
    crs: int
    as_of: date


@dataclass
class Detected:
    cards: list[Card]
    inputs: list[Any]  # the aggregated inputs, hashed into the run
    summary: dict[str, Any]


Detector = Callable[[AsyncConnection, Region, dict[str, Any]], Awaitable[Detected]]


def _label(value: float, high: float, medium: float) -> str:
    return "HIGH" if value >= high else "MEDIUM" if value >= medium else "LOW"


def poisson_upper_tail(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam), summed in log space (no underflow at large lam or k)."""
    if k <= 0:
        return 1.0
    if lam <= 0:
        return 0.0

    def log_pmf(i: int) -> float:
        return i * math.log(lam) - lam - math.lgamma(i + 1)

    def log_sum(terms: list[float]) -> float:
        top = max(terms)
        return top + math.log(sum(math.exp(t - top) for t in terms))

    if k > lam:  # the upper tail directly (its terms fall away quickly)
        terms = [log_pmf(i) for i in range(k, k + 2000 + int(10 * math.sqrt(lam)))]
        return min(1.0, math.exp(log_sum(terms)))
    below = math.exp(log_sum([log_pmf(i) for i in range(k)]))
    return min(1.0, max(0.0, 1.0 - below))


def block(day: date, days: int = 28) -> int:
    """The 28-day block a date falls in: one card per pattern per block, not one per day."""
    return day.toordinal() // days


def tail_label(p: float) -> str:
    return "HIGH" if p < 1e-6 else "MEDIUM" if p < 1e-4 else "LOW"


def normal_upper_tail(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


def _rows(result: Any) -> list[Any]:
    return list(result.all())


# --- 1. data-quality anomalies -----------------------------------------------------------------------

_LOCATION_SHARE = text(
    """
WITH r AS (
    SELECT i.station_id, (i.reported_at AT TIME ZONE 'Asia/Kolkata')::date AS d,
           (l.geometry IS NULL OR coalesce(l.confidence_score, 0) < 0.4) AS bad
    FROM core.incident_record i LEFT JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.region_id = :region AND i.station_id IS NOT NULL
      AND i.reported_at >= (CAST(:base_start AS date)::timestamp AT TIME ZONE 'Asia/Kolkata') AND i.reported_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
)
SELECT r.station_id, s.code, count(*) FILTER (WHERE d >= :cur_start) AS n,
       count(*) FILTER (WHERE d >= :cur_start AND bad) AS k,
       count(*) FILTER (WHERE d < :cur_start) AS bn, count(*) FILTER (WHERE d < :cur_start AND bad) AS bk
FROM r JOIN core.organizational_unit s ON s.id = r.station_id
GROUP BY r.station_id, s.code ORDER BY s.code
"""
)

_SOURCE_VOLUME = text(
    """
SELECT i.source_system_id, ss.code, (i.reported_at AT TIME ZONE 'Asia/Kolkata')::date AS d, count(*) AS n
FROM core.incident_record i JOIN core.source_system ss ON ss.id = i.source_system_id
WHERE i.region_id = :region AND i.reported_at >= (CAST(:base_start AS date)::timestamp AT TIME ZONE 'Asia/Kolkata') AND i.reported_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
GROUP BY 1, 2, 3 ORDER BY 2, 3
"""
)


async def dq_anomaly(conn: AsyncConnection, region: Region, p: dict[str, Any]) -> Detected:
    end = region.as_of
    cur_start = end - timedelta(days=p["window_days"] - 1)
    base_start = cur_start - timedelta(days=p["baseline_days"])
    args = {
        "region": region.id,
        "base_start": base_start,
        "end_excl": end + timedelta(days=1),
        "cur_start": cur_start,
    }
    shares = _rows(await conn.execute(_LOCATION_SHARE, args))
    cards: list[Card] = []
    for r in shares:
        if r.n < p["min_records"] or r.bn < p["min_records"]:
            continue
        p0 = (r.bk + 1) / (r.bn + 2)
        z = (r.k - r.n * p0) / math.sqrt(r.n * p0 * (1 - p0))
        share = r.k / r.n
        if z < p["z_threshold"] or share - p0 < 0.05:
            continue
        cards.append(
            Card(
                dedup_key=f"location-share:{r.code}:{block(end)}",
                station_id=r.station_id,
                geography={"station": r.code},
                window_start=cur_start,
                window_end=end,
                category_id=None,
                category_label="All categories",
                evidence={
                    "records": r.n,
                    "without_usable_location": r.k,
                    "share": round(share, 3),
                    "z": round(z, 2),
                },
                baseline={
                    "records": r.bn,
                    "without_usable_location": r.bk,
                    "share": round(p0, 3),
                    "days": p["baseline_days"],
                },
                data_quality_score=round(1 - share, 3),
                exclusions={
                    "note": "Highly restricted and sensitive-category records are not used."
                },
                confidence=round(1 - normal_upper_tail(z), 3),
                confidence_label=tail_label(normal_upper_tail(z)),
                calibration_note="Confidence is 1 minus the binomial tail probability against the station's own baseline; not calibrated against reviewed outcomes.",
                explanation=(
                    f"In the last {p['window_days']} days, {r.k} of {r.n} records at {r.code} ({share:.0%}) had no usable "
                    f"location, against {p0:.0%} in the previous {p['baseline_days']} days (z = {z:.1f})."
                ),
                limitations=[
                    "Compares the station with its own past only.",
                    "A change in how a source records locations has the same effect as a data fault.",
                ],
                alternatives=[
                    "A new source or form at the station.",
                    "More reports from places that are hard to geocode (e.g. roads, open land).",
                ],
            )
        )
    volume = _rows(await conn.execute(_SOURCE_VOLUME, args))
    by_source: dict[tuple[UUID, str], dict[date, int]] = {}
    for r in volume:
        by_source.setdefault((r.source_system_id, r.code), {})[r.d] = r.n
    recent_start = end - timedelta(days=p["volume_window_days"] - 1)
    for (_sid, code), days in by_source.items():
        base_days = [
            base_start + timedelta(days=i) for i in range((recent_start - base_start).days)
        ]
        base = [days.get(d, 0) for d in base_days]
        recent = [
            days.get(recent_start + timedelta(days=i), 0) for i in range(p["volume_window_days"])
        ]
        mean = sum(base) / len(base) if base else 0
        if mean < 5:
            continue
        sd = math.sqrt(sum((x - mean) ** 2 for x in base) / max(1, len(base) - 1)) or 1.0
        recent_mean = sum(recent) / len(recent)
        z = (mean - recent_mean) / (sd / math.sqrt(len(recent)))
        if z < p["z_threshold"]:
            continue
        cards.append(
            Card(
                dedup_key=f"source-volume:{code}:{region.code}:{block(end)}",
                station_id=None,
                geography={"region": region.code, "source": code},
                window_start=recent_start,
                window_end=end,
                category_id=None,
                category_label="All categories",
                evidence={
                    "source": code,
                    "mean_per_day": round(recent_mean, 1),
                    "daily": recent,
                    "z": round(z, 2),
                },
                baseline={
                    "mean_per_day": round(mean, 1),
                    "sd_per_day": round(sd, 1),
                    "days": len(base),
                },
                data_quality_score=round(min(1.0, recent_mean / mean), 3),
                exclusions={
                    "note": "Highly restricted and sensitive-category records are not counted."
                },
                confidence=round(1 - normal_upper_tail(z), 3),
                confidence_label=tail_label(normal_upper_tail(z)),
                calibration_note="Normal approximation to daily counts; weekly patterns are not modelled.",
                explanation=(
                    f"Source {code} sent {recent_mean:.1f} records a day in {region.code} over the last "
                    f"{p['volume_window_days']} days, against {mean:.1f} before (z = {z:.1f}): a possible feed problem."
                ),
                limitations=["Holidays and real changes in reporting also lower volume."],
                alternatives=[
                    "A genuine fall in incidents or reports.",
                    "A planned change at the source.",
                ],
            )
        )
    inputs = [[str(r.station_id), r.n, r.k, r.bn, r.bk] for r in shares] + [
        [str(r.source_system_id), str(r.d), r.n] for r in volume
    ]
    summary = {
        "stations": len(shares),
        "sources": len(by_source),
        "records": sum(r.n + r.bn for r in shares),
    }
    return Detected(cards, inputs, summary)


# --- 2. duplicate suggestions ------------------------------------------------------------------------

_DUPLICATES = text(
    """
SELECT t.id AS a, d.id AS b, CASE WHEN d.station_id IS NOT DISTINCT FROM t.station_id THEN t.station_id END AS station_id,
       t.analytical_category_id AS cat, c.name AS cat_name,
       d.distance_m, d.gap_min, d.same_locality, d.same_station, d.same_source,
       (t.occurrence_start_at AT TIME ZONE 'Asia/Kolkata')::date AS day
FROM core.incident_record t
JOIN core.incident_location tl ON tl.incident_id = t.id AND tl.geometry IS NOT NULL
JOIN core.analytical_category c ON c.id = t.analytical_category_id
JOIN LATERAL (
    SELECT o.id, o.station_id,
           ST_Distance(ol.geometry::geography, tl.geometry::geography) AS distance_m,
           abs(extract(epoch FROM o.occurrence_start_at - t.occurrence_start_at)) / 60 AS gap_min,
           lower(btrim(ol.locality)) IS NOT DISTINCT FROM lower(btrim(tl.locality)) AS same_locality,
           o.reporting_station_id IS NOT DISTINCT FROM t.reporting_station_id AS same_station,
           o.source_system_id = t.source_system_id AS same_source
    FROM core.incident_location ol JOIN core.incident_record o ON o.id = ol.incident_id
    WHERE ST_DWithin(ol.geometry, tl.geometry, 0.0015)
      AND ST_DWithin(ol.geometry::geography, tl.geometry::geography, :max_distance)
      AND o.id > t.id AND o.region_id = t.region_id AND o.analytical_category_id = t.analytical_category_id
      AND abs(extract(epoch FROM o.occurrence_start_at - t.occurrence_start_at)) <= :max_gap * 60
) d ON true
WHERE t.region_id = :region AND t.occurrence_start_at >= (CAST(:since AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
  AND t.occurrence_start_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
  AND NOT EXISTS (
      SELECT 1 FROM core.data_quality_issue q WHERE q.rule_code = 'DQ-DUP-001' AND q.state IN ('OPEN', 'ASSIGNED')
        AND ((q.incident_id = d.id AND q.detail->>'duplicate_of_id' = t.id::text)
          OR (q.incident_id = t.id AND q.detail->>'duplicate_of_id' = d.id::text)))
"""
)


def duplicate_score(
    distance_m: float, gap_min: float, same_locality: bool, same_station: bool, p: dict[str, Any]
) -> float:
    return round(
        0.4 * (1 - distance_m / float(p["max_distance_m"]))
        + 0.3 * (1 - gap_min / float(p["max_gap_minutes"]))
        + 0.15 * same_locality
        + 0.15 * same_station,
        3,
    )


async def duplicate_suggest(conn: AsyncConnection, region: Region, p: dict[str, Any]) -> Detected:
    end = region.as_of
    since = end - timedelta(days=90)
    rows = _rows(
        await conn.execute(
            _DUPLICATES,
            {
                "region": region.id,
                "since": since,
                "end_excl": end + timedelta(days=1),
                "max_distance": p["max_distance_m"],
                "max_gap": p["max_gap_minutes"],
            },
        )
    )
    scored = sorted(
        (
            (
                duplicate_score(
                    float(r.distance_m), float(r.gap_min), r.same_locality, r.same_station, p
                ),
                r,
            )
            for r in rows
        ),
        key=lambda x: -x[0],
    )
    cards = []
    for score, r in [x for x in scored if x[0] >= p["min_score"]][: p["max_suggestions"]]:
        signals = [
            f"{float(r.distance_m):.0f} m apart",
            f"{round(float(r.gap_min))} minute{'' if round(float(r.gap_min)) == 1 else 's'} apart",
            "same category",
        ]
        signals += ["same locality"] if r.same_locality else []
        signals += ["same reporting station"] if r.same_station else []
        cards.append(
            Card(
                dedup_key=f"duplicate:{r.a}:{r.b}",
                station_id=r.station_id,
                geography={"region": region.code},
                window_start=r.day,
                window_end=r.day,
                category_id=r.cat,
                category_label=r.cat_name,
                evidence={
                    "distance_m": round(float(r.distance_m), 1),
                    "gap_minutes": round(float(r.gap_min), 1),
                    "same_locality": r.same_locality,
                    "same_reporting_station": r.same_station,
                    "same_source": r.same_source,
                    "score": score,
                },
                baseline={
                    "rule": "score >= " + str(p["min_score"]),
                    "weights": {"distance": 0.4, "time": 0.3, "locality": 0.15, "station": 0.15},
                },
                data_quality_score=1.0,
                exclusions={"note": "Pairs already flagged by rule DQ-DUP-001 are skipped."},
                confidence=score,
                confidence_label=_label(score, 0.9, 0.8),
                calibration_note="Hand-set weights; the score is not a calibrated probability of duplication.",
                explanation="Two records may describe the same event: "
                + ", ".join(signals)
                + ". Compare them; nothing is merged.",
                limitations=[
                    "Separate incidents close together look the same to this rule.",
                    "Text of the records is not compared.",
                ],
                alternatives=[
                    "Two genuine incidents (e.g. a series at one place).",
                    "A follow-up report of the same event.",
                ],
                incident_ids=[r.a, r.b],
            )
        )
    inputs = [
        [str(r.a), str(r.b), round(float(r.distance_m), 1), round(float(r.gap_min), 1)]
        for r in rows
    ]
    return Detected(
        cards,
        inputs,
        {"candidate_pairs": len(rows), "suggested": len(cards), "since": since.isoformat()},
    )


# --- 3. geocoding suggestions ------------------------------------------------------------------------

_GEOCODE = text(
    """
WITH targets AS (
    SELECT i.id, coalesce(i.station_id, i.reporting_station_id) AS station_id, lower(btrim(l.locality)) AS loc,
           l.locality, (i.reported_at AT TIME ZONE 'Asia/Kolkata')::date AS day, l.confidence_score
    FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.region_id = :region AND l.locality IS NOT NULL AND btrim(l.locality) <> ''
      AND (l.geometry IS NULL OR coalesce(l.confidence_score, 0) < 0.4)
      AND i.reported_at >= (CAST(:since AS date)::timestamp AT TIME ZONE 'Asia/Kolkata') AND i.reported_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
    ORDER BY i.reported_at DESC LIMIT :max_targets
),
support AS (
    SELECT coalesce(i.station_id, i.reporting_station_id) AS station_id, lower(btrim(l.locality)) AS loc,
           count(*) AS n, ST_Centroid(ST_Collect(l.geometry)) AS centre
    FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.region_id = :region AND l.geometry IS NOT NULL AND l.confidence_score >= :support_confidence
      AND lower(btrim(l.locality)) IN (SELECT loc FROM targets)
    GROUP BY 1, 2 HAVING count(*) >= :min_support
),
spread AS (
    SELECT s.station_id, s.loc, s.n, s.centre,
           avg(ST_Distance(l.geometry::geography, s.centre::geography)) AS spread_m
    FROM support s
    JOIN core.incident_location l ON lower(btrim(l.locality)) = s.loc AND l.geometry IS NOT NULL
         AND l.confidence_score >= :support_confidence
    JOIN core.incident_record i ON i.id = l.incident_id AND coalesce(i.station_id, i.reporting_station_id) = s.station_id
    GROUP BY 1, 2, 3, 4
)
SELECT t.id, t.station_id, t.locality, t.day, t.confidence_score, sp.n, sp.spread_m,
       round(ST_X(sp.centre)::numeric, 4) AS lon, round(ST_Y(sp.centre)::numeric, 4) AS lat
FROM targets t JOIN spread sp ON sp.station_id = t.station_id AND sp.loc = t.loc
"""
)


async def geocode_suggest(conn: AsyncConnection, region: Region, p: dict[str, Any]) -> Detected:
    end = region.as_of
    since = end - timedelta(days=365)
    rows = _rows(
        await conn.execute(
            _GEOCODE,
            {
                "region": region.id,
                "since": since,
                "end_excl": end + timedelta(days=1),
                "max_targets": 200,
                "min_support": p["min_support"],
                "support_confidence": p["support_confidence"],
            },
        )
    )
    cards = []
    for r in rows:
        spread = float(r.spread_m)
        if spread > p["max_spread_m"]:  # too spread out to be a useful suggestion
            continue
        if r.n >= p["high_support"] and spread < p["high_spread_m"]:
            conf, label = 0.85, "HIGH"
        elif r.n >= 10 and spread < 2 * p["high_spread_m"]:
            conf, label = 0.65, "MEDIUM"
        else:
            conf, label = 0.4, "LOW"
        review = (
            "Low confidence: verify the place with the source before any correction."
            if label == "LOW"
            else "Verify against the record before any correction."
        )
        cards.append(
            Card(
                dedup_key=f"geocode:{r.id}",
                station_id=r.station_id,
                geography={
                    "locality": r.locality,
                    "lon": float(r.lon),
                    "lat": float(r.lat),
                    "kind": "locality centre",
                },
                window_start=r.day,
                window_end=r.day,
                category_id=None,
                category_label="All categories",
                evidence={
                    "supporting_records": r.n,
                    "spread_m": round(spread),
                    "current_confidence": float(r.confidence_score)
                    if r.confidence_score is not None
                    else None,
                },
                baseline={
                    "min_support": p["min_support"],
                    "support_confidence": p["support_confidence"],
                },
                data_quality_score=round(min(1.0, r.n / p["high_support"]), 3),
                exclusions={
                    "note": "Only well-located records (confidence 0.8+) in the same station support the suggestion."
                },
                confidence=conf,
                confidence_label=label,
                calibration_note="Confidence bands from support and spread; not checked against surveyed positions.",
                explanation=(
                    f"The locality '{r.locality}' has {r.n} well-located records in this station, spread about "
                    f"{spread:.0f} m around ({float(r.lat):.4f}, {float(r.lon):.4f}). {review}"
                ),
                limitations=[
                    "A locality centre, not the exact place.",
                    "Spelling variants of the locality are not matched.",
                ],
                alternatives=[
                    "The incident happened elsewhere in or near the locality.",
                    "The locality text is wrong.",
                ],
                incident_ids=[r.id],
                geometry=(
                    "point",
                    {"lon": float(r.lon), "lat": float(r.lat)},
                ),
            )
        )
    inputs = [[str(r.id), r.n, round(float(r.spread_m))] for r in rows]
    return Detected(cards, inputs, {"targets_with_support": len(rows), "since": since.isoformat()})


# --- 4. emerging place-time patterns -----------------------------------------------------------------

_CELL_COUNTS = text(
    """
WITH pts AS (
    SELECT i.analytical_category_id AS cat, i.station_id,
           core.hex_cell(l.analysis_x, l.analysis_y, :size) AS c,
           (i.occurrence_start_at AT TIME ZONE 'Asia/Kolkata')::date AS d
    FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.region_id = :region AND i.record_type = 'CASE' AND i.analytical_category_id IS NOT NULL
      AND l.analysis_x IS NOT NULL AND l.geometry IS NOT NULL AND coalesce(l.confidence_score, 0) >= 0.4
      AND i.occurrence_start_at >= (CAST(:base_start AS date)::timestamp AT TIME ZONE 'Asia/Kolkata') AND i.occurrence_start_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
)
SELECT pts.cat, c.code AS cat_code, c.name AS cat_name, pts.c[1] AS q, pts.c[2] AS r,
       count(*) FILTER (WHERE d >= :cur_start) AS cur, count(*) FILTER (WHERE d < :cur_start) AS base,
       CASE WHEN count(DISTINCT pts.station_id) FILTER (WHERE d >= :cur_start) = 1
            THEN (min(pts.station_id::text) FILTER (WHERE d >= :cur_start))::uuid END AS station_id
FROM pts JOIN core.analytical_category c ON c.id = pts.cat
GROUP BY 1, 2, 3, 4, 5
"""
)

_USABLE = text(
    """
SELECT count(*) AS n, count(*) FILTER (WHERE l.geometry IS NOT NULL AND l.analysis_x IS NOT NULL
                        AND coalesce(l.confidence_score, 0) >= 0.4) AS usable
FROM core.incident_record i LEFT JOIN core.incident_location l ON l.incident_id = i.id
WHERE i.region_id = :region AND i.record_type = 'CASE'
  AND i.occurrence_start_at >= (CAST(:cur_start AS date)::timestamp AT TIME ZONE 'Asia/Kolkata') AND i.occurrence_start_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
"""
)


async def emerging_pattern(conn: AsyncConnection, region: Region, p: dict[str, Any]) -> Detected:
    end = region.as_of
    days = p["window_days"]
    cur_start = end - timedelta(days=days - 1)
    base_start = cur_start - timedelta(days=days * p["baseline_windows"])
    args = {
        "region": region.id,
        "size": float(p["cell_m"]),
        "base_start": base_start,
        "cur_start": cur_start,
        "end_excl": end + timedelta(days=1),
    }
    rows = _rows(await conn.execute(_CELL_COUNTS, args))
    usable = (await conn.execute(_USABLE, args)).one()
    dq = round(usable.usable / usable.n, 3) if usable.n else 0.0
    cards = []
    for r in rows:
        lam = r.base / p["baseline_windows"]
        if r.cur < p["min_count"]:
            continue
        pv = poisson_upper_tail(int(r.cur), max(lam, 0.5))
        if pv >= p["max_p"]:
            continue
        key = f"{region.code}:{p['cell_m']}:{r.q}:{r.r}"
        cards.append(
            Card(
                dedup_key=f"emerging:{key}:{r.cat_code}:{block(end)}",
                station_id=r.station_id,
                geography={"cell_key": key, "cell_m": p["cell_m"]},
                window_start=cur_start,
                window_end=end,
                category_id=r.cat,
                category_label=r.cat_name,
                evidence={
                    "count": r.cur,
                    "window_days": days,
                    "p_value": float(f"{pv:.2e}"),
                    "ratio": round(r.cur / max(lam, 0.5), 1),
                },
                baseline={
                    "mean_per_window": round(lam, 2),
                    "baseline_count": r.base,
                    "baseline_days": days * p["baseline_windows"],
                },
                data_quality_score=dq,
                exclusions={
                    "without_usable_location": usable.n - usable.usable,
                    "note": "Sensitive categories and highly restricted records are not used.",
                },
                confidence=round(1 - pv, 6),
                confidence_label="HIGH" if pv < 1e-5 else "MEDIUM" if pv < 1e-4 else "LOW",
                calibration_note="Poisson upper-tail p-value; assumes counts are Poisson (overdispersion not modelled); many cells are tested.",
                explanation=(
                    f"{r.cur} recorded {r.cat_name.lower()} cases in cell {key} in the last {days} days, against an "
                    f"average of {lam:.1f} per {days} days over the previous {days * p['baseline_windows']} days."
                ),
                limitations=[
                    "Recorded cases only: more reporting or patrol presence also raises counts.",
                    "A place and time window to review, not a forecast and not about any person.",
                ],
                alternatives=[
                    "A change in recording or reporting.",
                    "A one-off event or a single series.",
                    "Chance (many cells are tested).",
                ],
                geometry=(
                    "hex",
                    {"q": r.q, "r": r.r, "size": float(p["cell_m"]), "crs": region.crs},
                ),
            )
        )
    inputs = [[str(r.cat), r.q, r.r, r.cur, r.base] for r in rows]
    return Detected(
        cards,
        inputs,
        {"cells": len(rows), "case_records": usable.n, "usable_location": usable.usable},
    )


# --- 5. explainable clusters -------------------------------------------------------------------------

_CLUSTERS = text(
    """
WITH pts AS (
    SELECT i.analytical_category_id AS cat, i.station_id, l.locality, l.analysis_x AS x, l.analysis_y AS y,
           i.occurrence_start_at AS t
    FROM core.incident_record i JOIN core.incident_location l ON l.incident_id = i.id
    WHERE i.region_id = :region AND i.record_type = 'CASE' AND i.analytical_category_id IS NOT NULL
      AND l.analysis_x IS NOT NULL AND coalesce(l.confidence_score, 0) >= 0.4
      AND i.occurrence_start_at >= (CAST(:cur_start AS date)::timestamp AT TIME ZONE 'Asia/Kolkata') AND i.occurrence_start_at < (CAST(:end_excl AS date)::timestamp AT TIME ZONE 'Asia/Kolkata')
),
cl AS (
    SELECT *, ST_ClusterDBSCAN(ST_MakePoint(x, y), eps := :eps, minpoints := :minp) OVER (PARTITION BY cat) AS cid
    FROM pts
)
SELECT cl.cat, c.code AS cat_code, c.name AS cat_name, cid, count(*) AS n, avg(x) AS cx, avg(y) AS cy,
       ST_MaxDistance(ST_MakePoint(avg(x), avg(y)), ST_Collect(ST_MakePoint(x, y))) AS radius,
       (min(t) AT TIME ZONE 'Asia/Kolkata')::date AS first_at, (max(t) AT TIME ZONE 'Asia/Kolkata')::date AS last_at,
       count(*) FILTER (WHERE extract(hour FROM t AT TIME ZONE 'Asia/Kolkata') >= 20
                          OR extract(hour FROM t AT TIME ZONE 'Asia/Kolkata') < 6) AS night,
       CASE WHEN count(DISTINCT cl.station_id) = 1 AND count(cl.station_id) = count(*)
            THEN min(cl.station_id::text)::uuid END AS station_id,
       mode() WITHIN GROUP (ORDER BY cl.locality) AS locality
FROM cl JOIN core.analytical_category c ON c.id = cl.cat
WHERE cid IS NOT NULL
GROUP BY cl.cat, c.code, c.name, cid
"""
)


async def cluster_explain(conn: AsyncConnection, region: Region, p: dict[str, Any]) -> Detected:
    end = region.as_of
    cur_start = end - timedelta(days=p["window_days"] - 1)
    rows = _rows(
        await conn.execute(
            _CLUSTERS,
            {
                "region": region.id,
                "cur_start": cur_start,
                "end_excl": end + timedelta(days=1),
                "eps": float(p["eps_m"]),
                "minp": p["min_points"],
            },
        )
    )
    cards = []
    snap = p["snap_m"]
    for r in rows:
        sx, sy = round(float(r.cx) / snap) * snap, round(float(r.cy) / snap) * snap
        radius = max(50, math.ceil(float(r.radius) / 50) * 50)
        night = r.night / r.n
        cards.append(
            Card(
                dedup_key=f"cluster:{r.cat_code}:{sx}:{sy}:{block(end)}",
                station_id=r.station_id,
                geography={"centre_snapped_m": snap, "radius_m": radius, "locality": r.locality},
                window_start=cur_start,
                window_end=end,
                category_id=r.cat,
                category_label=r.cat_name,
                evidence={
                    "cases": r.n,
                    "radius_m": radius,
                    "first": r.first_at.isoformat(),
                    "last": r.last_at.isoformat(),
                    "night_share": round(night, 2),
                },
                baseline={
                    "method": "DBSCAN",
                    "eps_m": p["eps_m"],
                    "min_points": p["min_points"],
                    "window_days": p["window_days"],
                },
                data_quality_score=1.0,
                exclusions={
                    "note": "Records without a usable location, sensitive categories and highly restricted records are not used."
                },
                confidence=round(min(0.95, r.n / 25), 3),
                confidence_label=_label(r.n, 20, 12),
                calibration_note="Confidence reflects cluster size only; cluster validity is not measured.",
                explanation=(
                    f"{r.n} recorded {r.cat_name.lower()} cases within about {radius} m of a point near "
                    f"{r.locality or 'an unnamed locality'} between {r.first_at:%d %b} and {r.last_at:%d %b}; "
                    f"{night:.0%} at night (20:00 to 06:00)."
                ),
                limitations=[
                    "Other parameters would find other clusters.",
                    "A concentration of records, not a series or a cause.",
                ],
                alternatives=[
                    "A busy place with many people (more incidents, not higher risk).",
                    "Reporting concentrated at one place.",
                ],
                geometry=(
                    "circle",
                    {"sx": float(sx), "sy": float(sy), "crs": region.crs, "radius": float(radius)},
                ),
            )
        )
    inputs = [[str(r.cat), int(r.cid), r.n, round(float(r.cx)), round(float(r.cy))] for r in rows]
    return Detected(cards, inputs, {"clusters": len(rows), "window_start": cur_start.isoformat()})


DETECTORS: dict[str, Detector] = {
    "dq-anomaly": dq_anomaly,
    "duplicate-suggest": duplicate_suggest,
    "geocode-suggest": geocode_suggest,
    "emerging-pattern": emerging_pattern,
    "cluster-explain": cluster_explain,
}

# --- running -----------------------------------------------------------------------------------------

_MODELS = text(
    """
SELECT id, code, version, parameters FROM core.model_registry
WHERE kind IN ('RULE', 'STATISTICAL') AND insight_type IS NOT NULL AND approval_status = 'APPROVED' AND is_enabled
  AND (CAST(:only AS text) IS NULL OR code = :only)
ORDER BY code
"""
)
_REGIONS = text(
    """
SELECT r.id, r.code, r.analysis_crs,
       (SELECT max((i.reported_at AT TIME ZONE 'Asia/Kolkata')::date) FROM core.incident_record i WHERE i.region_id = r.id) AS as_of
FROM core.organizational_unit r
WHERE r.unit_type = 'REGION' AND (CAST(:only AS text) IS NULL OR r.code = :only)
ORDER BY r.code
"""
)
_INSERT_RUN = text(
    "INSERT INTO core.ai_run (model_id, region_id, as_of, parameters, status) "
    "VALUES (:model, :region, :as_of, CAST(:params AS jsonb), 'RUNNING') RETURNING id"
)
_FINISH_RUN = text(
    "UPDATE core.ai_run SET status = :status, reason = :reason, input_summary = CAST(:summary AS jsonb), "
    "input_sha256 = :sha, insight_count = :count, finished_at = clock_timestamp() WHERE id = :id"
)
# One constant statement; the geometry kind (none, point, hexagon, circle) is a bound parameter.
_INSERT_CARD = text(
    """
INSERT INTO core.ai_insight (run_id, model_id, insight_type, dedup_key, region_id, station_id, geography, geometry,
    window_start, window_end, analytical_category_id, category_label, evidence, baseline, data_quality_score, exclusions,
    confidence, confidence_label, calibration_note, explanation, limitations, alternative_explanations, subject_incident_ids)
SELECT :run, :model, :type, :key, :region, :station, CAST(:geography AS jsonb),
    CASE CAST(:geom_kind AS text)
        WHEN 'point' THEN ST_SetSRID(ST_MakePoint(CAST(:lon AS float8), CAST(:lat AS float8)), 4326)
        WHEN 'hex' THEN core.hex_polygon(CAST(:q AS int), CAST(:r AS int), CAST(:size AS float8), CAST(:crs AS int))
        WHEN 'circle' THEN ST_Transform(ST_Buffer(ST_SetSRID(ST_MakePoint(CAST(:sx AS float8), CAST(:sy AS float8)),
                                                  CAST(:crs AS int)), CAST(:radius AS float8)), 4326)
    END,
    :ws, :we, :cat, :cat_label, CAST(:evidence AS jsonb), CAST(:baseline AS jsonb), :dq, CAST(:exclusions AS jsonb),
    :conf, :conf_label, :calibration, :explanation, CAST(:limitations AS text[]), CAST(:alternatives AS text[]),
    CAST(:incidents AS uuid[])
ON CONFLICT (model_id, dedup_key) DO NOTHING
RETURNING id
"""
)
_GEOMETRY_PARAMS = {k: None for k in ("lon", "lat", "q", "r", "size", "crs", "sx", "sy", "radius")}


def input_digest(inputs: list[Any]) -> str:
    return hashlib.sha256(json.dumps(sorted(inputs, key=str), default=str).encode()).hexdigest()


async def _insert_card(
    conn: AsyncConnection, run_id: UUID, model: Any, insight_type: str, region: Region, card: Card
) -> bool:
    kind, geo_params = card.geometry or (None, {})
    row = (
        await conn.execute(
            _INSERT_CARD,
            {
                "run": run_id,
                "model": model.id,
                "type": insight_type,
                "key": card.dedup_key,
                "region": region.id,
                "station": card.station_id,
                "geography": json.dumps(card.geography, default=str),
                "ws": card.window_start,
                "we": card.window_end,
                "cat": card.category_id,
                "cat_label": card.category_label,
                "evidence": json.dumps(card.evidence, default=str),
                "baseline": json.dumps(card.baseline, default=str),
                "dq": card.data_quality_score,
                "exclusions": json.dumps(card.exclusions, default=str),
                "conf": card.confidence,
                "conf_label": card.confidence_label,
                "calibration": card.calibration_note,
                "explanation": card.explanation,
                "limitations": card.limitations,
                "alternatives": card.alternatives,
                "incidents": card.incident_ids,
                "geom_kind": kind,
                **_GEOMETRY_PARAMS,
                **geo_params,
            },
        )
    ).one_or_none()
    return row is not None


INSIGHT_TYPES = {
    "dq-anomaly": "DQ_ANOMALY",
    "duplicate-suggest": "DUPLICATE",
    "geocode-suggest": "GEOCODE",
    "emerging-pattern": "EMERGING_PATTERN",
    "cluster-explain": "CLUSTER",
}


_SWITCHES = text(
    "SELECT (SELECT enabled FROM core.feature_flag WHERE key = 'ai_assistance') AS ai_on, "
    "(SELECT is_enabled AND approval_status = 'APPROVED' FROM core.model_registry WHERE id = :model) AS model_on"
)
_FAILED_RUN = text(
    "INSERT INTO core.ai_run (model_id, region_id, as_of, parameters, status, reason, finished_at) "
    "VALUES (:model, :region, :as_of, CAST(:params AS jsonb), 'FAILED', :reason, clock_timestamp()) RETURNING id"
)


async def _audit(engine: AsyncEngine, environment: str, request_id: str, **event: Any) -> None:
    await write_independent(
        engine,
        AuditEvent(action="AI_RUN", **event),
        AuditContext(request_id=request_id, environment=environment),
    )


async def run_detectors(
    engine: AsyncEngine, *, environment: str, region: str | None = None, model: str | None = None
) -> dict[str, int]:
    """Run every enabled, approved detector for each region. Returns new insights per model.

    ``engine`` connects as the crimemap_ai login (ai-worker container): every read, the runs, the
    insights and the AI_RUN audit events are written by that role. Both switches are checked again
    inside each run, so a switch-off takes effect at the next model/region. A failing detector is
    recorded (FAILED run, audited error) and the others still run."""
    async with engine.begin() as conn:
        enabled = (
            await conn.execute(
                text("SELECT enabled FROM core.feature_flag WHERE key = 'ai_assistance'")
            )
        ).scalar_one_or_none()
        if not enabled:
            log.info("ai_disabled")
            await _audit(
                engine,
                environment,
                "ai:run",
                result="DENY",
                reason="AI_DISABLED",
                object_type="ai_run",
            )
            return {}
        models = (await conn.execute(_MODELS, {"only": model})).all()
        regions = [
            Region(r.id, r.code, int(r.analysis_crs), r.as_of)
            for r in (await conn.execute(_REGIONS, {"only": region})).all()
            if r.as_of
        ]
    created: dict[str, int] = {}
    for m in models:
        detector = DETECTORS.get(m.code)
        if detector is None:
            continue
        for reg in regions:
            request_id = f"ai:run:{m.code}:{reg.code}"
            base = {
                "model": m.id,
                "region": reg.id,
                "as_of": reg.as_of,
                "params": json.dumps(m.parameters),
            }
            try:
                async with engine.begin() as conn:
                    switches = (await conn.execute(_SWITCHES, {"model": m.id})).one()
                    if not (switches.ai_on and switches.model_on):
                        skipped = True
                    else:
                        skipped = False
                        run_id = (await conn.execute(_INSERT_RUN, base)).scalar_one()
                        detected = await detector(conn, reg, dict(m.parameters))
                        new = 0
                        for card in detected.cards:
                            new += await _insert_card(
                                conn, run_id, m, INSIGHT_TYPES[m.code], reg, card
                            )
                        await conn.execute(
                            _FINISH_RUN,
                            {
                                "id": run_id,
                                "status": "COMPLETED",
                                "reason": None,
                                "summary": json.dumps(detected.summary, default=str),
                                "sha": input_digest(detected.inputs),
                                "count": new,
                            },
                        )
            except Exception as exc:
                log.exception("ai_detector_failed", extra={"model": m.code, "region": reg.code})
                failed_id = None
                try:
                    async with engine.begin() as conn:
                        failed_id = (
                            await conn.execute(_FAILED_RUN, {**base, "reason": type(exc).__name__})
                        ).scalar_one()
                finally:
                    await _audit(
                        engine,
                        environment,
                        request_id,
                        result="ERROR",
                        reason=type(exc).__name__,
                        object_type="ai_run",
                        object_id=str(failed_id) if failed_id else None,
                        after_meta={"model": f"{m.code}@{m.version}", "region": reg.code},
                    )
                continue
            if skipped:
                await _audit(
                    engine,
                    environment,
                    request_id,
                    result="DENY",
                    reason="AI_DISABLED",
                    object_type="ai_run",
                    after_meta={"model": f"{m.code}@{m.version}", "region": reg.code},
                )
                continue
            created[m.code] = created.get(m.code, 0) + new
            await _audit(
                engine,
                environment,
                request_id,
                result="ALLOW",
                object_type="ai_run",
                object_id=str(run_id),
                after_meta={
                    "model": f"{m.code}@{m.version}",
                    "region": reg.code,
                    "as_of": reg.as_of.isoformat(),
                    "insights": new,
                    "candidates": len(detected.cards),
                },
            )
    return created


class AiSettings(BaseSettings):
    """The ai-worker container's settings: the crimemap_ai login only (no other credentials)."""

    model_config = SettingsConfigDict(env_prefix="CRIMEMAP_AI_", extra="ignore")

    environment: Literal["DEMO", "SANDBOX", "PRODUCTION"] = "DEMO"
    database_url: SecretStr  # crimemap_ai login (asyncpg URL)
    interval_seconds: int = 24 * 3600


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crimemap_worker.ai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the enabled detectors once")
    run.add_argument("--region")
    run.add_argument("--model")
    sub.add_parser(
        "loop", help="run the detectors now and then every interval (ai-worker container)"
    )
    args = parser.parse_args(argv)
    configure_logging()
    settings = AiSettings()  # read from the environment

    async def once(region: str | None = None, model: str | None = None) -> dict[str, int]:
        engine = create_async_engine(settings.database_url.get_secret_value())
        try:
            return await run_detectors(
                engine, environment=settings.environment, region=region, model=model
            )
        finally:
            await engine.dispose()

    if args.cmd == "run":
        print(json.dumps(asyncio.run(once(args.region, args.model))))
        return 0
    while True:
        try:
            log.info("ai_detectors_run", extra={"insights": asyncio.run(once())})
        except Exception:
            log.exception("ai_loop_failed")
        time.sleep(settings.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
