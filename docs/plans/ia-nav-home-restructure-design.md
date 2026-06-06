# Design — IA restructure: grouped nav + Approvals surfacing + home hierarchy

Status: **founder-gated 2026-06-06** (N1+N2+N3 + H1; IA skeleton FIRST, theme styling after).
Role chain: architect (this) → frontend (agy) → review → live smoke → land.
**Dispatch AFTER the Open Questions FE lands (overlaps App.jsx). Theme v1 styling layers on top of
this skeleton afterwards.**

## Problem
- The project-detail sidebar is a **flat 13-tab list** with no priority/grouping:
  `Overview · Dev Loop · Workflow Graph · Roles · Artifacts · Actions · Agile Board · Smoke Runs ·
  Workers · Memory · Messages · Approvals(12th) · System`. The most action-critical tab
  (**Approvals** — where the human must decide gates) is buried near the bottom.
- The **home** stacks the Projects list and the always-expanded "Start Workflow Console" form at
  equal weight → no hierarchy ("which project do I work on" competes with a big create form).

## Nav redesign (N1 group + N2 surface Approvals + N3 merge)

### Grouped sidebar (4 labeled sections; Approvals at the top of WORK)
- **WORK** (act): Overview · **Approvals** (with a pending badge) · Artifacts · Actions · Agile Board
- **OBSERVE** (status): Workflow Graph · Dev Loop · Messages
- **INSPECT** (data): **Agents & Memory** (merged) · Smoke Runs
- **SYSTEM**: System

### N3 merges (13 → ~10, function preserved via sub-tabs)
- **Agents & Memory** = merge **Roles + Memory + Workers** into ONE tab with internal sub-tabs
  (Roles / Memory / Jobs). They are all "the agents and what they know/did". (3 → 1.)
- **Smoke Runs** stays its own item under INSPECT (or fold into System later — keep for now).
- Net: 13 flat → ~11 items but in 4 priority-ordered groups, Approvals promoted to #2 overall.
  Further merges (e.g. Actions→Overview) are tunable later; don't over-merge in v1.

### N2 — surface Approvals so it's never missed (FE-only, derive from existing state)
- A **count badge** on the Approvals nav item = number of actionable gate reviews (derive from the
  existing `actionableGateReview` / `approval_reviews` already in state — no BE change).
- A **persistent top banner** when a gate is actionable: "1 gate awaiting your approval → [Review]"
  that jumps to the Approvals tab, visible from any tab.

## Home redesign (H1 — Projects primary, create as an action)
- **Projects list/grid = the home body.** Each project card shows status (current gate / progress /
  active-run indicator) so the home answers "which project do I work on".
- **"+ New Project" = a prominent action button** (top-right of the Projects header, and a "+" tile
  in the grid). Clicking opens the create flow in a **modal (or a dedicated panel)** — NOT an
  always-expanded form competing with the list. Move the existing Start Workflow Console markup into
  that modal/panel (Adopt + New Project + delivery mode + picker all live there).
- **Empty state** (no projects): show the create CTA prominently (since there's nothing to list).
- Adopt-existing and New-Project both live inside the same create flow (the current two-mode toggle).

## Scope / constraints
- **IA skeleton only** — structure + grouping + Approvals surfacing + home hierarchy. Keep using the
  current visual styling (the theme v1 work will restyle ON TOP of this skeleton afterwards). Don't
  redo colors/typography here.
- FE-only: `frontend/src/App.jsx`. No backend (the approvals badge derives from existing state). No
  new deps (modal can be a simple conditional overlay like the existing directory picker).
- Preserve all existing functionality — every current tab's content must remain reachable (merged
  ones via sub-tabs). Don't drop features; just regroup/relocate.

## Acceptance / smoke
- `npm run lint` + `npm run build` green.
- Live: sidebar shows the 4 groups with Approvals near the top + a badge when a gate is actionable;
  the top banner appears for an actionable gate and jumps to Approvals; Roles/Memory/Workers are
  reachable under "Agents & Memory" sub-tabs; the home shows Projects as the primary list with a
  "+ New Project" button that opens the create modal (and the empty state shows the CTA).

## Out of scope (v1)
Theme/visual restyle (separate, after); further tab merges beyond Agents&Memory; mobile-specific nav.
