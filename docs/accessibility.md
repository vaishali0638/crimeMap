# CRIMEMAP Accessibility

**Environment:** DEMO, synthetic data only. Target: WCAG 2.2 AA (specification, UX principles),
designed for desktop operations at 1440 and 1920 px first, then tablet; phones support limited
station-officer use. This page records how the interface meets the target and what was tested in
Phase 8 (2026-10-13). Automated tools find only part of WCAG; the manual checks still open are
listed in §5.

## 1. Keyboard

| Feature | Implementation | Tested |
|---|---|---|
| Skip link | "Skip to main content" is the first focus stop and moves focus to `#main` | Playwright: first Tab focuses it, Enter moves to `#main` |
| Visible focus | Global `:focus-visible` outline, 2 px teal, offset 2 px (`app/globals.css`) | axe (no focus-order or focus-visible rule failures); not measured for contrast |
| Map without a mouse | Every map feature is also listed in "Features in view (keyboard list)"; choosing one opens the same summary as clicking the map | Playwright: keyboard only, Enter on the first feature opens its "Grid cell" summary |
| Dialogs | Radix dialogs trap focus and return it on close (station details, evidence, pattern review) | Exercised in the E2E scenarios; focus return not asserted |
| Irreversible decisions | Two-step confirmation ("Yes, Approve"), keyboard operable | E2E scenarios 4–8 |
| After a change | Flash message with `role="status"`, focused, keyed so a repeated change is announced again | Phase 7 browser check; E2E asserts the status text |

## 2. Contrast and non-colour cues

- Colour tokens meet AA contrast in the axe colour-contrast rule on all audited screens (§4).
- Map cells use a lightness ramp plus a legend with counts; data-quality warnings use an outline,
  not colour alone; selected states use `aria-pressed` / `aria-current`, not colour alone.
- The map has no basemap by default (ADR-033), so boundaries are drawn on a plain background.

## 3. Screen readers and semantics

- Landmarks: banner, main navigation, search, main, complementary panels, status bar. The
  classification strip is a labelled region read first.
- The map canvas is a labelled region whose label points to the text alternatives: the filters,
  the feature list, the legend (with counts) and the status bar (selected, usable, excluded).
- Analytics Workbench charts (ECharts) have the ECharts accessibility description enabled with a
  title, and each analysis has a "Table" tab with the same numbers and an "Explanation" tab.
- Tables have captions and header cells; form fields have visible labels; validation messages never
  echo the submitted value.
- In the workflow forms, the hint that explains why a button is disabled (for example "Add your
  assessment (at least 10 characters) to submit.") is linked to the button with `aria-describedby`
  (fixed in Phase 8; E2E scenario 4 asserts the button's accessible description).
- Language: `lang="en"`. All interface text goes through the message catalogue.

## 4. Automated audit (Phase 8)

`apps/web/e2e/accessibility.spec.ts`: axe-core 4.13 via `@axe-core/playwright`, rules tagged WCAG 2.0,
2.1 and 2.2 A/AA, on the live stack, signed in as the user who sees each screen, at 1440 × 900.

| Screen | User | Violations |
|---|---|---|
| Home Map | demo.analyst | 0 (1 found and fixed, below) |
| PY Dashboard | demo.supervisor | 0 |
| Analytics Workbench (overview) | demo.analyst | 0 |
| Analytics: Location Trends | demo.analyst | 0 |
| Review Queue | demo.analyst | 0 |
| Review Queue: data quality | demo.steward | 0 |
| Review Queue: AI insights | demo.steward | 0 |
| Data Health | demo.steward | 0 |
| Reports | demo.analyst | 0 |
| AI models | demo.analyst | 0 |
| Signed out | — | 0 |

Reflow (WCAG 1.4.10): at 320 CSS px (1280 px at 400 % zoom) the Home Map, PY Dashboard, Analytics,
Review Queue and Data Health have no horizontal scrolling. The map canvas is two-dimensional content
(an allowed exception); it no longer widens the page.

Found and fixed in Phase 8:

| Finding | WCAG | Fix |
|---|---|---|
| Home Map search box referenced its results list in `aria-controls` while the list was not rendered (axe `aria-valid-attr-value`, critical) | 4.1.2 | Reference only while the list exists (`components/map/SearchBox.tsx`) |
| Home Map overflowed by 206 px at 320 px: header toolbar did not wrap; layer panel and map side by side | 1.4.10 | Header toolbar wraps; below the `md` breakpoint the layer panel stacks above the map |
| PY Dashboard and Analytics overflowed by about 240 px at 320 px (fixed-width side panels) | 1.4.10 | Station list, map and legend stack below `md`; the Analytics section menu stacks above the workbench |
| Workflow form hints explaining a disabled button were not linked to it (found in code review) | 1.3.1, 4.1.2 | Each hint has an id referenced by `aria-describedby` (`components/workflow/Forms.tsx`) |

ESLint's `jsx-a11y` rules run on every change (`pnpm --filter @crimemap/web lint`).

## 5. Manual checks

| Check | Status |
|---|---|
| Screen reader pass (NVDA on Windows, VoiceOver on macOS) of the three required screens and the review workflow | **Not done** in Phase 8 |
| 200 % text resize without loss of content (1.4.4) | Not done (reflow at 400 % zoom passes, §4) |
| Target size at least 24 × 24 CSS px (2.5.8) | Controls use a 44 px minimum height (`min-h-11`); not measured on every control |
| Focus not obscured by sticky content (2.4.11) | Not checked |
| Reduced motion | Implemented (CSS media query; charts turn animation off); not tested with the setting on |
| Contrast of the focus outline against every background (1.4.11) | Not measured |
| Map label legibility at all zoom levels | Not checked |

A manual audit by an accessibility specialist, with screen-reader users if possible, is a
condition for production ([phase-8-report.md](phase-8-report.md)).

## 6. How to run

With the stack and the web app running (`pnpm start` on :3100) and the demo password in the
environment (from `.env`):

```bash
pnpm --filter @crimemap/web e2e e2e/accessibility.spec.ts
```

Per-screen results are written to `apps/web/test-results/axe/*.json`.
