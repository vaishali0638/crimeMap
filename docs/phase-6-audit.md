# Phase 6 Audit — Review Queue, Actions and Outcome Reviews

**Date:** 2026-10-13 · **Scope:** every Phase 6 source file and configuration:
- migration 0010;
- `routes/workflow.py`, `analytics/outcome.py`, the Review Queue badge and the operational-record change;
- the Review Queue pages (tabs, pattern review, action with outcome, evidence), the workflow forms
  and helpers, the message catalogue;
- tests and docs.

Checked against `crimemap_master_build_prompt.md` (REVIEW QUEUE, Phase 6, the data model for
PatternReview / OperationalAction / OutcomeReview) and ADR-012/035/036. Environment: DEMO,
synthetic data only. Not a production-readiness claim.

Method:
- Two independent reviews (backend with 12 throwaway probes against a test database, and web with
  throwaway unit tests), plus my own reading of the spec.
- Each finding was re-checked by hand. Fixes come with regression tests
  (`tests/integration/test_phase6_audit.py`, `apps/web/tests/workflow.test.ts`).
- Key fixes were mutation-checked (§5).
- A browser pass as `demo.supervisor` was run on the live stack after migrating to 0011.

Database fixes are in a new migration, **0011_workflow_audit**, because 0010 was already applied to
the live database.

| | Rows below |
|---|---|
| Fixed | 41 |
| Partly fixed | 2 |
| Accepted / documented | 4 |
| Open | 5 |
| Blocked | 2 |

## 1. Access, privacy and integrity (fixed)

| # | Severity | Finding (proved) | Fix | Test |
|---|---|---|---|---|
| B1 | **High** | A station officer saw a **sensitive category label** and the evidence snapshot id on an assigned action and in the queue. Row-level security admits station-scoped reviews. | Category and evidence link are shown only to callers who may read the review's evidence snapshot (ADR-035 rule). This applies to reviews, actions and queue items. The pattern-review link is hidden when the review is not visible. | `test_a1_…` ✓ |
| B2 | **High** | Stored **outcome numbers bypassed territory and ADR-012**. An SHO of one station read another station's comparison counts, computed unmasked by a region analyst. | Metrics record their coverage (regions, stations, whether sensitive categories were combined). Readers without that access get `metrics_visible: false` and no numbers. | `test_a2_…` ✓ |
| B3 | Medium | Comparison keys with "no comparison" were unvalidated free text: personal details and 5,000 characters were accepted. | API: no keys for NONE, max 40 characters, codes only, distinct. DB CHECK `outcome_review_comparison_keys`. | `test_a3_a4_…` |
| B4 | Medium | Comparison cells from **another region** were counted as the action region's cells. Duplicates were accepted. | Comparison area must be in the action's region, distinct and outside the action's cells. The comparison station must differ from the action's. The metrics code also refuses a foreign region. | same (layered, §5) |
| B5 | Medium | The author could **forge the supervisor's decision fields** (rationale, decider, time) and backdate submission after a return. | The trigger freezes those fields during revisions; a decision cannot change the submission time. | `test_a5_a6_…` ✓ |
| B6 | Medium | The completer could **rewrite the approval** (approver, rationale, time); the approver could pre-fill completion. | Trigger freezes the approval during completion and the completion fields during approval. | same ✓ |
| B7 | Medium | **History was forgeable**: any officer could insert a fake "APPROVED → REJECTED" step. | The application role lost INSERT. History is written by AFTER triggers (SECURITY DEFINER) on the three tables, including creation and revisions. | `test_a7_…` ✓ |
| B8 | Medium | Concurrent outcome creation gave **500**; database refusals generally surfaced as 500. | The action is locked before creating; database refusals (23505, 55000, 42501, 23514, 23503) become audited 409s. | `test_a8_…` |
| B9 | Medium | **Queues silently dropped items.** Fixed scans (200 reviews / 500 actions) included completed and final rows, so new proposals vanished and the badge undercounted. | SQL selects only actionable rows (state + permission territory + filters). It returns `more` beyond 200, and the badge reports `more`. Completed rows can no longer appear as "record completion". | `test_a9_…` ✓ |
| B10 | Medium | Outcome counts **ignored the review's filters** (hours, multi-category, record type, locality). Probe: 6 records reviewed, 23 measured. | Metrics re-run the review's own evidence query with only the period changed. | `test_a10_…` ✓ |
| B11 | Medium | Date gaps: the baseline could overlap the action; the after period could end in the future; an action could be completed before it starts. | The baseline ends before the action starts. The after period ends today or earlier. Completion needs the action started (API 409 and trigger). | `test_a11_…` ✓ |
| B12 | Low | `GET /actions/assignees` (officer names) was not audited. | `RECORD_VIEW` event. | `test_a12_…` ✓ |
| B13 | Low | One SHO could approve, complete and review the outcome of the same action alone. | The approver does not record completion (API, trigger, CHECK). The completer does not review the outcome (API, trigger). Queues follow the same rules. | `test_a13_…` (layered, §5) |
| B14 | Low | The caller's own drafts could be HIGH priority by record count. | Own drafts are LOW, or MEDIUM when returned. | full suite |
| B15 | Low | Every analyst saw others' draft outcome reviews as "continue" (they could not edit them), and "OVERDUE" hid "DRAFT". | Others' drafts are not in the queue. Status shows the real state; `overdue` is a separate flag. | `test_an_outcome_…` |
| B16 | Low | Editing a review did not re-check `pattern_review.create` on its territory. | The same check as submit (shared `_author_draft`, with a row lock). | full suite |
| B17 | Low | Audit object types were split ("action" vs "operational_action"). Propose and approve validation refusals were not audited. | One name per table. Validation refusals are audited `INVALID_REQUEST`. | full suite |
| B18 | Low | The assignee list ignored grant `valid_from`. | Checked. | — |
| B19 | Low | Snapshot references were not tied to the action's region. | Must cover the action's region (and be readable). | — |
| B20 | **High (flaky test)** | About **5% of valid snapshot ids were refused as "phone numbers"** by the personal-identifier check. This was the cause of the unexplained Phase 6 flake. Proof: 1,089 of 20,000 random UUIDs were flagged. | Snapshot references must be UUIDs and skip the free-text check. Document references keep it. | `test_snapshot_references_…` ✓ |

## 2. Web (fixed)

| # | Finding | Fix |
|---|---|---|
| W1 | **High:** a missing message key crashed the page (`t()` threw), e.g. review type CIVIC_CONTEXT. | `t()` falls back to the key. Missing labels added. A unit test checks that every API enum and stored state has a message. |
| W2 | 422 validation reasons were hidden ("Validation error" only), including the personal-reference refusal. | Field messages from `errors[]` are shown. Verified in the browser. |
| W3 | Double submit, including a second POST /actions. | The button stays disabled through the request and the page transition. Repeated presses are ignored. |
| W4 | Success messages vanished with the form, and focus was lost (WCAG 4.1.3, 2.4.3). | `?done=` flash outside the forms, focused on arrival. Verified in the browser. |
| W5 | Outcome notes and conclusion could not be cleared, and the form showed stale state. | API: `""` clears. The form remounts on `updated_at`. |
| W6 | Queues showed the data-quality "unavailable" text. A timeout broke the whole page. | Workflow text; fetch errors caught. |
| W7a | **Spec gap:** no filters and no "assigned to me" on the workflow queues. | Priority, region, owned-by-me and overdue filters, applied in SQL. Verified in the browser. |
| W7b | **Spec gap:** no history for DQ and duplicate items, or for operational records. | History page from the audit log (states, actor, time). Access as for the item. |
| W7c | The pattern review did not show its data-quality summary. | Shown. |
| W8 | "1 days" / "1 cells". | Plural forms (unit test). |
| W9 | Client date checks were weaker than the API's. | Same rules as the API, as message keys (unit tests). |
| W10 | Duplicate `h1` and id on DQ errors. | Paragraph alert. |
| W11 | The duplicates tab showed the count of all issues. | Duplicates-only count. |
| W12 | Revise and Submit were separate, so unsaved edits were lost on submit. | One draft form; submit saves the revision first. |
| W13 | Officers saw a dead review link; outcome 404 said "try later". | Link hidden when not visible; "not available to you". |
| W14 | Station and assignee lists failed silently. | Loading, error and empty states. |
| W15 | CSRF failure was shown as "not allowed"; 401 had no sign-in link. | Specific messages; sign-in link. |
| W16 | Outcome form: concurrent saves, duplicate references, no reason at the limit, plain-text snapshot references. | Single busy state; duplicates refused; reasons shown; links. |
| W17 | Accessibility: row header was the action link; h1→h3 jumps; no `required`; disabled buttons gave no reason; alerts re-fired per keystroke; hints were not linked. | Row header is the item and area; links described by it; h2 panels; `required`; hints; polite live region; `aria-describedby`. |
| W18 | Irreversible decisions had no confirmation. | Two-step confirm for approve, return, reject, complete and finalise. Verified in the browser. |
| W19 | Raw codes and ISO dates. | Section names and day formatting translated. Queue area built in the web app. |

## 3. Accepted, partly fixed, open or blocked

| Item | Status |
|---|---|
| A station-scoped SHO cannot see a region-wide review's evidence or category ("not shown"), and cannot propose from it | **Accepted** (territory model, ADR-036) |
| Retroactive actions (dates in the past) are allowed | **Accepted, documented** (recording actions already taken); completion needs the start date |
| Outcome summary sentences and `sensitive_rule` text come from the API in English | **Accepted** (like other API text; i18n later) |
| Mutations of three API checks survive because another layer enforces the same rule (§5) | **Accepted** (defence in depth) |
| DQ and duplicate rows show no due date, and the owner only as "you" | **Partly fixed** (history added; DQ has no due-date or owner-name field) |
| Station list in the propose form is not limited to stations the caller may propose for | **Partly** (refused by the API with a message; help text says so) |
| No Playwright, axe or screen-reader tests | **Open** |
| Queue pagination beyond 200 (only `more` and filters) | **Open** |
| No notifications for due or overdue items | **Open** |
| Applying approved location corrections | **Open** (not built) |
| Carried over: Phase 4 audit (12 web findings, no backend audit), concurrency targets, snapshot retention, `demo.analyst` TOTP, Phase 2 F1 | **Open** |
| Officer completion through the browser (no KKL officer account; seeding needed for the analyst's steps) | **Blocked** in the browser; covered by integration tests |
| Visual screenshots of the new pages (browser pane hidden, so clicks were driven by page scripts and form input) | **Blocked**; text, focus, network and console were checked |

## 4. Verification (run locally, 2026-10-13)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0011 and the round trip) and worker tests | **509 passed** (was 495; +14 audit regression tests), 1 deprecation warning from before Phase 3. Two full runs and repeated focused runs; the flaky failure did not recur after B20. |
| ruff, ruff format, mypy --strict (API 53 files, worker 10) | clean |
| Web: ESLint, tsc, Vitest (**112**, was 106), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` | all PASS (bandit, pip-audit, pnpm audit, gitleaks, semgrep, trivy) |
| `scripts/check_docs.py` | no problems |
| Live Compose | Migrated to **0011**; API, worker and scheduler rebuilt; web restarted |
| Browser (`demo.supervisor`, KKL) | The patterns queue was filtered (High, KKL); the user's own LOW draft was correctly excluded. Return for revision showed the confirmation question, recorded the decision, focused the flash message ("Decision recorded."), and the seeded review's history was written by the database (creation, then DRAFT → PENDING by the analyst; checked in SQL). Proposing an action naming a person showed the API's reason. The final outcome showed its before/after counts and a linked snapshot reference. No new console errors (the one 404 dates from before a Phase 6 fix). |

The browser seeding for the analyst's steps is as described in the Phase 6 report: application role,
analyst's session, the supervisor's Phase 5 evidence. The database now writes the history rows for
seeded steps too.

## 5. Mutation checks

Each fix was reverted and its test re-run.

| Check | Result |
|---|---|
| B1 evidence rule, B2 metrics coverage, B5 decision freeze, B6 approval freeze, B7 history revoke, B9 state filter, B10 evidence query, B11 future after period, B12 assignee audit, B20 snapshot id | **caught** (10 of 10) |
| Phase 6 build checks re-run: finalise check, DB FINAL CHECK | caught |
| Metrics immutability trigger (now in 0011) | caught |
| Author/proposer SoD with both the API and the trigger removed | caught (both) |
| Survive alone, enforced by another layer: B4 region check (the metrics code also refuses), B8 lock (the unique index maps to 409), B13 approver-completes (the trigger refuses, mapped to an audited SoD 409), API-only removal of review and action SoD (the triggers refuse) | accepted as defence in depth |

## 6. Files changed

- **API:**
  - `migrations/versions/0011_workflow_audit.py` (new);
  - `routes/workflow.py`;
  - `analytics/outcome.py`;
  - `routes/reviewqueue.py`.
- **Tests:**
  - `tests/integration/test_phase6_audit.py` (new);
  - `tests/integration/test_workflow.py`;
  - `tests/integration/test_phase3_audit.py` (badge shape).
- **Web:**
  - `components/workflow/Forms.tsx`, `components/workflow/View.tsx`;
  - `lib/workflow.ts`, `lib/i18n.ts`;
  - `app/(app)/review/page.tsx`, `review/patterns/[id]`, `review/actions/[id]`, `review/evidence/[id]`;
  - `review/history` (new);
  - `app/(app)/layout.tsx` (badge, from the Phase 6 build);
  - `messages/en.ts`;
  - `tests/workflow.test.ts`.
- **Contract:** `packages/types/openapi.json`, `src/api.d.ts`.
- **Docs:**
  - this file;
  - ADR-036 (revised) in `decision-log.md`;
  - `api.md`, `data-dictionary.md`, `requirements-traceability.md`;
  - `phase-6-report.md`, `README.md`.
