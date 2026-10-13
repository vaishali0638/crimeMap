# CRIMEMAP User Guide

**Environment:** DEMO, synthetic data only. Not a production-readiness claim.

This guide is for crime analysts, station officers, Station House Officers (SHOs), district or
region supervisors, data stewards, senior leadership and auditors. It describes the screens as they
are built today. Anything not yet built is marked **not built**.

System administrators should also read the [admin guide](admin-guide.md). Starting, stopping and
deploying the system are covered in the [runbook](runbook.md) and the [deployment guide](deployment.md).

## 1. Before you start

- Every record, station, beat and boundary in this deployment is **synthetic**. Boundaries are not
  official jurisdictions. Patterns in the demo data are illustrative, not findings about real places.
- What you open, search and change is recorded in the audit log.
- You see only what your role and territory allow. A territory is a set of regions (for example
  `PDY`, `KKL`) or stations (for example `PDY-S01`). Requests outside it are refused, and the
  refusal is recorded.
- Free-text fields (notes, narratives, rationales) must describe places, times and records only. Do
  not enter names, phone numbers, ID numbers or other personal details. The system refuses many of
  them.

## 2. Signing in

### 2.1 Demo accounts

The demo has one fictional user per role (§13). Usernames and the shared demo password are written
by `scripts/dev_bootstrap.sh` to `secrets/demo-credentials.txt` on the machine that runs the demo.
Ask the person who set up the demo for them. They are never printed in documentation.

### 2.2 First sign-in: enrol an authenticator app

1. Open CRIMEMAP. You are sent to the sign-in page.
2. Enter your username and password.
3. You are asked to set up an authenticator app. Scan the code with an app on your phone that
   supports time-based one-time codes (TOTP: 6 digits, changing every 30 seconds), then enter the
   current code.
4. **Sign out and sign in again.** The sign-in where you first set up the app is not counted as
   verified with your authenticator. Until you sign in again, screens that need your roles show:
   "Your roles need multi-factor authentication, but this session was not verified with your
   authenticator (this happens on the sign-in where you first set it up). Sign out and sign in again
   with your one-time code."

### 2.3 Every later sign-in

Enter your username, password and the current one-time code from your app. The demo identity
provider asks every user for the code. CRIMEMAP ignores the grants of every role except the demo
viewer (`DEMO_USER`) unless the session was verified with the code.

| Rule | Value in the demo identity provider |
|---|---|
| Sign-in name | Username only (not email) |
| Password | At least 14 characters, with an upper-case letter, a lower-case letter and a digit; not your username or email; not one of your last 3 passwords |
| Failed attempts | After 5 failures the account is locked for a while (up to 15 minutes); the lock is temporary |
| Idle session | Ends after 15 minutes without activity |
| Longest session | 8 hours |
| Self-registration and "forgot password" | Not available. Ask an administrator. |

### 2.4 Fresh sign-in for sensitive steps

Some steps need a sign-in **within the last 5 minutes**: exporting a report (§8), and the
administrator steps in the admin guide. If your sign-in is older, the screen says so (for example
"Exports need a fresh sign-in. Sign in again, then generate the export.") and offers "Sign in
again". Sign in, then repeat the step.

### 2.5 Your account menu

The "Account" menu shows your name, your "Roles" (or "No roles assigned — ask an administrator"),
whether "Multi-factor authentication" is verified for this session, and "Sign out". Signing out ends
both your CRIMEMAP session and your identity-provider session ("You have signed out").

## 3. The screen frame

### 3.1 Safeguard strip

A dark strip at the top of every page shows:
- the environment: **"DEMO / SYNTHETIC DATA"** (or "SANDBOX"); demo and sandbox deployments
  contain synthetic data only;
- the classification: **"RESTRICTED — Internal Police Use Only"**;
- data freshness: "Data refreshed: {time}", "Data refresh: no data loaded" or "Data refresh: status
  unavailable", and a warning such as "1 source feed is stale" when a feed is late.

Check freshness before relying on any number.

### 3.2 Navigation

The main navigation has "Home Map", "PY Dashboard", "Analytics", "Review Queue" and "Reports".
"AI models" appears if you may read the model registry, and "Data Health" if you may read source and
lineage information. The "Review Queue" link shows a count of items waiting for you.

The shell also has a global search box ("Search records, stations, localities"), a "Notifications"
button and a "Help and accessibility" menu. **Notifications are not built**: the button shows a fixed
message only.

## 4. Home Map

The Home Map is the first page. It shows recorded cases for one region at a time (the four regions
are not contiguous). Choose the region with the region switcher or the locator ("Regions of
Puducherry (synthetic outlines)").

### 4.1 Filters

The "Map filters" bar has "Region", "Station", "Beat", "Category", "Record type", "Period" ("From",
"To"), "Time basis" ("Occurrence time" or "Report time"), "Case status" and "Location confidence"
("Any", "At least medium (0.4)", "High only (0.8)"). The period may be at most two years.

Changing a filter does not update the map until you select "Apply" (the bar says "Filters changed —
select Apply to update the map."). "Reset" returns to the defaults. Choosing a station or beat
outside your territory is refused: "These filters are outside your authorised territory. The attempt
has been recorded."

The search box ("Search reference, station, beat or locality") finds records, units and localities
in your territory only. Landmark and road-segment search are not available in the demo data.

### 4.2 Layers and how cases are shown

The "Layers" panel turns on "Cases", "Station boundaries", "Region boundaries", "Beats" and "Event
markers (approved) and observations". "Civic context (disabled)" is off.

"Show cases as" offers "Points", "Hexagon grid" (with a "Hexagon size") or "Beat totals". Roles
without point access see aggregates only. When too many points are selected, the map shows hexagons
and says why.

### 4.3 Legend and status bar

The "Legend" explains symbols and shading (lighter for fewer records, darkest for 40 or more). A
record outlined on the map has a data-quality warning.

The status bar gives "{count} selected", "{count} usable locations" and "{count} excluded". Select
"Why excluded?" to see the reasons, for example "No location", "Outside every synthetic beat",
"Residential (shown only in aggregates)" or "Sensitive category (aggregates only)". Sensitive
categories are counted only where there are 3 or more in a cell. It also states the location
precision you are seeing (for example "Locations snapped to 250 m"). "About this analysis" explains
the method.

### 4.4 Features in view (keyboard list)

"Features in view (keyboard list)" lists every feature on the map as text. Choose one to open its
summary, without using the mouse or the map. If the list is long it shows the first part; narrow the
filters to list the rest.

### 4.5 Record summary

Selecting a point or a list entry opens the "Record summary" panel. It shows category, offence
heading or event type, occurred and reported times, locality, location confidence, station, region
and status. Some roles see times to the hour only ("Times are shown to the hour for your role.").
No panel shows coordinates.

| Button | Who sees it | What it does |
|---|---|---|
| "Open permitted details" | Roles with record detail access | Shows record type, act, beat, reporting station, data quality, open data-quality issues, source version and received time |
| "Open source record" | Everyone who can open the summary | Shows which source the record came from. No source system is connected in the demo. |
| "Flag data issue" | Roles that may add operational records | Sends a data-quality flag to the data steward. Describe the problem in 5–500 characters. |
| "Add analyst note" | Roles that may add operational records | Adds a plain-text note, stored as RESTRICTED. Notes can be added to records and hexagon cells; beat notes are not available yet. |

### 4.6 Operational records

"+ Add Operational Record" adds context to the map. It never creates or changes an FIR or case
record; FIRs are created only in the official source system.

| Type | Review |
|---|---|
| "Analyst note" | None |
| "Operational observation" | None |
| "Map / location correction request" | Reviewed by someone else. Open the record on the map first. |
| "Event marker (needs approval)" | Reviewed by someone else before it appears on the map |
| "Action completion" | Not available here: completion is recorded on the approved action (§7) |
| "Verified civic-context reference (feature off)" | Not available: the civic-context feature is off |

Pick a point on the map ("Pick a point on the map"), or press Escape and type the latitude and
longitude. Records reviewed by others appear in the Review Queue under "Operational records" for SHOs,
supervisors and stewards (stewards decide location corrections only). You can never decide a record
you created ("You created this record; someone else must review it.").

## 5. PY Dashboard

The PY Dashboard needs aggregate map access.

1. Choose a "Region" (or "All Regions"). The map shows one region at a time.
2. Use "Search station" to narrow the "Stations" list.
3. Optionally set "Category", "From", "To" and "Time basis".
4. Select "View Details" for a station. "Station details" shows its jurisdiction (beats, area and
   boundary version), records in the selected period, case records by status ("Active", "Detected",
   "Closed") and records by category. "View Station on Map" opens the Home Map there.
5. Select "TRENDS" for the trend boards: "Top Crime Spot", "Police Station Crime Rate", "Region",
   "Location", "Time", "Month" and "Week". Each board states its period, category, time basis, scope,
   measure, comparison and data-quality caveat. "View on Map" opens a row on the Home Map.

Rankings show higher or lower recorded counts in the selected period only. Where no population or
other denominator exists, the rate is shown as unavailable, never estimated.

## 6. Analytics Workbench

The Analytics page needs analytics access. Data stewards see the "Data Quality" section only.
Senior leadership and the demo viewer see aggregates only.

### 6.1 Sections

| Section | Contents |
|---|---|
| "Overview" | Key figures, trend, categories, data-quality status, map, open review items |
| "Location Trends" | Hexagon, beat or station counts; repeat locations; persistence; density surface |
| "Case Trends" | Series, by category, day of week × hour, calendar, reporting delay, case status (if permitted) |
| "Civic Trends (disabled)" | Off: no verified civic source is connected |
| "AI Analysis" | Emerging place-time patterns and clusters awaiting review (§9); needs AI insight access |
| "Comparison" | Period, year, region, station, beat or category comparisons; needs comparison access |
| "Data Quality" | Data-quality measures, location method and confidence, open issues, feed health |

### 6.2 Filters and views

The "Analysis filters" bar adds "Locality", "Time of day (IST)", "Comparison period" ("Previous
period", "Same period last year", "None"), "Spatial unit" and "Time unit" to the Home Map filters.
The header shows the active filters, records selected and excluded, data quality, method and
caveats.

Each section has four views: "Map", "Chart", "Table" and "Explanation". The Table view has the exact
numbers behind every chart and map. The Explanation view states the method and its limits.

### 6.3 Actions on an analysis

| Button | What it does |
|---|---|
| "Open Evidence" | "Create snapshot of this analysis": an immutable copy of the data, filters and method with a SHA-256 fingerprint |
| "Add Analyst Note" | Adds a note attached to an evidence snapshot of this exact analysis |
| "Export Report" | See §8 |
| "Create Pattern Review" | Saves a draft pattern review from this analysis (§7.1) |

## 7. Review Queue and the pattern review workflow

### 7.1 Review Queue tabs

You see only the tabs your role can work. If you have none, the page says so.

| Tab | Shown to | Contents |
|---|---|---|
| "Data quality" | Data stewards | Data-quality issues (§10) |
| "Possible duplicates" | Data stewards | Possible duplicate records |
| "Operational records" | SHOs, supervisors, data stewards | Operational records awaiting review (§4.6) |
| "Pattern reviews" | Analysts, SHOs, supervisors | Reviews awaiting your decision, and your own drafts or returned reviews |
| "Actions" | Station officers, SHOs, supervisors | Actions awaiting your approval, and approved actions you may complete |
| "Outcome reviews due" | Analysts, SHOs, supervisors | Completed actions whose outcome review is due or overdue |
| "AI insights" | Analysts, data stewards, SHOs, supervisors, auditors | AI insight cards (§9) |

Queue tables show priority, type, date, area, category, owner, due date ("Due today", "Overdue by 3
days"), status, an evidence link and the "Next step". Filters include "My drafts only", "Assigned to
me", "My outcome reviews" and "Overdue only". Priority is High when an item is overdue or a pending
review covers 50 or more records. Each item has a "History" of every change of state, who made it,
when and why.

### 7.2 Separation of duties

- Nobody decides their own pattern review ("You wrote this review, so someone else decides it.").
- Nobody approves an action they proposed ("Awaiting approval by someone other than the proposer.").
- The person who approved an action does not record its completion.
- The person who completed an action does not review its outcome.
- Every decision is confirmed ("{choice}: are you sure? This cannot be undone.") with "Yes,
  {choice}", for example "Yes, Approve".

### 7.3 Step by step

| Step | Who may do it | Where and how |
|---|---|---|
| 1. Create a pattern review | Analyst, SHO, supervisor (in their territory) | Analytics → "Location Trends" with "Hexagon grid" and a comparison period → "Create Pattern Review". Choose the "Pattern type" and "Cells", write the "Analyst narrative (at least 20 characters)" and "Recommended next step", then "Create draft". |
| 2. Submit for decision | The author only | Review Queue → "Pattern reviews" → open the draft. Add the "Analyst assessment" (at least 10 characters) and select "Submit for decision". You may "Save changes" to the narrative first. |
| 3. Decide | SHO or supervisor, not the author | Open the review. Choose "Approve", "Return for revision" or "Reject", give a "Rationale" (at least 10 characters) and confirm. A returned review goes back to the author ("Returned to you: revise and resubmit"). |
| 4. Propose an action | Analyst, SHO, supervisor | From an approved review: "Propose an action". Choose the "Action type", "Cells (from the pattern review)", "Station", "Starts" and "Ends" (at most a year), and a "Description" (20–2000 characters). Select "Propose action". |
| 5. Approve or reject the action | SHO (own station) or supervisor (own region), not the proposer | Review Queue → "Actions". Give a "Rationale", optionally choose the "Assigned officer", then "Approve" or "Reject" and confirm. |
| 6. Record completion | The assigned station officer, or the station's SHO; not the approver | After the action has started: "Record completion" with "Completion evidence" (at least 10 characters) and a "Reference (e.g. log number)". |
| 7. Start the outcome review | Analyst, SHO, supervisor; not the person who completed the action | Due 28 days after the action ends. "Start the outcome review": give the "Baseline (before)" and "After" periods and an optional "Comparison area", then "Compute and start". The server counts recorded cases in the action's cells, the surrounding ring and the comparison area. The counts are then fixed. |
| 8. Finalise the outcome review | The reviewer who started it | Write the "Displacement and diffusion notes" and a "Conclusion" (at least 20 characters), add at least one "Evidence references" entry, then "Save draft" or "Finalise". A final outcome review cannot be changed. |

Action types are "Patrol visibility", "Community engagement", "Joint inspection", "Environmental
request (e.g. lighting)", "Data-quality fix" and "Other". Actions are human decisions about places
and times; none targets a person, and nothing is triggered automatically.

Period rules for outcome reviews: the baseline ends before the action starts; the after period starts
when the action starts or later and ends today or earlier; each period is at most a year.

Outcome counts are descriptive. A rise in the ring next to the action area may suggest displacement;
a fall may suggest diffusion of benefit. The counts alone show neither. There is no effectiveness
score.

Station-scoped users (SHOs, officers) do not see region-wide evidence. Such evidence shows as
"Outside your territory view".

## 8. Reports and exports

Exporting needs your role's export permission (analysts, SHOs and supervisors), a declared purpose
and a sign-in within the last 5 minutes.

1. In the Analytics Workbench select "Export Report".
2. Choose the "Format": "CSV (aggregated data)" or "PDF briefing".
3. Choose the "Purpose": "Briefing", "Operational review", "Pattern analysis" or "Outcome review".
4. Select "Generate export". If asked, select "Sign in again", sign in, and generate it again.
5. Download the file from the link ("Ready (expires {expires}):") or from the Reports page.

Every export:
- contains aggregated tables only;
- carries the INTERNAL / RESTRICTED watermark, the DEMO / SYNTHETIC DATA label, and its filters,
  method and data-quality notes;
- can be downloaded only by you, and each download is recorded;
- expires after 24 hours and is then deleted.

The "Reports" page lists your exports from the last 24 hours with format, created and expiry time,
classification and download count. Map-image exports are **not built**. A library of approved reports
is **not built**.

## 9. AI insights

AI in CRIMEMAP uses explainable rules and statistics. It concerns places, time windows and records,
never people. Every output is labelled **"AI-assisted; analyst review required."**

Insight types: "Data-quality anomaly", "Possible duplicate", "Geocoding suggestion", "Emerging
place-time pattern" and "Cluster".

### 9.1 Reading an insight

Open one from Review Queue → "AI insights". The "AI insight" card shows what and where, the model or
rule, place, time window, confidence and calibration, a data-quality score, exclusions, evidence and
baseline, limitations and alternative explanations. "Open evidence (input snapshot)" shows the
detector run and a SHA-256 of its inputs. "Open the place and window in the Analytics Workbench"
opens the analysis.

### 9.2 Reviewing an insight

Analysts review all insight types in their territory. Data stewards review data-quality types only.
SHOs, supervisors and auditors may read but not review ("You may read this insight but not review
it.").

1. Read the evidence, limitations and alternative explanations.
2. Write a "Review note".
3. Select "Validate" if the evidence supports the insight, or "Dismiss" if it does not, and confirm.

An insight is reviewed once. Validating records your judgement only: it changes no record and
creates no action. "Add a note" adds further restricted notes to the card.

If AI assistance is switched off, the queue says "AI assistance is switched off: no new insights are
produced and none can be reviewed." If one model is switched off, its insights are not reviewed.

### 9.3 Template briefing

Reviewers can open "Template briefing", choose validated insights (up to 20), give a "Title" and
select "Make draft briefing". The briefing is filled from a fixed template, cites each insight, and is
a draft you edit and own. Nothing is stored or sent.

### 9.4 Model registry

"AI models" shows each model's card: intended and prohibited uses, features, evaluation, bias notes,
owner, approval, rollout gates and whether it is on. Forecasting and the analyst copilot (language
model) are **not built** and stay off.

## 10. Data-quality issues

Data stewards work these from Review Queue → "Data quality" and "Possible duplicates". Issues come
from automatic rules during ingestion, AI duplicate suggestions, and "Flag data issue" on the map.

Filter by "State", "Severity", "Issue type", "Region" and "Assigned to me", then "Apply filters".

| From | Button | Result |
|---|---|---|
| "Open" | "Assign to me" | "Assigned" to you |
| "Open" or "Assigned" | "Resolve" | "Resolved": you fixed it at source |
| "Open" or "Assigned" | "Accept as is" | "Accepted as is": the record stays as it is |
| "Assigned" | "Reopen" | "Open" |
| "Resolved" or "Accepted as is" | "Reopen" | "Open" |

"Resolve" and "Accept as is" need a "Resolution note" (5–2000 characters, no personal details).
If someone else changed the issue first, the page says "This issue changed in the meantime. Refresh
and try again."

Highly restricted records show "Highly restricted record — case facts are not shown". Approved
location corrections are **not yet applied** to the analytical location.

The "Data Health" page (stewards, administrators, auditors) shows source feeds, the last batch,
dead letters, ingestion reconciliation for the last 30 days and open issues by type. Only synthetic
demo feeds are connected; government systems are listed as interface stubs.

## 11. Accessibility

- **Skip link:** press Tab on any page to reveal "Skip to main content".
- **Keyboard:** Tab moves between controls; Escape closes panels and dialogs.
- **Map features as text:** every map feature is also in "Features in view (keyboard list)" (§4.4).
  The map's status bar, legend and filters give the same information in text.
- **Tables behind charts:** every chart has a "Table" view.
- **Reduced motion:** map movement follows your system's reduced-motion setting.
- **Help menu:** "Help and accessibility" in the top bar repeats these points.
- Results of workflow, data-quality and operational-record changes, and map status updates, are
  announced to screen readers.

Known gaps: a screen-reader pass has not been done. What was tested, and what is still open, is in
[accessibility.md](accessibility.md).

## 12. What the system will not do

CRIMEMAP does **not**:
- give individual risk scores;
- predict suspects, offenders or any person;
- use facial recognition;
- dispatch, deploy or allocate officers automatically;
- recommend or take enforcement without human review;
- infer that anyone is guilty;
- publish prediction maps;
- track the location of officers or citizens;
- create or change FIRs or case records.

## 13. What each role can do

| Role | Demo user and territory | Can do | Cannot do |
|---|---|---|---|
| Crime analyst (`CRIME_ANALYST`) | `demo.analyst`, regions PDY and KKL | Home Map with points and details; Analytics including Comparison and AI Analysis; evidence, notes, flags, operational records; create and submit pattern reviews; propose actions; outcome reviews; validate or dismiss AI insights; template briefings; CSV and PDF exports | Decide pattern reviews; approve or complete actions |
| Station officer (`STATION_OFFICER`) | `demo.officer`, station PDY-S01 | Home Map and record summaries for their station (times to the hour, locations snapped to 250 m); PY Dashboard; operational records, notes and flags; record completion of actions assigned to them | Analytics; exports; pattern reviews; approvals; sensitive categories |
| SHO (`SHO`) | `demo.sho`, station PDY-S01 | Station-level map and details; Analytics; pattern reviews (create and decide); propose, approve and complete actions; outcome reviews; review operational records; read AI insights; exports | Region-wide evidence; reviewing AI insights; deciding or approving their own items |
| District or region supervisor (`DISTRICT_OR_REGION_SUPERVISOR`) | `demo.supervisor`, region KKL | Region-level map, details and Analytics; pattern reviews (create and decide); propose and approve actions; outcome reviews; review operational records; read AI insights; exports | Record completion; reviewing AI insights; deciding or approving their own items |
| Data steward (`DATA_STEWARD`) | `demo.steward`, region PDY | Home Map in their region; data-quality and duplicate queues; flags, notes and operational records; Analytics "Data Quality" section; data-quality AI insights; decide location corrections; Data Health; boundary approval (steward slot) | Other Analytics sections; pattern reviews; actions; exports |
| Senior leadership (`SENIOR_LEADERSHIP`) | `demo.leadership`, all regions, aggregates only | Aggregate Home Map, PY Dashboard and Analytics | Record points or details; review work; exports |
| Auditor (`AUDITOR`) | `demo.auditor`, all regions | Read AI insights and the model registry; Data Health; read the audit log and verify its chain (no screen yet; see the admin guide) | Case data on the map or in Analytics; any change |
| System administrator (`SYSTEM_ADMIN`) | `demo.admin`, global | Role grants, AI switches, boundary approval (admin slot), audit metadata, Data Health, model registry | Case data; changing their own roles |
| Demo viewer (`DEMO_USER`) | `demo.viewer`, all regions, aggregates only; grants do not depend on the one-time code | Aggregate Home Map, PY Dashboard and Analytics | Everything else |

The full permission matrix is in [data-classification-and-access.md](data-classification-and-access.md).
