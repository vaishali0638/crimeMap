# Integration Contracts (Future, Approved Sources Only)

> **No government system is integrated.** CRIMEMAP has no access to CCTNS, ERSS-112, ICJS, CCTV, ICCC,
> eDAR, e-challan, or any other government system, and does not simulate, scrape, or assume such
> access. This document defines the contract an approved adapter must satisfy **after** the
> prerequisites in section 1 are met. Until then, only the DEMO adapter is enabled.

## 1. Prerequisites before any adapter is enabled

All must be true and recorded against the `source_system` row (`approved_status = APPROVED`):

1. Written authorisation from the data owner.
2. Legal review and a data-sharing agreement defining purpose, fields, retention, and onward use.
3. Security review of the connection (network path, authentication, encryption, credential handling).
4. Data classification of every field received.
5. Named accountable owner on both sides.
6. Approved credentials issued to CRIMEMAP (never guessed, shared, or reused) and stored in the secrets
   manager with a rotation schedule.
7. Test run against a non-production source environment with synthetic data.
8. Sign-off recorded as an audit event by SYSTEM_ADMIN with a second approver.

The worker refuses to run an adapter whose source system is not `APPROVED` (or `DEMO` for the demo
adapter), and PRODUCTION mode refuses to run the demo adapter.

## 2. Adapter interface

```python
class SourceAdapter(Protocol):
    source_code: str                       # e.g. "DEMO_SYNTHETIC"
    def health_check(self) -> HealthStatus: ...
    def fetch_batch(self, cursor: str | None) -> Batch: ...      # pull; or receive() for push
    def verify(self, batch: Batch) -> VerificationResult: ...    # signature / checksum / schema version
    def to_canonical(self, raw: RawRecord) -> CanonicalRecord: ...
```

- Adapters are **read-only** toward the source. CRIMEMAP never writes back to a source system.
- Adapters never bypass the pipeline: output goes to `staging.raw_ingest` and continues through
  validation, idempotency, mapping, enrichment, data-quality rules, lineage, and audit.

## 3. Canonical record contract (v1)

| Field | Required | Notes |
|---|---|---|
| `source_system` | yes | code |
| `source_record_reference` | yes | stable ID in the source |
| `source_version` | yes | monotonically increasing or source timestamp |
| `record_type` | yes | CASE / ERSS_CALL / ACCIDENT / TRAFFIC_EVENT / CIVIC_CONTEXT |
| `official_offence_act` / `section` / `name` | CASE only | as provided by the source; not interpreted |
| `occurrence_start_at`, `occurrence_end_at` | optional | ISO 8601 with offset |
| `reported_at` | yes | |
| `reporting_station_code` | yes | mapped to `organizational_unit` |
| `status` | optional | mapped via configured status table |
| `location` | optional | `{lat, lon}` or `{text}`; precision and method if known |
| `sensitivity_classification` | yes | assigned by source or by field-level mapping |
| `restricted` | optional | sensitive fields routed only to the `restricted` schema |

Unknown fields are dropped and counted (data minimisation). Personal identifiers are accepted only if
the data-sharing agreement names them, and are routed to `restricted`.

## 4. Delivery guarantees

| Property | Requirement |
|---|---|
| Idempotency | `idempotency_key = sha256(source_system ‖ source_record_reference ‖ source_version)` |
| Ordering | Not assumed; newer `source_version` supersedes older; older arrivals stored as history |
| Retries | Exponential backoff for transient failures (database/network): as built, Dramatiq retries 5 times from 10 s doubling to at most 10 min; files stay in the inbox until processed. A batch that can never succeed as delivered is marked FAILED, audited and filed under `failed/`; corrected re-delivery is processed. |
| Failures | Dead-letter with error code; never silently dropped |
| Raw data | Immutable; no destructive overwrite |
| Reconciliation | Daily report per source/day (`reconcile_yesterday`): declared = accepted + duplicates + rejected, audited as `RECONCILIATION_REPORT`; an imbalance opens DQ-REC-001. Per-station breakdown and comparison with separately supplied source totals need a real source and are not built. |
| Health | Source health check every 5 minutes (`check_sources`); stale threshold per source (`sync_policy.stale_after_hours`) |

## 5. Source-specific notes (placeholders only)

| Source | Expected record type | Status |
|---|---|---|
| CCTNS (or authorised case system) | CASE | **Not authorised — interface stub only** |
| ERSS-112 | ERSS_CALL | **Not authorised — interface stub only** |
| Accident / traffic systems (e.g. eDAR, e-challan) | ACCIDENT, TRAFFIC_EVENT | **Not authorised — interface stub only** |
| Civic infrastructure (verified, non-sensitive) | CIVIC_CONTEXT | **Not authorised — feature flag off** |
| ICJS (Inter-operable Criminal Justice System) | CASE status / court-stage metadata only | **Not authorised — interface stub only** |
| CCTV / ICCC | OPERATIONAL_OBSERVATION / CIVIC_CONTEXT (e.g. camera asset location, verified event timestamp) | **Not authorised — interface stub only**; limited to non-identifying metadata. No video, images, or biometric data are ingested |
| Facial recognition or any person-identification feed | — | **Prohibited permanently** (specification safety boundaries 2 and AI prohibited uses) |

## 6. Security requirements for connectors

- mTLS or signed payloads; no plaintext transport.
- Credentials from the secrets manager only, rotated on schedule; never in code, images, or logs.
- Egress allow-list per adapter; no user-supplied URLs.
- Payload size and rate limits; schema version pinned; unknown schema versions rejected to dead letter.
- Every batch produces an audit event (source, count, accepted, rejected, duplicates).
