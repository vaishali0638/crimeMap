# Phase 7 Audit — Responsible AI Assistance

**Date:** 2026-10-13 · **Scope:** every Phase 7 source file and configuration:
- migration 0012 and the AI role init script;
- the detectors (`crimemap_worker.ai`) and their scheduling;
- `routes/ai.py` and `ai/copilot.py`;
- the notes and badge changes;
- the AI web pages and forms;
- tests and docs (`ai-governance.md`, ADR-037).

Checked against `crimemap_master_build_prompt.md` (AI Architecture and Governance, the AI insight
card contract, rollout gates, LLM restrictions, Phase 7). Environment: DEMO, synthetic data only. Not
a production-readiness claim.

Method:
- Two independent reviews: the backend with throwaway probes against a test database, and the web
  with throwaway unit tests.
- My own check of the spec's model-registry fields.
- Each finding re-checked; fixes with regression tests, and the key fixes mutation-checked (§5).
- A browser pass as `demo.steward` on the live stack after migrating to 0013.

Database fixes are in **migration 0013_ai_audit**, because 0012 was already applied live.

| | Rows below |
|---|---|
| Fixed | 35 |
| Accepted / documented | 7 |
| Open | 4 |
| Blocked | 2 |

## 1. Backend and database (fixed)

| # | Severity | Finding (proved) | Fix | Test |
|---|---|---|---|---|
| B1 | **High** | The AI role could read **locations and data-quality issues of sensitive incidents** (210 location rows with exact geometry; 35 issues). The location policy checked only the location row. | The AI role sees a location or issue only if it may see the incident. | `test_ai_cannot_create…` counts ✓ |
| B2 | **High** | "AI cannot … whatever its code does" was false. Detectors ran on the worker login, which could `RESET ROLE` to a role with case, location and export write grants. | `crimemap_ai` is a **LOGIN** used only by the new **`ai-worker`** container, which holds no other credentials. Worker membership was revoked. The AI role writes its own audit events. | `SET ROLE` refused both ways ✓ |
| B3 | **High** | The forecasting and copilot gate was API-only. The app role could switch `ai_forecasting`, `ai_copilot` (and `civic_context`) on by direct SQL, and forge `changed_by`. | A feature-flag trigger allows AI flags only, by the session user, and copilot/forecasting only while their model is enabled. | `test_feature_flags_are_guarded…` ✓ |
| B4 | Medium | The AI role could write a PENDING insight for the **draft forecast model**, into any run. | Runs and insights only for an approved, enabled rule/statistical detector of that type, into a RUNNING run of that model and region. | `test_the_ai_role_writes_only…` ✓ |
| B5 | Medium | The auditor's global `ai.insight.read` returned nothing (row-level security). | `app.ai_global` for global AI readers; case-data scope unchanged. | `test_auditors_read…` ✓ |
| B6 | Medium | A detector failure aborted the whole run, unrecorded. | Each model/region is guarded; the failure becomes a FAILED run with an audited `AI_RUN` ERROR, and the others continue. | `test_a_failing_detector…` ✓ |
| B7 | Medium | Persistent patterns **re-alerted every day** (the key held the date). | One card per pattern per 28-day block. | unit test |
| B8 | Medium | Clusters and patterns used low-confidence locations while saying they did not. | Locations with confidence 0.4 or more only (including the usable count); model cards updated. | full suite |
| B9 | Medium | The copilot guard missed many phrasings ("Who stole…", "victim live", "Send patrols", "persons", "reoffending"); redaction missed grouped or lowercase identifiers. | Broad refusal (people, identity, home, whereabouts, victims/witnesses, risk, faces, predictions, enforcement, patrols); wider redaction; names and addresses left to an approved redaction service (rollout gate). | 10 new refusal and 7 redaction cases ✓ |
| B10 | Low | An SHO could not open the input snapshot of a card they can see. | A run is readable through any visible insight of it. | `test_auditors…` ✓ |
| B11 | Low | Day windows cut at 05:30 IST (database in UTC); cluster dates in UTC. | IST bounds and dates throughout. | full suite |
| B12 | Low | Poisson tail 0.0 at (40, 2) and 1.0 at (800, 760) from underflow. | Log-space summation. | unit test ✓ |
| B13 | Low | Pattern and cluster cards took the mode station, so a station reader saw counts covering other stations. Duplicate partners were not region-bound. | Station set only when all records share it; pairs within one region. | full suite |
| B14 | Low | The list limit was applied before the steward filter; the badge too. | Scope in SQL for both. | layered (§5) |
| B15 | Low | Any reader could generate briefings; the unvalidated refusal was not audited. | Briefings need `ai.insight.review`; refusals audited. | ✓ |
| B16 | Low | DQ confidence (about 0.999) disagreed with its LOW label. | Label from the same tail probability. | unit test |
| B17 | Low | Switch refusals returned raw database text; any DB error became "rollout-gate". | SQLSTATE 55000 only, with fixed messages. | full suite |
| B18 | Low | "Analyst review required" lacked the spec's full stop. | Wording fixed (CHECK, default, rows). | ✓ |
| B19 | Low | The switch did not re-check mid-run. | Both switches re-checked per model/region run. | full suite |
| G1 | Spec gap | The model registry lacked the **rollback state** (spec field). | `rollback_to` in the API and on the page. | — |
| B20 | Low | Duplicate suggestions on case records. | Documented as the spec's "source-ingest validation" use: never merges; model card updated. | accepted |

## 2. Web (fixed)

| # | Finding | Fix |
|---|---|---|
| W1 | **High:** after a switch, the form stayed armed and offered a one-click reverse with the old reason. | Forms remount on the new state. |
| W2 | The note form kept its text, risking a duplicate post. | Remounts after a note (verified in the browser). |
| W3 | A repeated change was not announced; a reload re-showed a stale flash. | Nonce in the URL; the flash is keyed on it (verified: second note announced and focused). |
| W4 | Readers without the note permission saw "unavailable" (a denied request on every view). | The notes section is shown only to those who may note. |
| W5 | Wrong reason when AI was globally off. | `ai_enabled` on the card. |
| W6 | The kill switch had no navigation entry; admins could reach it only by URL. | "AI models" nav item for `ai.model.read`. |
| W7 | AI Analysis misled data stewards ("None awaiting review"). | Steward message; h2 heading; a note that only the region filter applies. |
| W8 | 503 showed "Try again". | Shows the API's reason or "switched off". |
| W9 | "Switch on" was offered for models with unmet gates. | Hidden, with the reason shown. |
| W10 | Links shown to users who cannot open them (analytics, registry, briefing); station dropped from the analytics link. | Gated by permission; station kept. |
| W11–12 | Briefing: no reason when disabled; a stale draft after an error or a selection change; focus could miss. | Hints; the draft is cleared; focus by effect. |
| W13–15 | Untranslated values; citation link text was a UUID; snapshot ID not shown; decision note duplicated; "Required" shown after review. | Translated; "Insight n"; ID shown; one place; only while pending. |
| W16 | Unhandled fetch failures crashed into the error boundary. | Caught on all AI pages. |
| W17 | Stewards were offered analytic insight types. | Data-quality types only. |
| W18 | (live) "Analyst review required.." double full stop. | Message fixed. |

## 3. Accepted, open or blocked

| Item | Status |
|---|---|
| Steward-scope SQL is backed by the per-row check; the per-row check alone catches a mis-scoped SQL change (§5) | **Accepted** (defence in depth) |
| Names and free-text addresses are not redacted by patterns | **Accepted**: the copilot stays disabled; an approved redaction service is a rollout gate |
| `/ai/settings` audits the reason's length, not its text | **Accepted** (free text is never written to the audit log) |
| Card history shows decisions, not notes or refused attempts (those are in the audit log) | **Accepted** |
| Exclusions do not count left-out sensitive or highly restricted records | **Accepted** (a count would reveal them) |
| A stale `change_reason` can persist if an update omits it (the API always sends one) | **Accepted, low** |
| Live demo data: 23 earlier daily-keyed pattern cards remain PENDING beside their new block-keyed ones (one-time effect of B7). Insights cannot be deleted by design. | **Open** (dismiss them in review) |
| Duplicate detector run time (Phase 7 report §5) | **Open** |
| Retention of AI runs and insights | **Open** |
| Playwright, axe or visual tests | **Open** |
| Registry, briefing and workbench AI pages in the browser: `demo.admin` and `demo.analyst` have no known TOTP | **Blocked** (API tests and build cover them) |
| Visual screenshots: the browser pane was hidden, so steps were driven by page scripts and form input | **Blocked** (text, focus, URL and network checked) |

## 4. Verification (run locally, 2026-10-13)

| Check | Result |
|---|---|
| API unit, integration (migrations 0001–0013 and the round trip) and worker tests | **554 passed** (was 531; +23), 1 deprecation warning from before Phase 3 |
| ruff, ruff format, mypy --strict (API 56 files, worker 11) | clean |
| Web: ESLint, tsc, Vitest (**116**, was 115), `next build` | clean |
| OpenAPI regenerated vs committed copy; types `check`; Redocly lint 2.57.0 | in sync; valid |
| `scripts/run_security_checks.sh` | all PASS (bandit, pip-audit, pnpm audit, gitleaks, semgrep, trivy) |
| `scripts/check_docs.py` | no problems |
| Live Compose | AI password added to the local `.env` (generated; not shown). Role updated (LOGIN, worker membership revoked; no memberships remain). Migrated to **0013**. API, worker and scheduler rebuilt; **ai-worker** started. |
| Live AI run (ai-worker) | 20 runs COMPLETED; 20 `AI_RUN` ALLOW events written by the AI login; the container has no worker, app or Redis credentials; no errors |
| Browser (`demo.steward`) | AI tab: type filter offers data-quality types only; no registry link or AI-models nav entry (no `ai.model.read`); briefing link shown (reviewer). Card: snapshot ID shown; notes section; two notes added in turn, each clearing the field, with a new flash focused each time. |

## 5. Mutation checks

| Check | Result |
|---|---|
| Audit fixes: location policy, DQ policy, flag trigger, insight model/type check, run trigger, auditor global read, run visibility, briefing permission, failure handling, log-space Poisson, copilot people terms, briefing validated-only, review session user (0013) | **13 of 13 caught** |
| Phase 7 build checks re-run: sensitive row-level security, extra AI write grant, limitations CHECK, switched-off model, gate loop, steward DQ-only, copilot refusal | **7 of 7 caught** |
| Steward-scope SQL | survives: the per-row check still filters (layered) |

## 6. Files changed

- **Infra:**
  - `infrastructure/docker/postgres/init/02-ai-role.sh` (login, idempotent);
  - `infrastructure/docker/compose.yaml` (ai-worker, postgres env);
  - `.env.example`, `scripts/dev_bootstrap.sh`.
- **API:**
  - `migrations/versions/0013_ai_audit.py` (new);
  - `routes/ai.py`, `ai/copilot.py`, `db.py`.
- **Worker:** `crimemap_worker/ai.py` (login settings, loop, failure handling, detector fixes) and
  `jobs.py` (AI job moved out).
- **Tests:** `tests/integration/test_ai.py`, `tests/integration/conftest.py`, `tests/unit/test_ai.py`,
  `tests/unit/test_routes.py`.
- **Web:**
  - `components/workflow/Forms.tsx`, `components/Shell.tsx`;
  - `components/analytics/AiAnalysis.tsx`, `Workbench.tsx`;
  - `app/(app)/review/insights/[id]`, `review/ai-runs/[id]`, `ai/page.tsx`, `ai/briefing/page.tsx`,
    `review/page.tsx`, flash nonce on four pages;
  - `lib/ai.ts`, `lib/workflow.ts`, `messages/en.ts`;
  - `tests/ai.test.ts`, `tests/workflow.test.ts`.
- **Contract:** `packages/types/openapi.json`, `src/api.d.ts`.
- **Docs:**
  - this file;
  - `ai-governance.md`, ADR-037 (revised);
  - `data-dictionary.md`, `phase-7-report.md`, `README.md`.
