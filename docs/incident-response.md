# CRIMEMAP Incident Response

**Environment:** DEMO, synthetic data only. Not a production-readiness claim. This plan covers
security and privacy incidents: misuse by an authorised user, account compromise, data exposure,
tampering, and failures of the controls themselves. Operational failures (service down, slow,
ingestion or detector errors) are in [runbook.md](runbook.md). Roles named below are to be filled
by the owning police unit before production.

## 1. Roles

| Role | Responsibility |
|---|---|
| Incident lead (on-call system administrator) | Opens the incident, coordinates, keeps the timeline |
| Security officer | Decides severity, containment and notification |
| Data owner (unit head) | Approves notification and any disclosure; decides on access restoration |
| Auditor | Reads the audit log, preserves evidence, verifies the chain |
| Legal / data-protection adviser | Advises on statutory reporting and notices |

Separation of duties applies during incidents too: the person whose account is under investigation
does not handle their own case, and an administrator cannot grant themselves access (enforced).

## 2. Severity

| Severity | Examples | First response |
|---|---|---|
| Critical | Confirmed export or exposure of real case data; compromised administrator account; audit chain broken | Immediately, any hour |
| High | Mass export by one user; repeated out-of-scope attempts; unexpected privilege change | Within 1 hour |
| Medium | Repeated denials from one source without success; unusual geographic access | Same working day |
| Low | Single denied request; configuration change that was planned but not announced | Next review |

## 3. Detection

| Signal | Source | Section |
|---|---|---|
| `CrimemapMassExport` | exports per user per hour (audit `EXPORT_REQUEST`) | [Mass export](#mass-export) |
| `CrimemapRepeatedDeniedAccess`, `CrimemapDeniedAccessSpike` | `DENY` audit events per user or address | [Repeated denied access](#repeated-denied-access) |
| `CrimemapUnusualGeographicAccess` | a user opens a region not used in the previous 30 days | [Unusual geographic access](#unusual-geographic-access) |
| `CrimemapPrivilegeChange`, `CrimemapConfigChange` | `PRIVILEGE_CHANGE`, `CONFIG_CHANGE` audit events | [Privilege change](#privilege-change) |
| `audit_chain_broken` (worker log), `GET /api/v1/audit/verification` | daily hash-chain verification | [Audit tampering](#audit-tampering) |
| Rejected batch signatures (`INGEST_REJECTED`) | ingestion | [Forged or altered source data](#forged-or-altered-source-data) |
| BFF `csrf_rejected` log events | web | [Repeated denied access](#repeated-denied-access) |
| Report from a user or third party | any | §4 |

Signals carry counts only. The auditor finds the users, addresses and records concerned in the audit
log (`/api/v1/audit/events`, `audit.read`), which keeps the investigation itself audited.

## 4. Common steps

1. **Open** an incident record: time, signal, who is handling it.
2. **Preserve evidence** before changing anything (§6).
3. **Scope**: from the audit log, list the actor, requests (by request ID), objects, filters,
   exports and their downloads.
4. **Contain** (§5) proportionately: the least disruptive action that stops the harm.
5. **Notify** (§7) when the criteria are met.
6. **Recover**, then **review** (§8).

## 5. Containment actions

| Action | How | Effect |
|---|---|---|
| Revoke a role grant | `POST /api/v1/users/{id}/role-grants/{grant}/revocations` (administrator, fresh sign-in, reason; audited) | Access ends on the next request |
| Disable the account | Keycloak admin console: disable the user and sign out their sessions | No new sign-in; existing BFF sessions fail at the next token refresh |
| End all web sessions | Rotate `CRIMEMAP_SESSION_KEY` (runbook §6) | Everyone signs in again |
| Stop exports | Revoke export grants; expired exports are purged hourly; downloads are audited (`EXPORT_DOWNLOAD`) | No new exports |
| Switch off AI | Global AI switch or the model's switch (`/ai`) | No detector runs, no reviews |
| Stop ingestion | Stop the `worker` (and `scheduler`); files stay in the inbox | No new data |
| Rotate exposed secrets | [runbook.md](runbook.md) §6 | Closes credential misuse |

## 6. Evidence preservation

- **Audit log**: append-only and hash-chained (updates and deletes are refused by trigger). Ask an
  auditor to record `GET /api/v1/audit/verification` and the relevant events with their request IDs
  and sequence numbers.
- **Database**: take an encrypted backup immediately (`scripts/backup_restore.py backup`, runbook
  §4.1). Its manifest records the audit-chain state; keep the file and manifest read-only.
- **Logs**: save the API, worker, AI worker and BFF logs for the period (`scripts/compose.sh logs
  --since <time> <service>`). They contain request IDs, not request bodies.
- **Identity provider**: export the user's Keycloak events and sessions.
- **Exports**: the export record (file hash, classification, downloads) is in the database.
- Record who collected what and when (chain of custody). Do not restart or repair systems that hold
  evidence until it is collected.

## 7. Notification

Decided by the security officer with the data owner and legal adviser. Requirements must be
confirmed by legal for the deployment; at the time of writing they include:
- **CERT-In**: Indian Computer Emergency Response Team directions (April 2022) require reporting of
  specified cyber security incidents within 6 hours of noticing them.
- **Data protection**: personal data breaches under the Digital Personal Data Protection Act, 2023
  and its rules (notice to the Data Protection Board and affected persons).
- **Internal**: the unit head, and the departmental vigilance or disciplinary process for misuse by
  an authorised user.

In DEMO every record is synthetic, so no statutory notification applies; exercises record what
would have been sent.

## 8. Recovery and review

- Restore access only on the data owner's decision, with a new grant and reason (audited).
- Within 10 working days, review: timeline, root cause, what detection caught or missed, actions
  (owner and date), threshold changes for alerts.
- Update the threat model and this plan.

## 9. Playbooks

### Mass export

`CrimemapMassExport`: one user requested 5 or more exports in an hour.
1. Auditor: list the user's `EXPORT_REQUEST`, `EXPORT_GENERATED` and `EXPORT_DOWNLOAD` events (purpose,
   filters, classification, file hashes).
2. Contact the user's supervisor to confirm the business need (a briefing deadline is a common
   reason).
3. If not justified: revoke the export grant (§5), preserve the export records, and treat downloaded
   files as disclosed (High; Critical with real data).

### Repeated denied access

`CrimemapRepeatedDeniedAccess`, `CrimemapDeniedAccessSpike`, or BFF `csrf_rejected` events.
1. Auditor: group the `DENY` events by actor or address and by reason (`NO_PERMISSION`,
   `OUT_OF_SCOPE`, `MFA_REQUIRED`, `REAUTH_REQUIRED`, authentication failures).
2. Many `AUTHN_FAILED` from one address: probing or a misconfigured client. The rate limiter already
   slows it; block the address at the network edge if it continues.
3. `OUT_OF_SCOPE` by an authenticated user (for example a station officer requesting another
   station's records): ask the supervisor; it may be a mistaken link, or an attempt to reach data
   outside the user's territory (High).
4. A spike across many users after a deployment usually means a configuration error: check the
   last change ([deployment.md](deployment.md)).

### Unusual geographic access

`CrimemapUnusualGeographicAccess`: a user opened a region they had not used in the previous 30 days.
1. Auditor: find the user and region (`MAP_QUERY`, `ANALYTICS_QUERY`, `RECORD_VIEW` events with
   `filters.region`).
2. New staff, a transfer or a new grant explain most cases: check `PRIVILEGE_CHANGE` events for the
   user.
3. Otherwise confirm with the supervisor; access is always within the user's granted scope (the API
   enforces it), so the question is whether the grant is still appropriate.

### Privilege change

`CrimemapPrivilegeChange`, `CrimemapConfigChange`.
1. Match the change (actor, target user, role, territory, reason) with an approved request.
2. Unmatched: revoke it (§5), check the granting administrator's account for compromise, and review
   their other recent changes.
3. AI or feature-flag changes: confirm they were planned ([ai-governance.md](ai-governance.md)).

### Audit tampering

The daily chain verification failed, or an auditor's verification reports a broken chain.
1. Critical. Preserve evidence (§6) before anything else; compare with the last backup manifest's
   `audit_chain.checked`.
2. The database refuses updates and deletes on the audit log; a break means direct superuser access
   or storage corruption. Review superuser access and host logs.

### Forged or altered source data

A batch file failed signature verification (`INGEST_REJECTED`, reason "signature does not verify").
1. Nothing was staged. Keep the rejected file (in the inbox's `rejected/` folder) as evidence.
2. Confirm with the source owner; if they did not send it, treat the drop location as compromised
   and rotate the signing key ([runbook.md](runbook.md) §6).
