# Phase 6 Delivery Report — Review Queue, Actions and Outcome Reviews

**Date:** 2026-10-13 · **Environment:** DEMO (synthetic data only) · **Go/no-go:** see §8.

> **Audited 2026-10-13** — see [phase-6-audit.md](phase-6-audit.md). The audit fixed a sensitive
> category leak to station officers, outcome numbers readable without the access that produced them,
> forgeable history and decision fields, queues that silently dropped items, outcome counts that
> ignored the review's filters, and the cause of the flaky test (migration 0011). Current totals:
> 509 Python, 112 web tests.

Nothing has been committed or pushed, so CI has not run on GitHub; every CI step was run locally
(§4). CRIMEMAP is **not** claimed to be production ready.

## 1. Specification acceptance criteria

| Criterion (spec Phase 6) | Result | Evidence |
|---|---|---|
| State transitions are authorised and audited | **Met** | Each transition checks permission and territory in the API and is limited again by migration 0010 triggers (session user, allowed transition, required fields), CHECKs and row-level security. Every transition writes a `WORKFLOW_TRANSITION` audit event and a history row. Refusals are audited (separation of duties, invalid state, incomplete). Tests: `test_pattern_review_decisions_are_authorised_audited_and_kept_in_history`, `test_an_author_never_decides_their_own_review` (API 409 and database refusal), `test_workflow_items_are_invisible_outside_territory`. Browser: 5 transitions by `demo.supervisor`, each with an audit event; audit chain verified. |
| An action cannot be marked approved by an unprivileged user | **Met** | `test_an_action_cannot_be_approved_by_an_unprivileged_user`: analysts and officers get 403, an SHO of another station 404, the proposer (an SHO) 409. As the application role, the database refuses approval by the proposer, or without a rationale. Mutation-checked. Browser: the supervisor's own proposal shows "awaiting approval by someone other than the proposer" with no approve form, and is not in their queue. |
| An outcome review cannot be finalised without baseline and evidence references | **Met** | API 422 (audited DENY `INCOMPLETE`) without an evidence reference or conclusion. The database CHECK refuses FINAL without references, even for the migrator (`test_the_database_refuses_a_final_outcome_without_evidence`). The baseline is required at creation and must end before the after period. Mutation-checked (API and CHECK). Browser: Finalise stays disabled until a reference and conclusion exist; then FINAL and read-only. |

## 2. What was built

| Area | Delivered |
|---|---|
| Schema (migration `0010_workflow`) | Pattern review decision columns and transition trigger. New tables: `core.operational_action`, `core.outcome_review`, `core.workflow_transition` (append-only). Triggers, CHECKs, row-level security, column grants. Full downgrade (round-trip test). |
| API (`routes/workflow.py`, `analytics/outcome.py`) | Pattern review read, revise, submit and decide (approve / return / reject). Actions: propose, assignee list, approve or reject (with assignment), complete. Outcome reviews: create (server-computed before/after metrics for the action cells, the surrounding ring and a comparison station or cells; neutral summary), edit (notes, conclusion, evidence references; snapshot references must be readable), finalise. `GET /review-queue?queue=` with priority and due dates. `GET /workflow-history`. Badge counts include the three new queues. |
| Web (`/review`) | Tabs: Data quality, Possible duplicates, Operational records, Pattern reviews, Actions, Outcome reviews due, and AI insights (Phase 7 placeholder). Tabs are shown by permission. Queue tables: priority, type, date, area, category, owner, due (overdue in words), status, evidence link, next step. Detail pages for pattern reviews and actions (with the outcome review). They show only the forms the caller may use, plus history. Evidence snapshot page. |
| Docs | ADR-036; api.md, data dictionary, acceptance plan, traceability (R-SCR-4, R-WF-1), README. |

## 3. Defects found and fixed during Phase 6

| # | Defect | How found | Fix |
|---|---|---|---|
| 1 | Pattern review trigger failed with `type "geometry" does not exist` | integration test | Trigger `search_path` includes `public` (PostGIS) |
| 2 | Station officers got 404 on their assigned action | integration test | Row-level security hides region-level reviews from them, so the join is a LEFT JOIN; category and evidence show "not shown" |
| 3 | OpenAPI schema name clash (`ApprovalIn`) | route test | Renamed `ActionDecisionIn` |
| 4 | Badge test expected the old response shape | full suite | Updated expectation (officers have an actions queue) |
| 5 | Three mutations survived at first | mutation check | Added tests: SHO self-approval; the metrics-immutability trigger tested as migrator (the app role is also blocked by column privileges) |
| 6 | Station list empty in the propose form (wrong path) | browser | `/stations?region=` |
| 7 | Cell checkbox named "on"; "1 cells" | browser | Accessible name per cell; plural forms |
| 8 | Next steps were English strings from the API | review | Codes, translated in the web catalogue |

## 4. Verification (run locally, 2026-10-13)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0010 and round trip) and worker tests | **495 passed** (was 489; +6 workflow integration tests), 1 deprecation warning from before Phase 3 |
| Mutation checks (Phase 6) | **7 of 8 caught**: review SoD, action SoD, finalise check, history row, database FINAL CHECK, metrics trigger, queue territory filter. **Survived:** removing the in-handler `action.complete` check. No single-grant role can reach it, because the route requirement and the in-territory load refuse first; it remains as defence in depth. |
| ruff, ruff format, mypy --strict (API 53 files, worker 10) | clean |
| Web: ESLint, tsc, Vitest (**106**, was 98), `next build` | clean |
| OpenAPI regenerated vs committed copy; Redocly lint | in sync; valid |
| `scripts/run_security_checks.sh` | all PASS |
| `scripts/check_docs.py` | no problems |
| Live Compose | Migrated to 0010; API, worker and scheduler rebuilt |
| Browser (`demo.supervisor`, KKL) | Decided (approved) a submitted review with a rationale. Proposed an action from it, which then could not be self-approved. Approved another user's proposal. Started an outcome review: before/after counts for action area, ring and comparison station, and a neutral summary. Added notes, a snapshot reference and a conclusion, then finalised. History and audit events recorded for every step; audit chain verified. One console 404 came from defect 6, before its fix. |

**Browser test seeding (honest note).** demo.supervisor is the only enrolled decider in KKL, the
analyst's TOTP is unknown, and no demo officer or SHO covers KKL. So three steps were seeded rather
than performed through the UI:
- **Analyst submits a review:** seeded as the application role with the analyst's session, through
  the same triggers and row-level security. It reuses the evidence and server-computed counts of the
  supervisor's Phase 5 draft.
- **Analyst proposes an action:** seeded the same way.
- **Officer records completion:** written by the migrator, outside the API, and labelled
  "Browser test seed".

These seeded steps have history rows but no audit events. Completion through the UI was not exercised
in the browser; the integration tests cover it.

**Flaky observation.** One combined run of the workflow and unit tests failed
`test_an_outcome_review_cannot_be_finalised_without_baseline_and_evidence`. The output was not
captured. It did not recur in 11 further runs, including two full suites. Cause unknown.

## 5. Performance

Not measured for Phase 6. Each queue request scans up to 200 pattern reviews or 500 actions in the
caller's territory and filters in Python. That is fine at demo volumes, but it will need SQL-side
filtering and pagination for real volumes. Creating an outcome review runs two analytics selections
(one per period), comparable to a workbench section.

## 6. Security and privacy notes

- Separation of duties is enforced in the API (409, audited), by triggers and by CHECKs.
- Station-level territory is respected. SHOs and officers do not see region-wide evidence; the UI
  says "outside your territory view".
- Free text refuses personal identifiers and person references. The audit log records lengths, not
  text.
- Outcome metrics use the reviewer's own access and the ADR-012 rule.
- Evidence references to snapshots must be readable by the reviewer (404 otherwise).

## 7. Known limitations and open items

| Item | Status |
|---|---|
| Phase 6 audit | not yet run |
| Applying approved location corrections to the analytical location (correction workflow) | not built |
| AI insights queue | Phase 7 |
| Notifications (due or overdue items) | not built; the shell's notifications button is still a stub |
| Queue pagination and SQL-side filtering (§5) | open |
| Outcome comparison area stored as keys; `comparison_area_geometry` unused | minor |
| Pattern review page does not list the actions proposed from it | minor |
| No Playwright, axe or screen-reader tests; UI checked by hand (pane hidden, so no visual screenshots of the new pages) | open |
| Carried over: Phase 4 audit unfinished (12 web findings, no backend audit); concurrency targets; snapshot retention; `demo.analyst` TOTP; Phase 2 F1 decision | open |

## 8. Go / no-go

**Conditional go for a Phase 6 audit, then Phase 7.**
- The three Phase 6 acceptance criteria are met, with tests (mutation-checked) and a browser check.
- Tests, linters, types, build, contract and security scans are clean.

Conditions:
- Audit Phase 6.
- Finish the carried-over Phase 4 audit.
- Add demo accounts (or enrol existing ones) so that officer completion can be checked end to end in
  the browser.
