# Phase 7 Delivery Report — Responsible AI Assistance

**Date:** 2026-10-13 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §8.

> **Audited 2026-10-13**: see [phase-7-audit.md](phase-7-audit.md). The audit closed several gaps:
> - the worker could switch out of the AI role (detectors now run in their own container with only the
>   AI login);
> - the AI role could see sensitive incidents' locations;
> - the flags could be switched on directly in the database;
> - insights could be written for a draft model.
>
> It also fixed detector correctness and the web forms (migration 0013). Current totals: 554 Python,
> 116 web tests.

Nothing has been committed or pushed, so CI has not run on GitHub; every CI step was run locally
(§4). CRIMEMAP is **not** claimed to be production ready. Model "approvals" are demo approvals for
synthetic data. Phase 7 has not been audited yet. See [ai-governance.md](ai-governance.md) and ADR-037.

## 1. Specification acceptance criteria

| Criterion (spec Phase 7) | Result | Evidence |
|---|---|---|
| AI output always includes evidence and limitations | **Met** | The database requires evidence, a baseline, limitations, alternatives, an explanation, a confidence with a calibration note, and a data-quality score on every insight. An insert without limitations is refused even from the AI role. `test_ai_output_always_includes_evidence_and_limitations` checks every card type through the API: the label, the input snapshot with SHA-256, and geometry. Mutation-checked. Browser: card page as `demo.steward`. |
| AI cannot create an action, export data, or modify a case | **Met** | Detectors run as the database role `crimemap_ai`. As that role, 11 attempted writes and reads are refused: update or delete cases, change locations or DQ issues, create an action, export, note, switch a model, write the audit log, read offence names or restricted details. Sensitive and highly restricted records are invisible to it. After validating a geocoding suggestion, the record is unchanged and no action exists. Mutation-checked (grant, row-level security, role switch). |
| Analyst validation/dismissal is required and audited | **Met** | Every insight starts PENDING with "Analyst review required". Validate or dismiss needs a note (no personal references). It is audited (`AI_INSIGHT_REVIEW`, never the note text) and appears in the card's history. The database allows only that change, once, by the session user. A second review gets 409; a station officer gets 403. Briefings use validated insights only. Browser: validated a duplicate suggestion (confirm step, flash, history). |
| Model registry and kill switch exist | **Met** | `core.model_registry` holds 8 model cards. Per-model switches and the global `ai_assistance` switch need `ai.config`, a fresh sign-in and a reason, and are audited. While off, the worker skips the model (or everything) and reviews are refused. Tested off and on again. Mutation-checked. |
| Forecasting remains disabled unless AI rollout gates are passed | **Met** | `forecast-review-priority` is a DRAFT with 8 unmet gates. Switching it on gets 409. The `ai_forecasting` and `ai_copilot` flags are refused while their model is off. Even after marking it APPROVED in the database, the trigger refuses switching it on with an unmet gate. Mutation-checked. |

## 2. What was built (spec delivery order)

| # | Spec item | Delivered |
|---|---|---|
| 1 | Data anomaly rules | `dq-anomaly`: a station's share without usable location versus its own 12-week baseline (binomial z ≥ 3), and source volume drops (z ≥ 3). This adds to the Phase 2 record-level rules. |
| 2 | Duplicate suggestions | `duplicate-suggest`: pairs within 100 m and 2 h in the same category, scored by distance, time, locality and station (≥ 0.7). Never merged. Pairs already flagged by DQ-DUP-001 are skipped. |
| 3 | Geocoding suggestion with confidence/review | `geocode-suggest`: a locality centre from well-located records in the same station, with support and spread. Low confidence is flagged for mandatory checking; too spread out (> 1.5 km), it is not suggested. |
| 4 | Emerging-pattern statistical alerts | `emerging-pattern`: 1 km cells per category, 28 days against 24 weeks, Poisson upper tail p < 0.001, minimum 5. |
| 5 | Explainable clustering | `cluster-explain`: PostGIS DBSCAN (250 m, 8 cases, 28 days). Described by size, centre snapped to 250 m, extent, time span and night share. |
| 6 | Template-based analyst briefings | `POST /ai/briefings` from validated insights, citing each insight and its input snapshot. A draft only; not stored. |
| 7 | Optional approved LLM adapter | Disabled (no approved deployment). Built and tested: refusal of prohibited questions, redaction, authorised and cited context only, an adapter with no tools. The endpoint answers 503, or 422 for prohibited questions. |

Also built:
- **Database:** migration `0012_ai` and the `02-ai-role.sh` init script (an idempotent role).
- **Worker:** `crimemap_worker.ai`, a daily scheduled job plus a CLI.
- **API:** `routes/ai.py` and `ai/copilot.py`.
- **Notes:** on insight cards.
- **Review Queue:** an AI insights tab (filters by type) and badge counts.
- **Web:**
  - an insight card page that implements the card contract, with Open Evidence, Validate, Dismiss, Add Note and the audit trail;
  - an input-snapshot page;
  - a model registry page with switches;
  - a briefing builder;
  - an Analytics Workbench "AI Analysis" section.
- **Docs:** `ai-governance.md` and ADR-037.

## 3. Defects found and fixed during Phase 7

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | The test database image lacked the new role init script (cached image) | migration test | Image rebuilt; documented |
| 2 | The worker read the model registry as itself (no grant) | code review | All AI reads also run as `crimemap_ai` |
| 3 | Planted test records broke other tests' counts | full suite | The fixture removes them after the module |
| 4 | Review-trigger test passed for the wrong reason (session settings rolled back) | test review | Autocommit connection; asserts the row is visible first |
| 5 | The AI-role UPDATE test passed without testing the grant (it read a column) | mutation check | Writes a literal value |
| 6 | Geocoding suggested locality centres with 3 km spread | test-data probe | Maximum spread 1.5 km |
| 7 | Run end time equalled the start (transaction time) | live run | `clock_timestamp()` |
| 8 | semgrep: SQL built from strings (constant, but flagged) | security scan | Single literal statements; geometry kind as a bound parameter |
| 9 | "1 minutes apart" | browser | Plural form |

## 4. Verification (run locally, 2026-10-13)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0012 and the round trip) and worker tests | **531 passed** (was 509; +8 AI integration, +14 unit), 1 deprecation warning from before Phase 3 |
| Mutation checks (Phase 7) | **10 of 10 caught**: the AI role's row-level security, an extra AI write grant, the limitations CHECK, the switched-off model, the rollout-gate loop, the steward's DQ-only scope, the role switch in the worker, briefings from validated only, copilot refusal, the review trigger's session user |
| ruff, ruff format, mypy --strict (API 56 files, worker 11) | clean |
| Web: ESLint, tsc, Vitest (**115**, was 112), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` | all PASS (after fix #8) |
| `scripts/check_docs.py` | no problems |
| Live Compose | Role created with the init script; migrated to **0012**; API, worker and scheduler rebuilt |
| Live AI run (scheduled job) | 20 runs completed: 399 geocoding suggestions, 89 duplicate suggestions, 17 clusters, 3 emerging patterns, 1 DQ anomaly. A second run created no duplicates (deduplicated) and recorded real durations. |
| Browser (`demo.steward`) | AI insights tab: data-quality types only, most confident first. Card: evidence, baseline, records named, limitations, alternatives, required review. Validate: confirmation, flash with focus, history (AI creation, then the steward's decision). Input snapshot page: parameters, inputs, SHA-256. |

**Not checked in the browser:** the model registry switches, the briefing builder and the workbench
AI section. `demo.admin` and `demo.analyst` have no known TOTP keys, and the steward has no
registry or analytics AI access. They are covered by the integration tests (API) and a successful
build; the web side has unit tests only.

## 5. Performance

On the live data (2-vCPU Colima VM):
- **Duplicate detection** takes about **3 minutes** for the largest region: a spatial self-join over
  90 days.
- **Other detectors** finish in under 2 seconds per region.

That is acceptable for a daily background job, but it would need tuning (a time-bucketed join)
before running more often.

## 6. Security and privacy notes

- **Database-enforced limits:** the AI role, the card contract, the single review and the kill
  switches (ADR-037).
- **Inputs:** sensitive categories and highly restricted records never reach a detector. Offence
  names and references are not readable by it.
- **Outputs:** places and time windows only. Cluster centres are snapped to 250 m. Geocoding gives
  a locality centre, not an address.
- **Visibility:** a steward sees and reviews data-quality types only. Referenced records are named
  only to callers who may see them.
- **Audit:** review notes and copilot questions are never written to the audit log (lengths only).

## 7. Known limitations and open items

| Item | Status |
|---|---|
| Phase 7 audit | not yet run |
| Thresholds hand-set and uncalibrated (stated on every card) | accepted (no labelled outcomes for synthetic data) |
| No retention policy for AI runs and insights | open |
| Duplicate detector run time (§5) | open |
| Validated geocoding suggestions are not applied to records (no correction workflow yet) | open |
| No Playwright, axe or visual tests; registry, briefing and workbench AI pages not checked in the browser (§4) | open |
| Forecasting and the copilot: not built; disabled behind gates | by design |
| Carried over: Phase 4 audit, concurrency targets, snapshot retention, `demo.analyst` / `demo.admin` TOTP, Phase 2 F1 | open |

## 8. Go / no-go

**Conditional go for a Phase 7 audit, then Phase 8.**
- All five Phase 7 acceptance criteria are met, with database-level enforcement, tests
  (mutation-checked) and a browser check of the review path.
- Tests, linters, types, the build, the contract and the security scans are clean.

Conditions:
- Audit Phase 7.
- Enrol test authenticators for `demo.analyst` and `demo.admin`, so the registry, briefing and
  workbench AI pages can be checked in the browser.
- Decide the retention of AI runs and insights.
