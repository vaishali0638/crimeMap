# Phase 0 Audit — Findings and Fixes

**Date:** 2026-10-11 · **Scope:** every Phase 0 file (`README.md`, `.gitignore`, `docs/*.md`) checked
against `crimemap_master_build_prompt.md`. Phase 0 contains documentation only; there is no
application code, so findings are design defects, specification gaps, and document errors.
Severity reflects the impact the defect would have had if carried into implementation.

## 1. Findings

| ID | Severity | File(s) | Finding | Fix |
|---|---|---|---|---|
| F-01 | High | data-dictionary | Table `core.user` uses a PostgreSQL reserved word; every query and migration would need quoting and ORMs commonly break on it | Renamed to `core.app_user`; convention added |
| F-02 | High | data-classification, decision-log | Role names did not match the specification (`REGION_SUPV`); the district level of DISTRICT_OR_REGION_SUPERVISOR was missing; CRB_ANALYST and STATION_SUPERVISOR aliases not mapped | Added role-code table mapping all nine specification roles; district scope dimension added |
| F-03 | High | data-classification | ABAC model omitted **purpose**, which the specification requires (role + scope + classification + purpose) | Purpose attribute defined, required on exports/step-up, recorded in audit; `purpose` added to `audit_event` and `report_export` |
| F-04 | High | architecture, decision-log ADR-008 | Audit for a request written "in the same transaction": a denied or failing request that rolls back would erase the record of the attempt | Successful reads audited in-transaction (fail-closed); denials and errors audited in an independent transaction; test added |
| F-05 | High | information-architecture | Design tokens claimed WCAG 2.2 AA but measured: saffron `#C9822B` 2.92:1 (fails text and non-text), border `#D5D8DC` 1.33:1 (fails control boundaries), teal on surface-2 4.17:1 (fails text) | Added saffron-strong `#8F5A16`, control border `#7A828B`, teal-text `#246A67`; usage rules stated; automated contrast test added to test plan |
| F-06 | Medium | integration-contracts | ICJS missing from source list; CCTV/ICCC marked "out of scope permanently", contradicting the specification's adapter requirement; non-existent record type used | ICJS row added; CCTV/ICCC as non-identifying metadata stubs; facial recognition remains permanently prohibited; record types corrected |
| F-07 | Medium | data-classification, ADR-020 | DATA_STEWARD could not see exact locations, yet must review geocoding confidence | Exact (non-residential) location allowed only under purpose `DQ_REVIEW` |
| F-08 | Medium | architecture | Action state machine had no RETURNED state (required by E2E-5) and ambiguous notation (`REJECTED → IN_PROGRESS`) | Rewritten with explicit transitions and RETURNED |
| F-09 | Medium | threat-model | Encryption requirements (TLS 1.3, AES-256 at rest, envelope/KMS, keys per environment and classification, backup encryption) absent | Encryption and key-management section added |
| F-10 | Medium | threat-model | Required alerts (mass export, unusual geography, repeated denials, privilege change, ingest/model failure) not listed; request-ID propagation to worker/data jobs not specified | Detection and alerting section with triggers; correlation path documented |
| F-11 | Medium | data-classification | No retention/deletion rules by record type and classification; no statement on location-history tracking | Retention section and explicit "no location-history tracking" rule added |
| F-12 | Medium | data-dictionary | IncidentRecord lacked the specification's `geometry_id` and `access_classification`; status declared as an enum that "varies per record type" (not possible); referenced tables undefined | Fields added/renamed; `status_definition` and `retention_policy` tables added; confidence CHECK constraint |
| F-13 | Medium | data-classification, api | No permission let AUDITOR view the model registry, which the specification grants | `ai.model.read` added; `ai.config` limited to kill switch |
| F-14 | Low | information-architecture | Home Map lacked PY Dashboard and Analytics buttons; PY sidebar lacked back button; Trends lacked export/logging notice; Civic neutral wording, AI calibration line, Overview KPIs missing | Added |
| F-15 | Low | information-architecture | 1920 px, tablet, limited mobile officer workflows, Tamil i18n, and "no seals/logos" not specified | Added |
| F-16 | Low | information-architecture | "Noto Sans (Latin + Tamil coverage)" is inaccurate (Noto Sans has no Tamil glyphs); font source unspecified (a font CDN would be a third-party call) | Noto Sans Tamil fallback; self-hosted fonts |
| F-17 | Low | api, threat-model, phase-0-report | Cited RFC 7807, which RFC 9457 obsoletes | Updated to RFC 9457 |
| F-18 | Low | api | Required security headers (HSTS, CSP frame-ancestors, nosniff, Referrer-Policy) not specified | Header convention added |
| F-19 | Low | decision-log | ADR-003 lacked refresh-token rotation; ADR-017 said "warm cache excluded" and "reported separately" in one sentence | Rotation with reuse detection; target defined on uncached requests |
| F-20 | Low | architecture | Stack list omitted the specified analytics libraries and the pgvector position; TLS 1.3 not stated | Added |
| F-21 | Low | README | Infrastructure path differed from specification (`terraform-or-opentofu`); script names missing; spec-required README sections not acknowledged | Fixed; note that setup sections arrive in Phase 1 |
| F-22 | Low | information-architecture | 33 wireframe rows misaligned; emoji with ambiguous width | All boxes regenerated to exact widths; emoji replaced with text |
| F-23 | Low | requirements-traceability | No rows for purpose, alerts, correlation, KMS, retention, location tracking, i18n, responsive, branding, adapter list | 11 rows added |
| F-24 | Low | acceptance-test-plan | PD-4 missed the export/logging notice; no tests for purpose binding, correlation, rollback-safe denial audit, token contrast | Added |

No secrets, credentials, or real personal data were found in any file.

## 2. Checks run (after fixes)

Results are recorded in [phase-0-report.md](phase-0-report.md) §4. Checks are custom scripts because
no Markdown linter or Mermaid renderer is installed locally.

## 3. Not verified / blocked

| Item | Status | Reason |
|---|---|---|
| Mermaid diagrams render | **Blocked** | No Mermaid renderer installed; installing `@mermaid-js/mermaid-cli` downloads a headless browser. Reviewed manually only |
| markdownlint | **Blocked** | Not installed; equivalent structural checks run instead |
| Vendor-specific claims (Keycloak refresh-token settings, PostGIS function availability, RFC numbering) | Not independently verified | Based on maintained knowledge; the context7 documentation tool is unauthenticated. To be confirmed against vendor docs at Phase 1 scaffold time |
| Proposed ADRs (004, 006, 012, 013, 015, 017, 018, 020) | Pending owner | Defaults applied, awaiting confirmation |
