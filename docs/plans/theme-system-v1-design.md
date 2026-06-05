# Theme System v1 — design (two switchable themes, light-first prototype)

Status: **founder-gated 2026-06-05**, ready to dispatch the prototype.
Owner role chain: architect (this doc) → frontend (gemini) → code_review (Claude) → QA live smoke → founder pick.

## Why
The current UI mimics Duolingo's *color + roundness* but not its *typographic clarity*.
Read-only audit of `frontend/src/App.jsx`:
- **216 uses of sub-11px text** (`text-[10px]`×134, `text-[9px]`×66, `text-[8px]`×16)
- **256 uses of bold/extrabold** (`font-extrabold`×119, `font-bold`×137) — hierarchy is faked
  by weight-everywhere instead of size+color, so nothing stands out → reads as crowded/blurry.
- **837 hardcoded hex** (37 distinct), inline as Tailwind arbitrary values
  (`border-[#E5E5E5]`×156, `text-[#AFAFAF]`×143, …). No semantic token layer →
  a theme switch today would mean rewriting the whole file.

## Founder decisions (locked)
1. **Switcher = productized, permanent feature** (a commercialization selling point) → invest
   in a clean semantic token layer; switcher persists across sessions.
2. **Two directions:** Theme A = **Vivid** (refined Duolingo / Wise — keep playful/rounded/energetic
   but fix type hierarchy + enhance interactions); Theme B = **Minimal** (Claude-web — restrained,
   generous whitespace, neutral palette, single accent, subtle borders).
3. **Prototype on representative slices first** (de-risk) — NOT the full 837-color conversion.
   Pick the winner visually, then full-convert in the winning direction.
4. **Light mode only for now.** Dark comes later for the winner. (Dark-mode bug fix = PR #82.)

## Architecture — semantic token layer (the foundation)

Add to `frontend/src/index.css` (additive; does NOT touch existing arbitrary-value classes, so
un-prototyped screens keep working unchanged):

### 1. Theme-agnostic token vocabulary (CSS variables)
Defined per theme via a root attribute `[data-theme="vivid"]` / `[data-theme="minimal"]`:
```
--surface-base      page background
--surface-raised    card / panel
--surface-sunken    input / inset / track
--surface-overlay   modal / popover
--border-subtle     hairline dividers
--border-strong     emphasized borders
--text-primary      body & headings (HIGH contrast — stop using #AFAFAF for primary)
--text-secondary    supporting copy
--text-muted        captions / metadata (the ONLY place a low-contrast gray is OK)
--text-inverse      text on accent fills
--accent            brand action color
--accent-text       readable accent for text-on-light
--accent-bg         soft accent tint (chips / hovers)
--accent-ring       focus ring
--success/-bg/-text, --warning/-bg/-text, --danger/-bg/-text, --info/-bg/-text
--shadow-card, --shadow-button   (elevation language differs per theme)
--radius-sm/md/lg/xl
```

### 2. Type scale (kills the clarity problem)
Replace ad-hoc 8/9/10px with a real scale (CSS vars, consumed via Tailwind `@theme inline`):
```
--text-2xs  11px / 1.4   ← absolute floor; replaces ALL 8/9/10px
--text-xs   12px / 1.45
--text-sm   13.5px / 1.5
--text-base 15px / 1.55   ← default body
--text-lg   18px / 1.5
--text-xl   22px / 1.3
--text-2xl  28px / 1.25
--text-3xl  34px / 1.2
```
Weight policy: only `normal(400) / medium(500) / semibold(600) / bold(700)`. Hierarchy comes from
size + color + weight together, never blanket extrabold.

### 3. Tailwind v4 mapping
Use `@theme inline { --color-surface-base: var(--surface-base); … }` so utilities like
`bg-surface-raised`, `text-primary`, `border-subtle`, `rounded-lg`, `shadow-card` resolve to the
active theme's variables. Switching theme = swap one `data-theme` attribute; everything cascades.
Composes cleanly with future dark mode (data-theme × prefers-color-scheme).

### 4. Switcher (productized)
A small control in the header (next to the existing dark toggle), 2-way segmented (Vivid | Minimal).
Persist to `localStorage('gt-theme')`, apply on mount by setting `document.documentElement.dataset.theme`
— mirror the existing darkMode effect (App.jsx:612).

## The two themes (concrete starting palettes — light)

### Theme A — "Vivid" (refined Duolingo / Wise)
- Font: keep **Nunito** (rounded, friendly — on-brand for Duo/Wise).
- accent `#58CC02`, accent-text `#3D8B00`, accent-bg `#E8F9D9`, accent-ring `#58CC02`.
- surface-base `#F7F7F7`, raised `#FFFFFF`, sunken `#F1F1F1`, overlay `#FFFFFF`.
- text-primary `#1A1A1A`, secondary `#5A5A5A`, muted `#8A8A8A` (NOT #AFAFAF for primary labels).
- border-subtle `#E5E5E5`, strong `#D1D1D1`.
- radius lg `18px`, xl `24px`; **chunky elevation** `--shadow-button: 0 4px 0 var(--border-strong)`,
  card `0 4px 0 #D1D1D1`.
- status: success green family, warning `#FF9600`, danger `#FF4B4B`, info `#1CB0F6`.
- **Interactions:** button press-physics (shadow compresses on :active), card hover-lift,
  input focus-ring grow, stat count-up, gate-approve success pop. Energetic, 200–250ms spring.

### Theme B — "Minimal" (Claude web)
- Font: clean sans (system-ui / Inter stack); headings may use slightly larger size, not heavier.
- surface-base `#FAF9F5` (warm paper), raised `#FFFFFF`, sunken `#F5F4EF`, overlay `#FFFFFF`.
- text-primary `#2B2A27`, secondary `#56544E`, muted `#8B8980`.
- border-subtle `#ECEAE3`, strong `#DAD7CE`.
- accent **single restrained** color `#C96442` (clay/terracotta, Claude-like); accent-text `#9D4B2F`,
  accent-bg `#F6EAE4`, accent-ring `#C96442`.
- radius md `8px`, lg `10px`; **soft elevation** `--shadow-card: 0 1px 2px rgba(0,0,0,.06), 0 1px 3px rgba(0,0,0,.04)`,
  buttons mostly flat with 1px borders.
- status: muted/desaturated families.
- **Interactions:** subtle only — 150ms ease fades, gentle hover bg shift, no chunky physics.

## Prototype scope (representative slices — both themes, behind the switcher)
Restyle ONLY these to consume the new tokens; leave the rest of the app on legacy styles.
1. **Home / Start screen** — project-cards grid + New Project / Adopt form (inputs, selects,
   delivery-mode dropdown + custom textarea, Browse picker, primary/secondary buttons).
2. **One project detail view** — the State card, a role/memory card, the Dev-Loop PR list with
   land-ready badges + copy button. (Exercises data display, badges, lists, status colors.)

These two cover ~all component archetypes, so the comparison is fair without converting 837 colors.

## Acceptance (prototype)
- `npm run lint` (0 warnings) + `npm run build` green.
- Switcher toggles `data-theme` between `vivid`/`minimal`, persists across reload (localStorage).
- On the two prototyped screens: no sub-11px text; primary text uses `--text-primary` (high contrast).
- Live Playwright smoke (light): switch to each theme, assert (a) `data-theme` set, (b) body/card
  background matches that theme's `--surface-base`, (c) a primary label's computed font-size ≥ 12px
  and color is high-contrast vs its surface.
- Lane integrity: only `frontend/src/App.jsx` + `frontend/src/index.css` changed.

## Out of scope (v1)
Full 837-color conversion; dark mode for the new themes; non-prototyped screens; backend.
These follow after the founder picks a winner.
