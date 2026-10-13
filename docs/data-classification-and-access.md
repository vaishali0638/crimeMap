# Data Classification and Authorisation Matrix

> Phase 0 design. This matrix is the single source of truth for the policy engine (ADR-002): in Phase 1
> it becomes a machine-readable policy table, and tests are generated from it.

## 1. Classification levels

| Level | Contents | Default handling |
|---|---|---|
| **PUBLIC** | No case-level data. Only explicitly approved public material (e.g. a published annual total). | May be shown to any authenticated user. Never assumed — must be explicitly approved. |
| **INTERNAL** | Non-sensitive operational metadata: station names, boundary versions, feed health, category definitions. | Any authenticated CRIMEMAP user. |
| **RESTRICTED** | Case-level analytical details: category, offence heading/section, times, locality, reduced-precision location, station, status; pattern reviews; actions. | Role + territory scope required. Banner: "RESTRICTED — Internal Police Use Only". |
| **HIGHLY_RESTRICTED** | Victim/witness identity references, exact residential locations, sensitive investigation markers, protected data. | Stored only in the `restricted` schema. **No role has access by default.** Requires an explicit grant, a recorded purpose, and fresh re-authentication; every access audited. Never in list previews, exports, AI prompts, aggregates, or map popups. |

Rules:
- A record's classification is the **highest** classification of any field it contains in that view.
- Aggregates inherit RESTRICTED unless approved as INTERNAL; small cells are suppressed (ADR-012).
- Exports carry the classification of their contents and a watermark.
- Sensitive categories (crimes against women, crimes against children, and any steward-flagged
  category) are aggregate-only for roles without `incident.sensitive_category.read`.

## 2. Scope dimensions

Every permission grant is scoped by one or more of:

| Dimension | Examples |
|---|---|
| Region | Puducherry, Karaikal, Mahe, Yanam |
| Station | one or more fictional stations |
| Beat | one or more beats |
| Classification ceiling | INTERNAL, RESTRICTED (HIGHLY_RESTRICTED only via explicit grant) |
| Record type | CASE, ERSS_CALL, ACCIDENT, TRAFFIC_EVENT, CIVIC_CONTEXT, OPERATIONAL_OBSERVATION |
| Action type | patrol review, inspection request, awareness activity, etc. |
| Export scope | none, aggregate CSV, PDF briefing, map image |
| District | district / subdivision units above station level (for DISTRICT_OR_REGION_SUPERVISOR) |

**Purpose (ABAC attribute).** The specification's ABAC model is role + geographic scope + record
classification + **purpose**. Purpose is a controlled value (`OPERATIONAL_REVIEW`, `DQ_REVIEW`,
`PATTERN_ANALYSIS`, `BRIEFING`, `OUTCOME_REVIEW`, `AUDIT`) declared per session context and required
explicitly on exports, step-up actions, and any future `restricted.read` grant. The policy engine
checks that the declared purpose is permitted for the role and the permission (e.g. exact coordinates
for DATA_STEWARD only under `DQ_REVIEW`), and every audit event records it.

Scope resolution: a request is allowed only if **every** requested filter value lies inside the
subject's scope. Out-of-scope filters are **denied** (HTTP 403, audited), not silently narrowed, so
users are never misled about what a count covers. Out-of-scope resource IDs return 404.

## 3. Permission codes (initial catalogue)

| Code | Meaning |
|---|---|
| `map.aggregate.read` | View grid/hex/beat aggregates |
| `map.points.read` | View individual record markers (reduced precision) |
| `incident.location.exact` | Receive exact coordinates (still not for HIGHLY_RESTRICTED/residential) |
| `incident.summary.read` | Open the record popup (permitted fields) |
| `incident.detail.read` | Open permitted details view |
| `incident.sensitive_category.read` | See sensitive-category records at case level |
| `restricted.read` | Read HIGHLY_RESTRICTED fields (explicit grant + purpose + re-auth) |
| `opsrecord.create` | Create operational records (note, correction request, marker, observation) |
| `opsrecord.approve` | Approve operational records that require review |
| `dq.manage` | Manage data-quality issues, category mappings, geocode reviews |
| `analytics.read` | Use the Analytics Workbench |
| `analytics.compare` | Comparison section |
| `ai.insight.read` / `ai.insight.review` | View / validate or dismiss AI insights |
| `ai.model.read` | View the model registry and model cards |
| `ai.config` | Operate the AI kill switch. Model registry entries change only through the reviewed release and approval process (Phase 7), not through this permission |
| `pattern_review.create` | Create and submit pattern reviews |
| `pattern_review.decide` | Approve / return / reject pattern reviews |
| `action.propose` / `action.approve` / `action.complete` | Operational action workflow |
| `outcome.review` | Create and finalise outcome reviews |
| `report.export.aggregate_csv` / `report.export.pdf` / `report.export.map_image` | Exports |
| `report.read_approved` | Read approved reports |
| `audit.read` | Read audit trail |
| `lineage.read` | Read data lineage and source health |
| `admin.config` | System configuration, boundary versions, adapters |
| `admin.users` | Manage users, roles, scopes |

## 4. Role → permission matrix

Role codes (machine names used by the policy engine and IdP), mapped to the specification's role names:

| Column | Role code | Specification role |
|---|---|---|
| SYS_ADMIN | `SYSTEM_ADMIN` | SYSTEM_ADMIN |
| DATA_STEWARD | `DATA_STEWARD` | DATA_STEWARD / CRB_ANALYST |
| CRIME_ANALYST | `CRIME_ANALYST` | CRIME_ANALYST |
| STATION_OFFICER | `STATION_OFFICER` | STATION_OFFICER |
| SHO | `SHO` | SHO / STATION_SUPERVISOR |
| DR_SUPV | `DISTRICT_OR_REGION_SUPERVISOR` | DISTRICT_OR_REGION_SUPERVISOR |
| SENIOR_LEAD | `SENIOR_LEADERSHIP` | SENIOR_LEADERSHIP |
| AUDITOR | `AUDITOR` | AUDITOR |
| DEMO_USER | `DEMO_USER` | DEMO_USER |

Legend: **S** = within assigned station/beat · **R** = within assigned district(s) or region(s) ·
**A** = aggregate-only · **✓** = allowed (within scope) · **–** = denied · **⊕** = re-authentication
required at the time of use · **DQ** = only with declared purpose `DQ_REVIEW`.

| Permission | SYS_ADMIN | DATA_STEWARD | CRIME_ANALYST | STATION_OFFICER | SHO | DR_SUPV | SENIOR_LEAD | AUDITOR | DEMO_USER |
|---|---|---|---|---|---|---|---|---|---|
| map.aggregate.read | – | R | R | S | S | R | ✓ (A) | – | ✓ (A) |
| map.points.read | – | R | R | S | S | R | – | – | – |
| incident.location.exact | – | R (DQ) | R | – | S | R | – | – | – |
| incident.summary.read | – | R | R | S | S | R | – | – | – |
| incident.detail.read | – | R (DQ fields) | R | S | S | R | – | – | – |
| incident.sensitive_category.read | – | – | R | – | S | R | – | – | – |
| restricted.read | – | – | – | – | – | – | – | – | – |
| opsrecord.create | – | R | R | S | S | R | – | – | – |
| opsrecord.approve | – | R (corrections) | – | – | S | R | – | – | – |
| dq.manage | – | R | – | – | – | – | – | – | – |
| analytics.read | – | R (DQ section) | R | – | S | R | A | – | A |
| analytics.compare | – | – | R | – | S | R | A | – | A |
| ai.insight.read | – | R (DQ insights) | R | – | S | R | – | ✓ | – |
| ai.insight.review | – | R (DQ insights) | R | – | – | – | – | – | – |
| ai.model.read | ✓ | – | R | – | – | – | – | ✓ | – |
| ai.config | ✓ (kill switch only) | – | – | – | – | – | – | – | – |
| pattern_review.create | – | – | R | – | S | R | – | – | – |
| pattern_review.decide | – | – | – | – | S | R | – | – | – |
| action.propose | – | – | R | – | S | R | – | – | – |
| action.approve | – | – | – | – | S | R | – | – | – |
| action.complete | – | – | – | S (assigned) | S | – | – | – | – |
| outcome.review | – | – | R | – | S | R | – | – | – |
| report.export.aggregate_csv | – | – | R ⊕ | – | S ⊕ | R ⊕ | – | – | – |
| report.export.pdf | – | – | R ⊕ | – | S ⊕ | R ⊕ | – | – | – |
| report.export.map_image | – | – | R ⊕ | – | S ⊕ | R ⊕ | – | – | – |
| report.read_approved | – | – | R | – | S | R | ✓ | ✓ | – |
| audit.read | ✓ (metadata) | – | – | – | – | – | – | ✓ | – |
| lineage.read | ✓ | R | – | – | – | – | – | ✓ | – |
| admin.config | ✓ | – | – | – | – | – | – | – | – |
| admin.users | ✓ | – | – | – | – | – | – | – | – |

Notes:
- `restricted.read` is granted to **no** role by default; it is an individual, time-bound grant with a
  recorded purpose (e.g. an investigating officer for a specific case) and is out of scope for the demo.
- SYSTEM_ADMIN audit access is metadata only (who/what/when), not case content in before/after payloads.
- AUDITOR sees audit trails, lineage, model registry and approved reports — no case content by default.
- The matrix is deliberately conservative; widening any cell requires an ADR.

## 5. Separation of duties

| Rule | Enforcement |
|---|---|
| A user cannot approve a pattern review or action they created | Service-level check + test |
| Role and scope changes cannot be made by the affected user | Service-level check + alert |
| Boundary version approval requires SYSTEM_ADMIN and DATA_STEWARD (dual control) | Workflow state |
| Manual data reconciliation requires a second reviewer | Workflow state |
| AI cannot propose, approve, export, or modify | No write endpoints callable by AI module |

## 6. Field-level masking (record popup and lists)

| Field | STATION_OFFICER | SHO / DR_SUPV / ANALYST | DATA_STEWARD | Aggregate roles |
|---|---|---|---|---|
| Internal source reference | ✓ | ✓ | ✓ | – |
| Analytical category | ✓ | ✓ | ✓ | aggregated |
| Official offence heading / section | ✓ | ✓ | ✓ | – |
| Occurrence / report time | ✓ (hour) | ✓ | ✓ | aggregated by bucket |
| Locality text | ✓ | ✓ | ✓ | – |
| Coordinates | 250 m snapped | exact (non-residential) | 250 m snapped; exact (non-residential) only under `DQ_REVIEW` for geocode review | – |
| Location confidence | ✓ | ✓ | ✓ | summary only |
| Station, region, status | ✓ | ✓ | ✓ | aggregated |
| Narrative / free text | – (not stored in demo) | – | – | – |
| Victim / witness references | – | – | – | – |

## 7. Re-authentication and step-up triggers

- Any export (CSV, PDF, map image).
- Granting or using `restricted.read` (future).
- Role/scope changes, boundary approvals, AI kill switch.
- Sessions idle > 15 minutes; absolute session lifetime 8 hours (configurable).

## 8. Retention, deletion, and tracking limits

- Retention is configurable per record type and classification (`retention_policy` table: record
  type, classification, retention period, action at expiry = `ARCHIVE` / `DELETE` / `REVIEW`). Expiry
  jobs run in the worker, are audited, and never delete audit events before the audit retention minimum
  (180 days baseline; production value set by policy). Demo defaults are placeholders, not legal advice.
- Exports expire (default 24 hours) and their objects are deleted from object storage at expiry.
- **No location-history tracking** of officers or citizens: CRIMEMAP does not collect device location,
  patrol GPS traces, or per-person movement data. Adding any such capability requires separate
  approval, documentation, and an ADR.
- Audit events record user IP/device metadata only where lawful; the default stores IP address and a
  hash of the user agent, configurable off.

## 9. Demo users (Phase 1 seed)

One fictional user per role, with clearly fictional names (e.g. `demo.analyst.py`,
`demo.officer.karaikal-north`). Scopes reference fictional stations. Credentials are generated at
setup time and stored only in the local, git-ignored `.env`; they are never committed or printed in
documentation.
