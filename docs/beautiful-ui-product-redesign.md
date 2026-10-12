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
Tier 2, read-only projection/navigation and presentation contract. Run existing frontend tests, lint/build, focused API read-contract checks, and isolated browser smoke. No added CI workflows; publish and validate final candidates through the existing exact-SHA process only when authorized.

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

Verification tier: Tier 2 (frontend presentation, preferences and navigation; no backend mutation contract changed).

- Full frontend unit suite: 45 files / 590 tests passed after the settings, delivery-panel and breadcrumb route-segment additions. All five navigation tests also passed after correcting Projects active-route matching.
- Existing Node helpers: 28 tests passed.
- New targeted settings/deep-link and collapsible-delivery regressions: 2 files / 68 tests passed, including the existing WorkPage tests in that selection.
- ESLint and production build passed; existing bundle-size/dynamic-import warnings remain.
- Focused backend import/registry/static-asset checks: 73 tests passed.
- Independent source review prompted corrections to the 768–820px Review queue breakpoint, Projects active-route matching, routed CSS import order and small muted-text contrast. Follow-up review is required against the final frozen candidate.
- Built-CSS/DOM cascade checks at 320, 600, 760, 768, 800, 820, 821, 1024 and 1440px verify the Review breakpoint, shared Work dark-material tokens, pill buttons and phone project-action placement. These are source-only checks, not pixel rendering. Muted light text calculates to at least 4.60:1 on the three light surfaces.
- Actual rendered GoodTeam browser acceptance remains unverified: the dot cloud browser could inspect the public reference, but could not connect to the isolated exec loopback preview. No screenshot or browser success is claimed. Do not equate passing DOM tests with visual acceptance.

The tracking issue remains open until the rendered light/dark and desktop/phone workflows are inspected. No deployment, remote CI changes or external preview publication is part of this candidate.
