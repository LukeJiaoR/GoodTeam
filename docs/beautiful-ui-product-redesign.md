## User goal
Redesign the whole GoodTeam product toward https://www.beautifului.dev/, rather than retaining the existing visual style and making small adjustments. The previously merged shared material and execution inspector work are useful functionality, but do not satisfy this visual redesign.

## Reference observed
Inspected the live light/dark reference, task capsules, chat, prompt bar, and sidebar. Adopt a quiet neutral canvas, floating white/charcoal surfaces, hairline dividers, restrained shadows, rounded task/status capsules, and a compact persistent workspace navigation. Adapt patterns to GoodTeam's actual workflows and evidence boundaries; do not invent activity or claim unavailable capabilities.

## Whole-product scope
- [ ] Persistent workspace navigation and project context across every destination
- [ ] Work: task history rail, conversation/composer as the main stage, compact execution rows and delivery/evidence side panel
- [ ] Projects: project overview and status-led workspace collection with search/filter/import
- [ ] Team: capability summary, manager identity, role/worker records and configuration dialogs
- [ ] Knowledge: view selector, repository/artifact/memory records, readable provenance details
- [ ] Review: queue, readable artifact and contextual approval/action panel
- [ ] Settings: a real appearance/language destination and clear routing to team/runtime configuration
- [ ] Coherent light/dark, desktop/narrow viewport, keyboard focus, reduced motion and long-content behavior
- [ ] Browser acceptance of navigation continuity, interrupted/repeated actions, source inspector, unsaved review and configuration dialogs

## Delivery boundaries
First broad slice is the product-wide navigation/composition and visual language, with local interaction regression checks and browser evidence. Keep the issue open for any acceptance or deeper flow redesign that is not actually verified. No backend authority, identity, approval, persistence, deployment, branch protection or CI workflow changes. Memory handoff issue #534 remains separate.

## Validation
Tier 4, browser-visible routing/layout with read-only projection and preferences. Run existing frontend tests, lint/build, focused HTTP/asset contract checks, and isolated browser smoke. No added CI workflows; publish and validate final candidates through the existing exact-SHA process.

## Implemented broad candidate

This candidate replaces the top-navigation composition with a persistent project workspace sidebar and a quiet route breadcrumb. The same neutral design system now spans every destination rather than being limited to Work. Project/task identity, remembered deep links and read-only historical contexts remain in their existing owners.

- Projects gains real count-based overview filters and separated spacious workspace records. Counts use the same semantics as each filter; activity is never fabricated.
- Work keeps its task history on the left, makes the conversation/composer the primary surface, and renders execution and delivery in soft capsule-like records. The delivery panel can be collapsed and restored. Opening the current approval reveals its delivery context; collapsing changes no approval or execution state.
- Team uses an inline capability summary and consistent responsibility/worker records. All runtime and skill configuration controls retain their existing handlers and authority.
- Knowledge uses a compact segmented view selector and the same source/catalog/document language. Project-scoped query parameters and provenance inspection remain intact.
- Review becomes a quiet queue plus readable document and contextual decision inspector; the unsaved-edit navigation guard is unchanged.
- Settings is now an actual destination. Theme and language are shared with the shell immediately and remain browser-local preferences. The project settings page links to Team for runtime configuration; legacy settings subsection deep links retain their existing Team redirect.
- Compact sidebar links keep explicit accessible names. Phone navigation is arranged independently from desktop, without requiring a horizontal four-pane workspace.

## Candidate verification and remaining acceptance

Verification tier: Tier 4 (frontend presentation, preferences, HTTP route fallback and live browser layout; no backend mutation contract changed).

- Full frontend unit suite: 45 files / 590 tests passed after the settings, delivery-panel and breadcrumb route-segment additions. All five navigation tests also passed after correcting Projects active-route matching.
- Existing Node helpers: 28 tests passed.
- New targeted settings/deep-link and collapsible-delivery regressions: 2 files / 68 tests passed, including the existing WorkPage tests in that selection.
- ESLint and production build passed; existing bundle-size/dynamic-import warnings remain.
- Focused backend import/registry/static-asset checks: 73 tests passed.
- Independent source review prompted corrections to the 768–820px Review queue breakpoint, Projects active-route matching, routed CSS import order and small muted-text contrast. Follow-up review is required against the final frozen candidate.
- Built-CSS/DOM cascade checks at 320, 600, 760, 768, 800, 820, 821, 1024 and 1440px verify the Review breakpoint, shared Work dark-material tokens, pill buttons and phone project-action placement. These are source-only checks, not pixel rendering. Muted light text calculates to at least 4.60:1 on the three light surfaces.
- Actual rendered GoodTeam acceptance ran on 2026-10-12 through a real Chromium browser and the production build served by GoodTeam HTTP. The workspace, home, registry and Operator state were isolated; all task execution providers were fake. Projects, Work, Team, Knowledge, Review, Settings, dark/light, phone layouts, unsaved-review protection, draft persistence, configuration dialogs, source inspection and interrupted-message retry were inspected. That pass found two blockers: project Settings refresh returned 404, and expanding Execution clipped Send. It required a local import-extension workaround for a macOS build failure inherited from main, so that earlier evidence is not exact-HEAD build validation.

## Browser acceptance repairs (2026-10-13)

- The HTTP SPA fallback now accepts `/projects/:projectKey/settings` as well as the legacy single-section route. Browser regression coverage opens the global/project destinations directly, reloads both with HTTP 200, and verifies the old settings subsection still redirects to Team.
- The active Task Assistant reserves the composer's intrinsic minimum, keeps the scrolling transcript out of intrinsic sizing, and scrolls expanded auxiliary guidance above the input/action row. Expanding Execution no longer hides Send. On short screens the page can scroll rather than clipping the main action.
- Explicit `.jsx`/`.js` imports disambiguate `TaskInputContext` and `currentWorkContext` component/helper pairs on case-insensitive macOS, including their existing tests. The normal production build now works without a local workaround.
- Added browser checks cover expanded/collapsed Execution and guidance at 1440×1000, 1280×720 and 1024×768, including pointer hit testing and zero composer-dock scroll. The existing 390px mobile navigation/focus and draft checks still pass. Real Chromium spot checks also passed dark mode at 320/390/1024/1280/1440px and Chinese at 390px; page scrolling was used on short/phone screens, with no composer-dock scrolling or horizontal overflow. A real Send through the isolated fake Assistant recorded one message, cleared input, preserved the run/gate and dispatched no Worker jobs.

Verification commands for these repairs (run from the repository root unless noted):

```sh
npm run build --prefix frontend
npm run lint --prefix frontend
NODE_OPTIONS=--no-experimental-webstorage npm run test:unit --prefix frontend -- --maxWorkers=2
npm run test:node --prefix frontend
PYTHONPATH=src python -m pytest tests/test_web.py::test_product_spa_path_contract tests/test_frontend_assets.py -q
ruff check src/goodteam_os/ tests/
# From frontend/, with GOODTEAM_E2E_PYTHON pointing to the local Python environment:
npm run test:e2e -- project-load-work.spec.js workspace-continuity.spec.js
git diff --check
```

Results: production build and lint passed, 45 unit files / 590 tests passed, 28 Node tests passed, 8 focused backend tests passed, Ruff passed, and 7 real HTTP/Playwright tests passed without retries. Existing bundle-size/dynamic-import warnings remain. `NODE_OPTIONS` disables Node 25's native WebStorage so Vitest uses its browser test storage.

Full backend and full E2E suites were not rerun for these bounded routing/layout/import repairs: approval authority, state mutation, execution identity and Worker dispatch contracts were unchanged. The complete approval loop, real model/adapter behavior, reduced-motion preferences and a full accessibility audit remain outside this acceptance evidence. Local checks do not replace exact-SHA candidate CI.

Keep the tracking issue open for remaining UX acceptance beyond the inspected paths. The PR remains Draft until its final head and current main base pass the prescribed exact-SHA candidate validation. No deployment, remote CI changes or external preview publication is part of these repairs.
