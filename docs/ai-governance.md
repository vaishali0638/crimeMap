# AI Governance — CRIMEMAP (Phase 7)

**Environment:** DEMO, synthetic data only. Model approvals below are demo approvals for synthetic
data, not production approvals. See ADR-037 and ADR-010.

AI in CRIMEMAP assists analysts; it does not make policing decisions. Every output is labelled
**"AI-assisted; analyst review required."** It concerns places, time windows and records, never
people.

## 1. Intended and prohibited uses

| Built (enabled) | Model / rule | Kind |
|---|---|---|
| Data-quality anomalies: a station's share of records without a usable location versus its own baseline; a source's daily volume drop | `dq-anomaly@1.0.0` | statistical (binomial and normal z-scores) |
| Possible duplicates: records close in place and time, same category; scored, never merged | `duplicate-suggest@1.0.0` | rule (weighted score) |
| Geocoding suggestions: the centre of well-located records with the same locality in the same station; low confidence is flagged for mandatory checking | `geocode-suggest@1.0.0` | statistical |
| Emerging place-time patterns: 28-day counts per 1 km cell and category against the previous 24 weeks (Poisson upper tail) | `emerging-pattern@1.0.0` | statistical |
| Explainable clusters: DBSCAN (250 m, at least 8 cases) over the last 28 days, described by size, centre (snapped to 250 m), extent, time span and hours | `cluster-explain@1.0.0` | statistical |
| Template briefings from validated insights, each cited | `briefing-template@1.0.0` | template |

| Not built (disabled) | Model | Why |
|---|---|---|
| Short-horizon place-time review priority (forecast) | `forecast-review-priority@0.1.0` | Draft. None of the 8 rollout gates is met (§4). |
| Analyst copilot (language model) | `copilot-llm@0.0.0` | Draft. No approved deployment. The control path is built and tested (§5). |

**Prohibited** (spec; also listed on each model card):
- suspect, person or offender predictions;
- individual risk scores;
- facial recognition;
- automatic dispatch, deployment or allocation;
- enforcement recommendations without human review;
- inference of criminality;
- public prediction maps;
- unexplained black-box decisions.

The copilot guard refuses questions of these kinds even before the disabled check.

## 2. What the database guarantees (revised by the Phase 7 audit)

The detectors run in their own container, **`ai-worker`**. It holds only the credentials of the
database login **`crimemap_ai`**: no worker, application or broker secrets. `crimemap_ai` is not a
member of any other role, and no role is a member of it, so detector code cannot switch to a role
that may write cases, actions or exports. The audit found that, before the fix, the worker could
switch back out of the role. Tests check that `SET ROLE` is refused both ways.

What `crimemap_ai` can do:
- **Read:** approved columns only (no offence names, narratives or references).
- **Visibility:** sensitive-category and highly restricted incidents are invisible to it, and so are
  their locations and data-quality issues (row-level security on the incident applies through them).
- **Write:** insert AI runs, insight cards, and its own `AI_RUN` audit events (append-only).
- **No grant on:** actions, exports, notes, data-quality changes, case updates, the model registry,
  or reading or changing the audit log.

So AI cannot create an action, export data or modify a case. This is tested by attempting each of
those writes as the AI login.

Insights can be written only into a running run of an approved, enabled rule or statistical model
of the same insight type. A run can only be started for such a model. So no "forecast" or disabled
model can produce cards, even by direct SQL.

Each insight card always carries the following (CHECK constraints):
- evidence and a baseline;
- limitations and alternative explanations;
- an explanation, a confidence with a calibration note, and a data-quality score.

An insight changes only once, by an analyst (the session user), to VALIDATED or DISMISSED, with a
note. Its content never changes.

Detectors use locations with confidence 0.4 or more only. Day windows are in IST (Asia/Kolkata). A
pattern produces at most one card per 28-day block, so a persistent pattern does not re-alert daily.
A detector failure is recorded as a FAILED run with an audited error, and the other detectors
continue.

## 3. Human review

- Analysts (and stewards, for data-quality types) validate or dismiss an insight with a note.
  Validation records a judgement only. It changes no record and creates no action. A geocoding
  suggestion is corrected at source or through the location-correction workflow.
- Every review is audited (`AI_INSIGHT_REVIEW`, states and note length; never the note text). It
  appears in the card's history.
- Briefings use validated insights only.

## 4. Rollout gates for forecasting

All must be true, and evidenced, before `forecast-review-priority` or the `ai_forecasting` flag can
be switched on. The database refuses otherwise:
- **Model switch:** a trigger on the model registry.
- **Flags:** since the Phase 7 audit, also a trigger on the feature flags. The application role may
  change only the AI flags, as the session user, and may switch on forecasting or the copilot only
  while its model is enabled.

| Gate | Status |
|---|---|
| Minimum data-quality threshold configured and met | not met |
| Baseline historical model exists | not met |
| Model beats a simple baseline on a holdout period by the configured margin | not met |
| Error and geographic concentration reviewed | not met |
| Human analyst acceptance workflow tested | not met |
| Model card approved by the accountable owner | not met |
| Security and privacy review passed | not met |
| Rollback / kill switch tested | not met |

## 5. Language-model (copilot) restrictions

- It is disabled: `POST /ai/copilot/queries` answers `503 feature-disabled`. No model is configured.
- An approved adapter would have to pass the built path (`crimemap_api/ai/copilot.py`):
  1. refusal of prohibited questions. This is deliberately broad, and was widened by the audit: any
     question about a person or their identity, home or whereabouts, victims, complainants or
     witnesses, risk, faces, predictions, or enforcement and deployment (including patrols) is
     refused;
  2. redaction of personal identifiers in the question, context and answer: phone numbers in any
     grouping (including landlines), Aadhaar-, PAN- and voter-ID-like numbers in any case, emails
     and vehicle numbers. Names and free-text addresses cannot be removed by patterns, so an
     approved redaction service is a rollout gate;
  3. context built only from data the caller is authorised to see, each item with a citation;
  4. an adapter interface with **no tools** (text only);
  5. answers rejected unless they cite only the given context.
- Questions are never stored or logged; only their length is.

## 6. Kill switches

- **Per model:** `POST /ai/models/{id}/switch`. Needs `ai.config` (system administrator), a sign-in
  in the last 5 minutes and a reason; it is audited (`CONFIG_CHANGE`). A switched-off model's pending
  insights are withdrawn from review, and the worker skips it.
- **Global:** `POST /ai/settings` with `ai_assistance`. While off, no detector runs (the attempt is
  audited) and no insight can be reviewed.
- Both were tested: off, then the detectors produce nothing and reviews are refused; on, and normal
  behaviour resumes.
- Both switches are checked again at the start of every model/region run, so a switch-off takes
  effect at the next run. A model-region step already running finishes.

## 7. Known limitations

- **Thresholds and calibration:** set by hand. No outcome labels exist for synthetic data.
- **Multiple testing** (many cells × categories) is only controlled by a strict p-value; some
  emerging-pattern alerts are chance.
- **Recorded counts** reflect reporting and patrol presence as well as occurrence. Each card says so.
- **Reference date:** the latest record date per region (the demo data is historical). In
  production it would be today.
- **Duplicate suggestions** include case records. This is the spec's "source-ingest validation"
  use: a steward compares the records at source, and nothing is merged or changed.
- **Exclusions** state which records are not used, but not how many sensitive or highly restricted
  records were left out (a count would itself say something about them).
- **Retention:** insights and runs have no retention policy yet (open item).
