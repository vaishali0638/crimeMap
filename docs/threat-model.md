# CRIMEMAP Threat Model (STRIDE)

> Phase 0 design-time threat model. It must be re-reviewed at the end of every phase and before any
> real data source is connected. Mitigation status is **Planned** throughout — nothing is implemented.

## 1. Scope

The CRIMEMAP web application, API, worker, database, cache, object store, identity provider, basemap
hosting, CI/CD pipeline, and future data-source adapters. Out of scope: source systems (CCTNS,
ERSS-112, etc.), which are not connected.

## 2. Assets

| ID | Asset | Classification | Why it matters |
|---|---|---|---|
| A1 | Victim/witness identity, residential locations, sensitive investigation details | HIGHLY_RESTRICTED | Exposure can endanger people and investigations |
| A2 | Case-level analytical records (category, time, location, station, status) | RESTRICTED | Operational intelligence; can reveal patrol and investigative focus |
| A3 | Aggregates, trends, reports | INTERNAL / RESTRICTED | Can reveal individuals if cells are small |
| A4 | Audit log | RESTRICTED | Evidence of access; target for cover-up |
| A5 | Credentials, tokens, signing keys, connector secrets | Secret | Full compromise path |
| A6 | Pattern reviews, approved actions, outcome reviews | RESTRICTED | Reveal planned operations |
| A7 | Model registry, AI insight history | INTERNAL | Accountability for AI-assisted decisions |
| A8 | Boundary data and configuration | INTERNAL | Wrong jurisdictions mis-assign records and permissions |

## 3. Actors

| Actor | Trust | Notes |
|---|---|---|
| Authorised user acting within role | Trusted, verified | Still bound by scope and audit |
| **Curious or malicious insider** | Partially trusted | **Primary threat**: browsing outside scope, bulk export, stalking a person via locations |
| External attacker with stolen credentials | Untrusted | Phishing, token theft, password reuse |
| External attacker without credentials | Untrusted | Internet-facing surface if exposed |
| Compromised dependency / CI | Untrusted | Supply-chain risk |
| Compromised or misbehaving data source | Untrusted input | Malformed or malicious records, injection payloads in text fields |
| System administrator | Privileged | Must not read case data by default (separation of duties) |

## 4. Trust boundaries

1. Browser ↔ Next.js BFF (internet / intranet boundary)
2. BFF ↔ FastAPI (service boundary; API re-validates everything)
3. FastAPI ↔ PostgreSQL (RLS is the last authorisation gate)
4. Worker ↔ data sources / object store (untrusted input enters here)
5. CI/CD ↔ deployment environment
6. Application ↔ any external service (basemap, LLM) — minimised to none by default

## 5. STRIDE analysis

| ID | Category | Threat | Mitigation (planned) | Phase | Verified by |
|---|---|---|---|---|---|
| S1 | Spoofing | Stolen password used to log in | OIDC with mandatory TOTP MFA for case-data and privileged roles; lockout and rate limit at IdP and BFF | 1 | E2E login tests; MFA claim test |
| S2 | Spoofing | Session/token theft via XSS | Tokens kept server-side (BFF); HttpOnly, Secure, SameSite=Strict cookie; strict CSP | 1 | Header tests; ZAP baseline |
| S3 | Spoofing | Forged JWT or wrong audience | Signature, issuer, audience, expiry and `azp` validation; JWKS pinned to IdP URL | 1 | Unit tests with forged tokens |
| S4 | Spoofing | Spoofed data source sending records | Adapter-level signature/mTLS (future); demo adapter reads local files only | 2 | Adapter contract tests |
| T1 | Tampering | Audit log edited to hide access | Insert-only grants, modification-rejecting trigger, hash chain, daily verification, off-host log shipping (prod) | 1 | Test that UPDATE/DELETE fail; chain verification test |
| T2 | Tampering | Raw source data overwritten | Immutable staging, versioned records, change detection | 2 | Re-ingest tests |
| T3 | Tampering | Official FIR/case data altered via CRIMEMAP | No create/update endpoints for source records; corrections are separate requests | 3 | API contract test asserting no such routes; E2E |
| T4 | Tampering | SQL injection | SQLAlchemy parameter binding only; raw SQL reviewed and parameterised; semgrep rules | 1+ | SAST; Schemathesis fuzz |
| T5 | Tampering | CSRF on state-changing requests | CSRF token + Origin check in BFF; SameSite=Strict; rejections emitted as structured BFF security events (they never reach the API audit log) | 1 | CSRF tests; live browser check |
| T6 | Tampering | Boundary data altered to widen a user's scope | Boundary versions require approval by SYSTEM_ADMIN + DATA_STEWARD (dual control); audited | 2 | Workflow tests |
| R1 | Repudiation | User denies viewing or exporting records | Audit event per sensitive read and export with request ID, user, scope, filters; watermark with user and time | 1, 5 | Audit assertions in E2E |
| R2 | Repudiation | Supervisor denies approving an action | Approval records actor, timestamp, rationale; separation of duties (no self-approval) | 6 | State-transition tests |
| I1 | Info disclosure | IDOR: request another station's record by ID | Policy check on every resource load + RLS; 404 for out-of-scope IDs (no existence leak); denial audited | 3 | Authorisation matrix tests per role × endpoint |
| I2 | Info disclosure | Small aggregate counts identify an individual | Small-cell suppression with secondary suppression (ADR-012) | 4, 5 | Property tests on suppression |
| I3 | Info disclosure | Exact residential location revealed on map | Precision reduction; HIGHLY_RESTRICTED and residential-flag records aggregate-only (ADR-020) | 3 | Role-based popup/feature tests |
| I4 | Info disclosure | Cache returns data across scopes | Cache key includes scope hash + classification ceiling; no shared CDN caching of API responses (`Cache-Control: private, no-store`) | 3 | Cache-key tests |
| I5 | Info disclosure | Basemap requests reveal viewing areas to third party | Self-hosted PMTiles (ADR-006) | 3 | Network test: no third-party origins in CSP/requests |
| I6 | Info disclosure | Case data sent to external LLM | LLM disabled; provider allow-list; redaction; restricted classes blocked | 7 | AI policy tests |
| I7 | Info disclosure | Bulk exfiltration via exports or paging | Export permission + re-auth; aggregate-only CSV; row/size limits; mass-access alerts; watermark | 5, 8 | Export denial E2E; alert tests |
| I8 | Info disclosure | Verbose errors or logs leak data/secrets | RFC 9457 problem-details errors without internals; log redaction filter; no secrets in logs | 1 | Log redaction tests; gitleaks |
| I9 | Info disclosure | Secrets committed to repository or images | `.gitignore`, gitleaks in CI, secrets via environment/secret manager, images scanned | 1 | CI gate |
| I10 | Info disclosure | SYSTEM_ADMIN or AUDITOR reads case content | Separate permission sets; admin role has no case permissions | 1 | Role matrix tests |
| I11 | Info disclosure | Demo data contains real personal data | Synthetic generator with no real names/addresses/phone numbers; scanner check on seed output | 2 | Seed validation test |
| D1 | Denial of service | Unbounded spatial/analytics query | Max bbox, max period, page size limits; `statement_timeout`; worker offload | 3, 5 | Performance tests |
| D2 | Denial of service | Request floods / login brute force | Redis rate limiting per user, per IP, and per IP for authentication failures; Keycloak brute-force lockout. The limiter **fails open** if Redis is unreachable (availability over throttling; error logged) — authentication and authorisation are unaffected | 1 | Rate-limit tests |
| D3 | Denial of service | Malicious upload (zip bomb, huge file) | Size limits, MIME allow-list, magic-byte check, quarantine, malware scan | 3 | Upload security tests |
| E1 | Elevation | User edits own role or scope | Role/scope changes only by SYSTEM_ADMIN via audited endpoint; alert on privilege change | 1 | Authorisation tests |
| E2 | Elevation | Mass assignment (extra fields in request body) | Pydantic models with `extra="forbid"`; separate create/update DTOs | 1 | Contract tests |
| E3 | Elevation | RLS bypass via owner or superuser connection | API uses non-owner role without BYPASSRLS; `FORCE ROW LEVEL SECURITY` | 2 | DB test connecting as app role |
| E4 | Elevation | SSRF via tile URL, import URL, or report renderer | Allow-listed outbound hosts; no user-supplied URLs fetched; renderer has no network access | 3, 5 | SSRF tests |
| E5 | Elevation | Compromised dependency or CI workflow | Pinned dependencies and actions, SCA, SBOM, least-privilege CI tokens | 1 | CI gate |
| AI1 | AI-specific | Prompt injection through narrative text | No LLM by default; AI has no write tools; any future prompt treats record text as data | 7 | AI policy tests |
| AI2 | AI-specific | Automation bias — users act on AI suggestions without review | Mandatory "Analyst review required", evidence and limitations, validation before any action | 7 | Workflow tests |
| AI3 | AI-specific | Model used for prohibited purpose (person ranking) | No person-level features in analytical tables; API has no person-ranking endpoints; refusal rules | 7 | Contract tests |

## 6. Encryption and key management (planned)

| Control | Demo / local | Production requirement |
|---|---|---|
| In transit | TLS on the NGINX entry point with a local development certificate; internal Compose traffic is on a private network | TLS 1.3 at the load balancer/NGINX; TLS between services where traffic leaves the host |
| At rest | Host disk only (residual risk, §9) | AES-256 managed database and object-storage encryption |
| Highly restricted fields and files | Synthetic tokens only | Envelope encryption with a KMS; data keys per classification |
| Key separation | Separate keys per environment from the first scaffold | Separate keys per environment **and** per data classification; rotation schedule |
| Backups | Encrypted (GnuPG AES-256) with a manifest; restore test automated (Phase 8, ADR-039) | Managed encrypted backups with PITR; restore tested on a schedule |
| Secrets | Local git-ignored `.env`; `.env.example` placeholders only | Secrets manager; nothing in source, images, frontend bundle, logs, or errors |

## 7. Detection and alerting (Phase 8: signals and rules built; routing not built)

Alerts required by the specification, each raised from audit events or job metrics:

| Alert | Trigger (initial threshold, configurable) |
|---|---|
| Mass export | More than N exports or more than M aggregated rows per user per day |
| Unusual geographic access | Requests touching scope at the edge of, or outside, a user's usual stations/regions; repeated out-of-scope attempts |
| Repeated denied access | ≥ 5 `AUTHZ_DENIED` events for one user within 10 minutes |
| Privilege changes | Any role or scope grant/revocation (always alerts) |
| Failed ingestion | Dead-letter growth, failed batch, stale source beyond threshold |
| Model failures | AI job errors, model disabled by kill switch, evaluation drift |
| Audit chain broken | Daily hash-chain verification failure |

Phase 8 status (ADR-038): signals for mass export, first-time region access, denials, privilege and
configuration changes, ingestion and AI failures are computed from the audit log and exposed on
`/metrics`; Prometheus rules exist for each (`infrastructure/observability/alerts.yml`). Not built:
alert routing, the rows-per-day export threshold, model drift, and an alert on the chain-verification
log event (needs log aggregation). Playbooks: [incident-response.md](incident-response.md).

Correlation: the `request_id` generated at the BFF is propagated to the API, written to every audit
event, passed into Dramatiq message metadata, and included in worker logs and data-job audit events.

## 8. Abuse cases to test explicitly

1. A STATION_OFFICER changes `station_id` in a URL or filter to a neighbouring station → denied (403 on
   filters, 404 on resource IDs) and audited.
2. A user pages through every record to rebuild the dataset → paging limits and mass-access alert.
3. A user requests a tiny polygon around one house to read its count → suppression and precision rules.
4. SENIOR_LEADERSHIP subtracts two aggregates to recover a suppressed cell → secondary suppression.
5. An export link is shared outside the organisation → short-lived signed URL bound to the requesting
   session; expired links rejected.
6. A user submits an operational record whose text contains script tags → stored as text, rendered
   escaped; CSP blocks inline script.

## 9. Residual risks (accepted for DEMO only)

| Risk | Reason | Required before production |
|---|---|---|
| No independent penetration test | Not available in hackathon | External security audit |
| Encryption at rest relies on host disk | Local Docker | Managed database/storage encryption + KMS |
| Single-host deployment | Demo scope | HA design with hosting owner |
| No formal legal/privacy review | Not available | Legal review, DPIA-equivalent, data-sharing agreements |
| Authorised insider misuse within scope | Cannot be fully prevented technically | Audit review process, alerts, policy and training |
