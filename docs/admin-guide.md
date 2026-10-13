# CRIMEMAP Admin Guide

**Environment:** DEMO, synthetic data only. Not a production-readiness claim.

This guide is for system administrators (`SYSTEM_ADMIN`) and data stewards (`DATA_STEWARD`). It
covers users and roles, multi-factor authentication, AI switches, feature flags, boundary approvals,
the audit log and demo data. It describes what is built. Items that are planned or not built are
marked **not built**.

For everyday use of the screens, see the [user guide](user-guide.md). Operator tasks (services,
monitoring, backups, restores, deployment) are in the [runbook](runbook.md) and the
[deployment guide](deployment.md).

## 1. What administrators can and cannot do

| Permission | Held by | Use |
|---|---|---|
| `admin.users` | System administrator | List users and their role grants; grant and revoke roles |
| `admin.config` | System administrator | Boundary approval, administrator slot |
| `ai.config` | System administrator | AI kill switches only |
| `ai.model.read` | System administrator, analyst, auditor | Read the model registry |
| `audit.read` | System administrator (metadata only), auditor (full) | Read the audit log; verify its chain |
| `lineage.read` | System administrator, data steward (own region), auditor | Data Health; boundary version history |
| `dq.manage` | Data steward (own region) | Data-quality queues; boundary approval, steward slot |

A system administrator holds **no case-data permission**: no map, record, analytics or export
access. Administration does not give sight of records.

There is **no administration screen** in the web application. The administrator actions below are
API calls under `/api/v1`, made with the administrator's own signed-in session. The exceptions are
the AI switches, which are on the "AI models" page. An administration screen for users, roles and
the audit log is **not built**.

## 2. User lifecycle

### 2.1 How users come into existence

1. A person's account is created in the identity provider (Keycloak, realm `crimemap-demo`).
   CRIMEMAP has no self-registration.
2. The first time that person makes a request to CRIMEMAP, the API creates an application user for
   them, keyed on the identity-provider subject. This user has **no role grants**, so they can see
   nothing beyond the screen frame ("No roles assigned — ask an administrator").
3. An administrator grants a role (§3).

Users created outside PRODUCTION are marked as demo users. In PRODUCTION they are not.

### 2.2 Demo users

The nine demo identities are fixed in `services/api/src/crimemap_api/demo_identities.py` and in the
Keycloak realm import. A test checks that the two agree. They are seeded with
`crimemap_api.cli seed-demo-identities`.

| Username | Role | Territory |
|---|---|---|
| `demo.admin` | `SYSTEM_ADMIN` | Global |
| `demo.steward` | `DATA_STEWARD` | Region PDY |
| `demo.analyst` | `CRIME_ANALYST` | Regions PDY, KKL |
| `demo.officer` | `STATION_OFFICER` | Station PDY-S01 |
| `demo.sho` | `SHO` | Station PDY-S01 |
| `demo.supervisor` | `DISTRICT_OR_REGION_SUPERVISOR` | Region KKL |
| `demo.leadership` | `SENIOR_LEADERSHIP` | Global, aggregates only |
| `demo.auditor` | `AUDITOR` | Global |
| `demo.viewer` | `DEMO_USER` | Global, aggregates only |

Passwords are never stored in the code or the realm file. `scripts/dev_bootstrap.sh` generates them
into the git-ignored `secrets/demo-credentials.txt` (mode 600). Do not copy them into documents,
tickets or chat.

### 2.3 Listing users and grants

| Call | Returns |
|---|---|
| `GET /api/v1/users` | Users, 25 per page by default (up to 100), with status, MFA status, last sign-in and demo flag |
| `GET /api/v1/users/{user_id}/role-grants` | That user's grants, including revoked ones |
| `GET /api/v1/roles` | Each role with its permissions |
| `GET /api/v1/permissions` | Every permission code |

Each listing is recorded in the audit log.

### 2.4 Leaving users

There is no CRIMEMAP call to disable or delete a user (**not built**). To remove access, revoke every
grant (§3.3) and disable the account in Keycloak.

## 3. Granting and revoking roles

### 3.1 Rules

- Only a system administrator (`admin.users`) can grant or revoke.
- A sign-in **within the last 5 minutes** is required. Otherwise the call is refused as needing
  re-authentication.
- A **reason** of 3–500 characters is required for every grant and revocation.
- **Nobody can change their own roles.** A grant or revocation for yourself is refused (409,
  separation of duties) and the refusal is audited.
- Every change is audited (`PRIVILEGE_CHANGE`) and written to the security log.
- Grants are valid from the moment they are written, by the database clock.

### 3.2 Granting a role

`POST /api/v1/users/{user_id}/role-grants` with:

| Field | Rule |
|---|---|
| `role` | One of the nine roles |
| `region_codes` | Up to 4 region codes (2–5 capital letters); unknown codes are refused |
| `valid_to` | Optional. A future time with a time zone; the grant ends then |
| `reason` | 3–500 characters |

**Station-scoped grants cannot be made through the API** (**not built**). The request takes regions
only. Station officers and SHOs therefore get their station only from the demo seed. A station
officer or SHO granted with regions only, and no station, has no case-data access.

The scope a role uses is fixed by the permission matrix: some permissions are global, some apply
within the grant's regions, some within its stations, and `action.complete` for station officers
applies only to actions assigned to them. See
[data-classification-and-access.md](data-classification-and-access.md).

### 3.3 Revoking a role

`POST /api/v1/users/{user_id}/role-grants/{grant_id}/revocations` with a `reason` (3–500
characters). A grant already revoked is refused (409). Revoked grants stay listed with their
revocation time.

## 4. Multi-factor authentication

### 4.1 What CRIMEMAP enforces

The API ignores a user's role grants unless their token proves multi-factor authentication. The one
exception, as coded (`MFA_EXEMPT_ROLES`), is `DEMO_USER`, which sees synthetic aggregates only.

A user whose grants are ignored for this reason sees: "Your roles need multi-factor authentication,
but this session was not verified with your authenticator …". The usual cause is the sign-in on which
they enrolled their authenticator; signing out and in again fixes it.

### 4.2 What the demo identity provider enforces

| Setting | Value |
|---|---|
| Sign-in flow | Username and password, then a one-time code; both required for every user |
| First sign-in | Each demo user must set up an authenticator app |
| One-time codes | TOTP, 6 digits, 30-second period |
| Password policy | At least 14 characters; an upper-case letter, a lower-case letter and a digit; not the username or email; not one of the last 3 |
| Failed sign-ins | Temporary lock after 5 failures, up to 15 minutes; no permanent lock |
| Sessions | Idle timeout 15 minutes; maximum 8 hours; access tokens 5 minutes |
| Self-registration, password reset, "remember me", email sign-in | Off |

These are realm settings. They are changed in Keycloak, not in CRIMEMAP.

### 4.3 Steps that need a fresh sign-in

A sign-in within the last 5 minutes is required for:
- granting and revoking roles;
- boundary approvals;
- AI model switches and the global AI switch;
- exports (CSV and PDF).

Granting or using `restricted.read` would also need it; no role holds that permission and it is out
of scope for the demo.

## 5. AI switches

AI switches need `ai.config` (system administrator only), a sign-in within the last 5 minutes and a
reason of 10–500 characters. Each change, and each refusal, is audited as `CONFIG_CHANGE`. The audit
event records the reason's length, not its text.

### 5.1 On the "AI models" page

1. Open "AI models".
2. Under "AI switches", the current state of "AI assistance (all detectors and reviews)", "Analyst
   copilot (language model)" and "Forecasting" is shown. The administrator can change "AI assistance"
   here.
3. Each model card has its own switch. Write the reason ("Reason for changing: …"), choose "Switch
   off: …" or "Switch on: …" and confirm.

A model that is not approved shows "Not approved: it cannot be switched on." An approved model whose
rollout gates are not met shows "Rollout gates not met: it cannot be switched on." The database
refuses both, not just the page.

### 5.2 Through the API

| Call | Effect |
|---|---|
| `POST /api/v1/ai/models/{model_id}/switch` | Switch one model on or off |
| `POST /api/v1/ai/settings` with `flag` = `ai_assistance` | Global AI switch |
| `POST /api/v1/ai/settings` with `flag` = `ai_copilot` or `ai_forecasting` | Allowed on only while that model is enabled, which needs all its rollout gates |

### 5.3 What a switch does

- **Model off:** its pending insights are withdrawn from review and the worker skips it.
- **Global off:** no detector runs (the attempt is audited), no insight can be reviewed, and template
  briefings are refused.
- Both switches are checked again **at the start of every model and region run**. A switch-off takes
  effect at the next run; a step already running finishes.

Forecasting (`forecast-review-priority`) and the copilot (`copilot-llm`) are drafts and are **not
built** for use. None of the 8 forecasting rollout gates is met. See [ai-governance.md](ai-governance.md).

## 6. Feature flags

Feature flags are stored in the database table `core.feature_flag`. These are the flags as built:

| Flag | Default | Effect | How it is changed |
|---|---|---|---|
| `ai_assistance` | on | All AI detectors, reviews and briefings | §5 |
| `ai_copilot` | off | The language-model copilot | §5; only while its model is enabled |
| `ai_forecasting` | off | Forecasting | §5; only while its model is enabled |
| `civic_context` | off | Civic Trends and civic-context operational records | No CRIMEMAP call (**not built**); the application may change only the AI flags |

There are no other feature flags.

## 7. Boundary approvals

New station or beat boundary versions take effect only with two approvals by two different people:
one in the administrator slot and one in the steward slot.

1. An operator proposes a version with `crimemap_api.cli propose-boundary <unit_code> <geojson>`.
   Invalid polygons, and polygons more than 0.1 % outside the parent's current boundary, are refused.
   The proposal is audited (`BOUNDARY_PROPOSED`).
2. A system administrator approves with `POST /api/v1/boundaries/{boundary_id}/approvals` and
   `as_role` = `SYSTEM_ADMIN` (needs `admin.config`).
3. A data steward whose region covers the boundary approves with the same call and `as_role` =
   `DATA_STEWARD` (needs `dq.manage` in that region).
4. When the second approval lands, the new version comes into force and the old one is retired.

Each slot needs a fresh sign-in. Each slot is filled once. The same person cannot fill both slots
(409, separation of duties). Every approval and refusal is audited (`BOUNDARY_APPROVAL`). Boundary
version history needs `lineage.read`.

Records ingested earlier keep the beat they were given at ingestion. Re-assigning history to a new
boundary is **not built**.

## 8. Audit log

The audit log is append-only and hash-chained. Every request that reads or changes data, and every
refusal, writes an event. Reading the log is itself audited.

| Call | Who | Returns |
|---|---|---|
| `GET /api/v1/audit/events` | Auditor (full); system administrator (metadata only) | Events, newest first; up to 100 per page; filter by `action`; page with `before_seq` |
| `GET /api/v1/audit/verification` | Auditor; system administrator | Whether the chain is intact, how many events were checked, and the first bad sequence number if not |

Metadata includes the time, action, result, reason, actor, roles, object, request id, purpose and
environment. The auditor also sees the IP address, filters and before and after metadata. Free text
(notes, rationales, review notes, AI questions) is never written to the audit log; only its length
is.

Useful `action` values include `PRIVILEGE_CHANGE`, `CONFIG_CHANGE`, `BOUNDARY_APPROVAL`,
`WORKFLOW_TRANSITION`, `AI_INSIGHT_REVIEW`, `AUDIT_VIEW` and `AUDIT_VERIFY`.

An audit log screen is **not built**.

## 9. Data stewards

Data stewards work in their own region. Their day-to-day screens are in the
[user guide](user-guide.md) (§10):
- the "Data quality" and "Possible duplicates" queues;
- deciding "Map / location correction request" records (and no other kind);
- data-quality AI insights;
- the "Data Quality" section of the Analytics Workbench;
- "Data Health".

They also fill the steward slot in boundary approvals (§7). Applying approved location corrections to
the analytical location is **not built**.

## 10. Demo data rules

- CRIMEMAP runs in one of three environments: `DEMO` (the default), `SANDBOX` or `PRODUCTION`.
- Demo and sandbox deployments contain synthetic data only. Every synthetic row is flagged as demo,
  and every screen shows "DEMO / SYNTHETIC DATA".
- **Production refuses demo data.** The API will not start in `PRODUCTION` if any demo row, demo user
  or demo feed is present. Every demo seeder also refuses `PRODUCTION`.
- No real police data and no real personal data may be loaded into any environment. See
  [demo-data.md](demo-data.md).

## 11. Where operator tasks live

| Task | Document |
|---|---|
| Services, monitoring, operational alerts, backups and restore, disaster recovery, key and secret rotation, routine tasks | [runbook.md](runbook.md) |
| Security incidents | [incident-response.md](incident-response.md) |
| Building and deploying; environment configuration | [deployment.md](deployment.md) |
| Architecture and decisions | [architecture.md](architecture.md), [decision-log.md](decision-log.md) |
| Threats and controls | [threat-model.md](threat-model.md) |
