# Phase 2 Audit — Findings, Fixes and Verification

**Date:** 2026-10-12 · **Scope:** every Phase 2 source file and configuration, against the Phase 2
section and the ingestion/security requirements of `crimemap_master_build_prompt.md`.
**Method:** three independent read-only reviews (data layer, API security, web/infra/docs), each
finding verified against the code and fixed test-first; my own spec-gap check; mutation checks on
the regression tests; live re-verification on the Compose stack. Nothing has been committed and CI
has not run on GitHub (no remote).

## 1. Summary

| | Count |
|---|---|
| Findings raised: 16 data layer, 10 API, 18 web, infrastructure and docs, 8 own checks | 52 |
| Fixed (two web findings duplicate data/API ones and share their fix) | 46 |
| Partly fixed, remainder open (M3, L3, F9) | 3 |
| Not changed: documented or accepted per the classification doc (F10, M9, L5) | 3 |

All Phase 2 acceptance criteria still hold after the fixes (§4). **One finding needs a reviewer
decision:** F1 — how the DQ queue reconciles with "sensitive categories aggregate-only" (ADR-028
amendment).

## 2. Findings and status

Severity is the reviewer's. "Test" names the regression test (in `services/api/tests/` unless noted).

### Data layer

| ID | Sev. | Finding | Status | Test / evidence |
|---|---|---|---|---|
| H1 | High | Lax-typed values (`"2"`, `2.0`, `2**40` versions, numeric flags) passed validation, then aborted the whole batch in SQL | **Fixed**: strict contract; pipeline reads a validated `canonical` column | `test_a_lax_typed_record_is_dead_lettered…` (3 cases), `unit/test_contract.py` (7 new) |
| H2 | High | A failing batch was never recorded; one bad file stopped the directory and skipped the health check | **Fixed**: FAILED row + `INGEST_FAILED` audit in a separate transaction; run continues; re-delivery processes | `test_a_failing_batch_is_recorded…` |
| H3 | High | Exact residential locations in the analytical store, readable by the API role | **Fixed**: generalised ~250 m point inside the beat, exact point only in `restricted`, CHECK constraint; live data converted (ADR-031) | `test_residential_locations_are_generalised…`, `test_no_exact_residential_point_remains…`; mutation-checked |
| H4 | High | `core.source_record_link` had no RLS | **Fixed**: RLS follows incident visibility | `test_lineage_links_are_hidden…` |
| H5 | High | `approve_boundary` trusted a caller-supplied approver | **Fixed**: approver from session, role/region/status checked in the function, custom SQLSTATEs | `test_approval_function_cannot_be_called_for_someone_else` |
| H6 | High | Idempotency key ignored content: same-version corrections dropped, dead letters unrecoverable, no change detection | **Fixed**: content-aware key; `VERSION_CONFLICT` dead letter; corrections close the dead letter | `test_a_corrected_record_replaces_its_dead_letter`, `test_different_content_under_an_accepted_version_is_a_conflict`; mutation-checked |
| H7 | High | A re-used `batch_ref` with different content was silently skipped | **Fixed**: batch content hash, `BATCH_REF_CONFLICT` rejection; generator refs include size and time | `test_a_reused_batch_reference_with_new_content_is_rejected` |
| M1 | Med | DQ issues kept the old territory when a new version moved the incident | **Fixed** | `test_issues_move_with_an_incident_that_changes_region`; mutation-checked |
| M2 | Med | Duplicate pairs missed when the earlier report arrived later; no counterpart re-check; concurrent batches | **Fixed**: evaluation set includes partners and referencing issues; per-source advisory lock | `test_duplicates_are_flagged_when_the_earlier_report_arrives_later`; mutation-checked |
| M3 | Med | One shared signing key for all sources, no key id or rotation | **Partly fixed**: the demo key can load only DEMO sources. Per-source keys/rotation **open** | `test_an_approved_source_is_refused_with_the_demo_key`; mutation-checked |
| M4 | Med | Unset or lower-case environment silently meant DEMO in CLIs | **Fixed**: explicit, case-insensitive, validated | `test_ingest_cli_refuses_an_unset_or_unknown_environment` (4 cases) |
| M5 | Med | Freshness could be faked (future `extracted_at`); naive/missing fell to NEVER_RUN; rejected batches never counted | **Fixed**: clamp, receipt-time fallback, rejected batches attributed to the claimed source (cannot flip health) | `test_a_future_extraction_time_is_clamped`, `test_a_rejected_signature_is_counted…` |
| M6 | Med | `record_quality_status` stale after manual review | **Fixed**: trigger on issue state change | `test_quality_status_is_refreshed_when_a_reviewer_closes_the_issues` |
| L1 | Low | Unknown fields kept in raw payload, docstring claimed they were dropped, not counted | **Fixed**: canonical record excludes them; count audited; raw stays as received (immutable, API-inaccessible); docstring corrected | `unit/test_contract.py::test_normalised_record_excludes_unknown_fields` |
| L2 | Low | Ingest reset reviewer location status, kept a stale assignee on reopen, kept old tokens when a version dropped them | **Fixed** | covered by the ingest suite (no dedicated test) |
| L3 | Low | RLS has no "global" scope; region derived from station parent; two missing indexes | **Indexes fixed**; global-scope RLS and DISTRICT/SUBDIVISION levels **open** (Phase 3, when global case-level roles query incidents) | — |

### API

| ID | Sev. | Finding | Status | Test / evidence |
|---|---|---|---|---|
| F1 | High | `category_restricted` flag disclosed which cases are sensitive; raw `detail` returned | **Fixed**: no category on any incident without sensitive-category access; per-rule detail whitelist. **Needs reviewer confirmation** (ADR-028 amendment) | `test_queue_hides_sensitive_categories…` |
| F2 | Med-High | Duplicate detail named the partner's reference (even HIGHLY_RESTRICTED/out of scope) | **Fixed**: partner id only; reference shown only if visible and in scope; existing data migrated | `test_a_duplicate_partner_that_is_hidden_is_not_named`; mutation-checked |
| F3 | Med | Issues on HIGHLY_RESTRICTED incidents listed and changeable | **Fixed** in RLS and API | `test_issues_on_hidden_records_are_not_listed_or_changeable` |
| F4 | Med | Empty-scope territorial grant passed every non-territorial check (Phase 1 B17) | **Fixed** in the policy engine | `unit/test_policy_engine.py::test_territorial_grant_without_territory…`, `test_a_steward_without_territory_is_denied` |
| F5 | Med | Out-of-scope region filter silently narrowed | **Fixed**: 403, audited | `test_an_out_of_scope_region_filter_is_refused_and_audited`; mutation-checked |
| F6 | Low-Med | Tampered cursors caused 500s | **Fixed** | `test_malformed_cursors_are_a_400` (4 cases) |
| F7 | Low | Boundary history ignored territory | **Fixed** | `test_boundary_history_is_scoped_to_the_stewards_region` |
| F8 | Low | Audit gaps (unknown boundary probe, summary, 409, ids, previous assignee) | **Fixed** | `test_unknown_boundary_needs_authorisation_first…`, `test_summary_and_refused_transitions_are_audited` |
| F9 | Low | Approver trusted; 42501 ambiguity; no purpose on approval | **Fixed** except purpose: approval records no declared purpose — **open** (step-up purpose capture is part of the Phase 5/6 workflow UI) | H5 test |
| F10 | Info | Grant-less users can read INTERNAL geography; provisioning never updates name/email | Geography: **accepted** (data-classification §1). Provisioning update: **open** (Phase 1 code) | — |

### Web, infrastructure, documentation

| ID | Sev. | Finding | Status | Evidence |
|---|---|---|---|---|
| H1 | High | Rejected-batches column always 0 | **Fixed** (data M5) | reconciliation test; live Data Health |
| H2 | High | Re-used `batch_ref` skipped | **Fixed** (data H7) | — |
| H3 | High | API failure replaced the page and dropped the DEMO label | **Fixed**: `(app)/error.tsx` inside the shell, `global-error.tsx`, `not-found.tsx`; pages show "unavailable" instead of throwing | build; browser |
| M1 | Med | Duplicate partner reference leak | **Fixed** (F2) | — |
| M2 | Med | DQ-REC-001 reopened ACCEPTED_AS_IS; never auto-resolved | **Fixed** | `test_adapters.py` reconciliation tests |
| M3 | Med | Focus lost after DQ actions; no success announcement | **Fixed**: focus to note / back to trigger, Escape cancels, polite live region | browser: `document.activeElement` checked |
| M4 | Med | Hard-coded English strings, untranslated enums, no plurals | **Fixed**: all through `t()`/`tn()`; labels from the catalogue | `tests/i18n.test.ts`, `tests/dq.test.ts` |
| M5 | Med | Freshness failure shown as "no data loaded"; 3+ API calls per page | **Fixed**: "status unavailable"; `requireUser` cached per request | build; browser |
| M6 | Med | Demo feed goes STALE after 48 h and a re-run could not renew it | **Fixed**: batch refs include extraction time, so a `demo-data` re-run delivers new (all-duplicate) batches and renews freshness without new incidents. The legacy feed stays stale by design and is labelled so | — |
| M7 | Med | Spec ingestion items missing/diverging without being marked | **Fixed**: adapters, retries, scheduled health (5 min), daily reconciliation built (ADR-030); remaining gaps listed in §3 and traceability | `test_adapters.py`, `worker/tests/test_jobs.py` |
| M8 | Med | Docs stale vs adapter/scheduler/reconciliation code | **Fixed** | `scripts/check_docs.py` |
| M9 | Med | Scheduled inbox ingestion not wired in Compose | **Documented** (off by default; enabling needs a writable volume — README, ADR-030) | — |
| L1 | Low | Error-catalogue / doc mismatches | **Fixed**: code aligned to catalogue (`invalid-state-transition`, `separation-of-duties` 409), `invalid-cursor` added; dictionary corrected | `test_boundary_needs_two_different_approvers…`, `test_one_person_cannot_fill_both_approvals` |
| L2 | Low | MFA message shown even when MFA would not help | **Fixed**: `/auth/me` `mfa_blocked_permissions` | `test_me_lists_effective_permissions` |
| L3 | Low | Root title said DEMO in every environment | **Fixed**: title, signed-out and not-found use the configured environment | curl: `<title>CRIMEMAP — DEMO / SYNTHETIC DATA</title>` |
| L4 | Low | CI never checked committed `openapi.json` | **Fixed**: `git diff --exit-code` after export | CI YAML parses |
| L5 | Low | `demo-data` memory vs 4 GB VM | **Documented** (README) | — |
| L6 | Low | Unwhitelisted params in pagination links; 403 shown as "no issues" | **Fixed**: `filterParams`; explicit messages | `tests/dq.test.ts` |

### My own checks

| Finding | Status |
|---|---|
| Spec "pluggable adapter architecture… stubs only" not built | **Fixed** (ADR-030) — `test_adapters.py` |
| Spec "retry policy with exponential backoff" not built | **Fixed** — Dramatiq retries on the new jobs, `worker/tests/test_jobs.py` |
| Source health only refreshed when a batch arrived (never scheduled) | **Fixed** — `check_sources` every 5 min; live `SOURCE_HEALTH_CHECK` events |
| Spec "daily reconciliation report" only a view | **Fixed** — `reconcile_yesterday`, audited; live `RECONCILIATION_REPORT` event |
| Batch delivered through one feed's channel could claim another source | **Fixed** — `REJECTED_SOURCE`, `test_a_batch_from_the_wrong_adapter_is_rejected` |
| Generalised points on a beat edge were not "covered" (found by the spatial test) | **Fixed** in `core.generalise_point` |
| Out-of-scope filter page reused the "cannot change this issue" message | **Fixed** (browser) |
| **Incident during the audit:** a shell-quoting mistake printed the local migrator DB password into tool output | **Rotated** immediately (new value never printed; old value verified invalid) |

## 3. Open items (not safely fixable now, or needing a decision)

1. **F1 interpretation** — confirm that a steward may see sensitive-category cases' DQ facts as long
   as categories are hidden uniformly (ADR-028 amendment), or restrict further.
2. **Per-source signing keys and rotation** (M3, spec "credentials encrypted and rotated").
3. **Purpose capture on boundary approval** (F9).
4. **Global-scope RLS / DISTRICT and SUBDIVISION levels** (L3) — needed when Phase 3 lets global
   roles query incidents.
5. **Not built (spec):** dual control for *manual* reconciliation (no manual reconciliation exists),
   per-station reconciliation against source-supplied totals, dead-letter replay UI, content/virus
   scan (no attachments yet), boundary re-assignment of history after an approved change, sibling
   overlap check in `propose-boundary`, Hypothesis property test for idempotency.
6. **Phase 1 findings** in `docs/phase-1-review-findings.md` remain open except B17 (closed by F4).

## 4. Verification (run locally, 2026-10-12, after all fixes)

| Check | Result |
|---|---|
| API + worker unit tests | 198 passed |
| Integration tests (testcontainers PostGIS; migrations 0001–0004 incl. round trip) | 190 passed |
| Total | **388 passed**, 0 failed (1 pre-existing Starlette/httpx deprecation warning) |
| New regression tests for this audit | 59 Python test cases — `test_phase2_audit.py` (29), `test_adapters.py` (13), `worker/tests/test_jobs.py` (5), contract (7), policy engine (1), ingest CLI environment (4) — plus 3 web tests (`i18n`, `dq`); two existing API tests were tightened |
| Mutation checks (fix disabled → its test fails) | 7 of 7 caught: residential generalisation, re-territory, symmetric duplicates, version conflict, demo-key gate, region-filter 403, detail whitelist |
| ruff, ruff format, mypy --strict (API, worker) | clean |
| Web tsc, ESLint, Vitest (65), `next build` | clean |
| OpenAPI regenerated: no drift; types in sync; Redocly 2.57.0 lint | valid |
| `scripts/run_security_checks.sh` (bandit, pip-audit, pnpm audit, gitleaks, semgrep, trivy) | all PASS |
| `scripts/check_docs.py` | no problems |
| Live Compose: migration 0004 applied | residential points generalised 27,697 (0 outside their beat), 2,050 duplicate details converted to ids, audit chain ok |
| Live idempotency after the fixes | re-ingest of all 42 batches: ALREADY_PROCESSED, 0 accepted, incidents unchanged (199,773), reconciliation unexplained 0 |
| Live scheduled jobs | `SOURCE_HEALTH_CHECK` and `RECONCILIATION_REPORT` audit events written by the scheduler/worker |
| Browser (demo.steward, password + TOTP) | queue with hidden categories and partner references, focus management, out-of-scope filter refused with explanation, Data Health page |

## 5. Files changed in this audit

- **Migrations:** `0004_phase2_audit_fixes.py` (new).
- **Worker:** `ingest/__init__.py` (rewritten in place), `adapters.py` (new), `jobs.py`,
  `settings.py`, `ingest_cli.py`; tests `worker/tests/test_jobs.py` (new).
- **API:** `ingest/contract.py`, `routes/dq.py`, `routes/geography.py`, `routes/integrations.py`,
  `routes/auth.py`, `policy/engine.py`, `cli.py`, `demo/generator.py`; tests
  `integration/test_phase2_audit.py`, `integration/test_adapters.py` (new), `test_ingest.py`,
  `test_phase2_api.py`, `unit/test_contract.py`, `unit/test_policy_engine.py`.
- **Web:** `app/(app)/review/page.tsx`, `app/(app)/data-health/page.tsx`, `app/(app)/layout.tsx`,
  `app/(app)/error.tsx` (new), `app/global-error.tsx` (new), `app/not-found.tsx` (new),
  `app/layout.tsx`, `app/signed-out/page.tsx`, `components/DqIssueActions.tsx`,
  `components/SafeguardStrip.tsx`, `components/Shell.tsx`, `lib/dq.ts`, `lib/i18n.ts`,
  `lib/current-user.ts`, `lib/environment.ts` (new), `messages/en.ts`, `vitest.config.ts`; tests
  `tests/i18n.test.ts` (new), `tests/dq.test.ts`.
- **Types:** `packages/types/openapi.json`, `packages/types/src/api.d.ts` (regenerated).
- **CI/config:** `.github/workflows/ci.yml` (OpenAPI drift check).
- **Docs:** this file; `decision-log.md` (ADR-026–028 amended, ADR-030, ADR-031),
  `data-dictionary.md`, `demo-data.md`, `api.md`, `integration-contracts.md`,
  `requirements-traceability.md`, `phase-2-report.md`, `phase-1-review-findings.md`, `README.md`.
- **Local secret:** `.env` (migrator password rotated; value not printed).
