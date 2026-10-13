# Information Architecture and Wireframes

> Low-fidelity wireframes (text). They fix layout, content, and interaction order, not visual styling.
> Visual design is produced in Phase 3 from the design tokens in section 9. Optimised for desktop
> operations at 1440 px and 1920 px, then tablet (≥ 768 px). Mobile (< 768 px) supports only limited
> station-officer workflows: view assigned actions, record action completion, add an operational
> observation. Analytics, exports, and the full map workspace are not offered on mobile.

## 1. Site map

```
/login                       OIDC sign-in (via IdP)
/                            Home Map
/dashboard                   PY Dashboard
/dashboard/trends            Trends (also openable as a dialog from the dashboard)
/analytics/overview
/analytics/location
/analytics/cases
/analytics/civic             (feature flag; hidden when disabled)
/analytics/ai
/analytics/comparison
/analytics/data-quality
/review                      Review Queue (tabs per queue)
/review/[type]/[id]          Review item detail + audit history
/reports                     Report requests, approvals, downloads
/admin/*                     SYSTEM_ADMIN only: users, roles, boundaries, adapters, flags, model registry
/audit                       AUDITOR / SYSTEM_ADMIN: audit trail, lineage
/help                        Help, accessibility statement, method notes
```

Navigation items a user's role cannot use are **not rendered**; the backend still denies direct URL
access (the UI hides links but never decides access).

## 2. Global app shell

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ DEMO / SYNTHETIC DATA · RESTRICTED — Internal Police Use Only        Data refreshed 10:42 IST  │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ CRIMEMAP  Home Map  PY Dashboard  Analytics  Review Queue (12)  Reports  [Search…] [!] [?] [Me]│
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- The first row is the safeguard strip: always visible, not dismissible; the environment badge
  (DEMO / SANDBOX / PRODUCTION) and classification badge use text plus colour (never colour alone).
- `[!]` notifications; `[Me]` user menu; `?` menu: accessibility options, keyboard shortcuts, help,
  "About the data".
- User menu: name, role, scope summary ("Scope: Karaikal region"), sign out.
- The wordmark is **text only**. No official seals, emblems, or logos are used unless supplied with
  written authorisation.
- All UI strings go through the i18n layer (English first; Tamil-ready message catalogues, locale-aware
  number and date formatting, layouts tested with longer Tamil strings).

## 3. Home Map

```
┌ Shell ─────────────────────────────────────────────────────────────────────────────────────────┐
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [ Search FIR/source ref, case ref, station, beat, locality, landmark, road…            ]       │
│ [+ Add Operational Record]  [PY Dashboard]  [Analytics]                                        │
│ Region▾ Station/Beat▾ Category▾ Record type▾ Period▾ Time basis▾ Case status▾                  │
│ Location confidence▾                                                       [Apply] [Reset]     │
├──────────────┬──────────────────────────────────────────────────────────────────┬──────────────┤
│ LAYERS       │                                                                  │ LEGEND       │
│ ☑ Cases      │                          MAP (MapLibre)                          │ ● Case tag   │
│ ☑ Station bd │     region switcher [Puducherry▾]          locator inset ┌───┐   │ ⬡ Grid cell  │
│ ☑ Region bd  │                                                          │4 r│   │ ◆ Pattern    │
│ ☐ Beats      │                                                          └───┘   │ ⚠ DQ warning │
│ ☐ Actions    │   aggregation: ( ) points (•) hex grid ( ) beat                  │ ▲ Approved   │
│ ☐ Civic(off) │                                                                  │   action     │
├──────────────┴──────────────────────────────────────────────────────────────────┴──────────────┤
│ 1,284 selected · 1,201 usable locations · 83 excluded (why?) · Synthetic bnd · 10:42 IST       │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Click a tag or cell → right-side drawer (map stays visible):

```
┌ Record summary ───────────────────────────────── ✕ ┐
│ DEMO-PDY-2026-000123                               │
│ Property / theft · Sec. (demo)                     │
│ Occurred 03 Oct 2026, 21:00–22:00                  │
│ Reported 04 Oct 2026, 09:12                        │
│ Locality: (fictional locality)                     │
│ Location confidence: Approximate                   │
│ Station: Puducherry North (Demo)                   │
│ Status: Under investigation                        │
│ [Open permitted details]                           │
│ [Open source record]  (stub: no source connected)  │
│ [Flag data issue]  [Add analyst note]              │
│ [Back to map]                                      │
└────────────────────────────────────────────────────┘
```

For a hex cell the drawer shows count, category breakdown, period, suppression note if applicable, and
"Create pattern review" (analyst roles only).

**+ Add Operational Record** dialog: type selector (six types, **no FIR option**) → title, date/time,
location (click map or enter locality), precision/confidence, reason, evidence/source reference,
visibility classification → "Submit for review" or "Save" (depending on type). Confirmation states
that an audit event was recorded.

## 4. PY Dashboard

```
┌ Shell ─────────────────────────────────────────────────────────────────────────────────────────┐
│ PY Dashboard                                                              [← Back to Home]     │
├─────────────────────────────┬──────────────────────────────────────────────────────────────────┤
│ [← Back]                    │ Category▾  Period▾                                    [TRENDS]   │
│ Region: [All Regions ▾]     │                                                                  │
│ [ Search station…      ]    │                                                                  │
│ ┌─────────────────────────┐ │                        MAP                                       │
│ │ Puducherry North (Demo) │ │   station + region boundaries; selected station emphasised;      │
│ │           [View Details]│ │   tags matching filters; others dimmed                           │
│ │ ▶Puducherry South (Demo)│ │                                                                  │
│ │           [View Details]│ │                                                     legend       │
│ │ …                       │ │                                                                  │
│ └─────────────────────────┘ │                                                                  │
└─────────────────────────────┴──────────────────────────────────────────────────────────────────┘
```

- The specification asks for both "a sidebar with back button" and a header `← Back to Home`; both are
  provided and both return to the Home Map.
- Selecting a region filters the station list, map, and Trends consistently.
- Clicking a station name: map fits to its jurisdiction, boundary emphasised, related tags highlighted,
  selection kept in sidebar (and in the URL, so it can be shared internally and restored).
- **View Details** drawer: station name, region, jurisdiction (synthetic), selected-case counts,
  active / detected / closed where permitted, category counts; buttons **View Station on Map** and
  **Back**.

**Trends** dialog / page:

```
┌ Trends ───────────────────────────────────────────────────────────────────────────────────── ✕ ┐
│ [Top Crime Spot] [Police Station Crime Rate] [Region] [Location] [Time] [Month] [Week]         │
│ Period: Jul–Sep 2026 · Category: All · Time basis: occurrence · Scope: Puducherry region       │
│ Measure: record count · Data quality: 94% usable locations · Refreshed 10:42 IST               │
│ ┌──────┬───────────────────────────────┬────────┬────────┬───────────────┐                     │
│ │ Rank │ Area / station / time bucket  │ Count  │ Change │               │                     │
│ │ 1    │ Grid cell PDY-H-0412          │  58    │ ▲ 12%  │ [View on Map] │                     │
│ │ …    │                               │        │        │               │                     │
│ └──────┴───────────────────────────────┴────────┴────────┴───────────────┘                     │
│ Police Station Crime Rate tab: "Rate unavailable — no approved denominator. Showing counts."   │
│ Export and logging: viewing is audited · export requires permission and re-authentication      │
│ About this analysis ▸                                                                          │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Neutral wording is mandatory: no "dangerous", "worst", "criminal", "high-crime area"; use "higher
recorded count in the selected period".

## 5. Analytics Workbench

```
┌ Shell ─────────────────────────────────────────────────────────────────────────────────────────┐
│ Region▾ Station▾ Category▾ Area/Beat/Grid▾ Period▾ Time range▾ Record type▾ Time basis▾        │
│ Case status▾ Location conf.▾ Comparison period▾                          [Apply] [Reset]       │
├───────────────┬────────────────────────────────────────────────────────────────────────────────┤
│ Overview      │ Location Trends — Hex grid counts                                              │
│ Location      │ Plain-language explanation of what this shows and how it was computed.         │
│ Case Trends   │ Filters: … · 1,284 records · 83 excluded (missing location) · 10:42            │
│ Civic (off)   │ Data quality: 94% usable locations; 6% approximate.                            │
│ AI Analysis   │ [Map] [Chart] [Table] [Explanation]       [Add Note] [Open Evidence]           │
│ Comparison    │                                [Create Pattern Review] [Export Report]         │
│ Data Quality  │ ┌──────────────────────────────────────────────────────────────────────────┐   │
│               │ │                               view content                               │   │
│               │ └──────────────────────────────────────────────────────────────────────────┘   │
│               │ About this analysis ▸ (method, spatial unit, grid size, limitations)           │
└───────────────┴────────────────────────────────────────────────────────────────────────────────┘
```

Section contents follow the specification exactly (Overview KPIs and charts; Location: point, hex,
choropleth, KDE behind thresholds, corridor, repeat-location, persistence; Case Trends: series,
day×hour heatmap, calendar heatmap, delay distribution, status ageing; Civic behind flag; AI Analysis:
insight cards; Comparison: A vs B with side-by-side maps, dual lines, difference table; Data Quality).
Overview KPI cards: selected records, change vs comparison period, usable-location percentage, open
review items. Civic Trends, when enabled, uses only the neutral phrase "contextual overlap for joint
inspection review" and never implies that infrastructure conditions cause crime.
Every view uses the same header block so the "filters, count, excluded, freshness, method, caveat"
rule is enforced by one component.

## 6. Review Queue

```
[Data quality (7)] [Duplicates (3)] [AI insights (2)] [Pattern reviews (1)] [Actions (4)] [Outcomes due (1)]
Filters: Owner▾ Priority▾ Status▾ Region▾ Due▾
┌──────┬──────────┬─────────┬──────────┬───────────┬────────┬────────┬────────┬─────────┐
│ Type │ Priority │ Date    │ Area     │ Category  │ Owner  │ Due    │ Status │ Evidence│
└──────┴──────────┴─────────┴──────────┴───────────┴────────┴────────┴────────┴─────────┘
```

Item detail: summary, evidence, state-transition buttons permitted for the user, and full audit
history (who, what, when, rationale).

## 7. Reports

Request form (type, filters, purpose) → re-authentication → approval if required → generation in
worker → short-lived download link. Every export lists requester, time, filters, classification,
source and refresh time, data-quality note, method note, watermark, and expiry.

## 8. AI insight card

```
┌ AI-assisted; analyst review required ──────────────────────────────┐
│ Emerging place-time pattern · Rule: emerging_v1.0                  │
│ Area: Grid PDY-H-0412 · 18:00–22:00 · Property / theft             │
│ Observed 14 vs baseline 5.2 (±2.1) over last 4 weeks               │
│ Data quality 0.92 · 3 records excluded (missing time)              │
│ Confidence: moderate (control-limit exceedance, 2 consecutive wks) │
│ Calibration: rule-based threshold; not a calibrated probability    │
│ Why: …  Limitations / alternative explanations: …                  │
│ Snapshot: EVS-2026-10-11-0042                                      │
│ [Open Evidence] [Validate] [Dismiss] [Add Note]                    │
└────────────────────────────────────────────────────────────────────┘
```

Insights refer to a **place and time window only**, never to a person.

## 9. Design tokens (initial)

| Token group | Values (initial; refined in Phase 3) |
|---|---|
| Colour — foundation | navy `#0F2340`, ink `#1B2433`, surface `#F7F7F5`, surface-2 `#EDEEEB` |
| Colour — borders | divider `#D5D8DC` (decorative separators only, 1.33:1); control border `#7A828B` (inputs, buttons, checkboxes: 3.63:1 on surface, 3.34:1 on surface-2 — meets non-text 3:1) |
| Colour — information | teal `#2D7D7A` for focus rings and graphics (≥ 3:1 on all surfaces incl. navy); teal-text `#246A67` for text and links (5.87:1 on surface, 5.41:1 on surface-2) |
| Colour — attention | saffron `#C9822B` as a small fill **only with ink text** (4.98:1) and a saffron-strong outline; saffron-strong `#8F5A16` for text, icons, and outlines (5.38:1 on surface, 4.95:1 on surface-2). Saffron `#C9822B` is never used as text or as the only boundary of a control (2.92:1) |
| Colour — severity | low/medium/high with icon + text label, contrast ≥ 4.5:1 |
| Restricted state | hatched pattern + "Restricted" label (non-colour cue) |
| Spacing | 8-point scale: 4, 8, 16, 24, 32, 48, 64 |
| Radius | 2 px controls, 4 px panels (restrained) |
| Elevation | border-first; one shadow level for drawers and dialogs |
| Typography | Noto Sans for Latin text with Noto Sans Tamil as the Tamil fallback (Noto Sans itself has no Tamil glyphs); self-hosted font files, no third-party font CDN; sizes 12/14/16/20/24; tabular figures for counts |
| Chart palette | colour-blind-safe categorical set, single-hue sequential for density |
| Map symbols | distinct shapes per layer (circle, hexagon, diamond, triangle) + warning glyph |
| Focus | 2 px teal outline + 2 px offset, always visible |
| Motion | ≤ 200 ms; disabled under `prefers-reduced-motion` |

## 10. Accessibility commitments

WCAG 2.2 AA: keyboard operation of all controls including map layer toggles and record selection
(a list view mirrors map features for keyboard and screen-reader users); visible focus; semantic
landmarks; labelled controls; charts have table equivalents; non-colour cues; target size ≥ 24×24 px;
no time limits except security session timeout (with warning). Contrast ratios in section 9 were
computed with the WCAG 2.x relative-luminance formula on 2026-10-11; they are re-checked automatically
in Phase 3.
