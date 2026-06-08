# Lightweight I18n Design

## Context

GoodTeam's React dashboard is currently English-first with no i18n framework,
locale files, or language preference surface. The previous backlog review counted
260+ hardcoded frontend strings in `frontend/src/App.jsx`, so a full extraction in
one pass would be a large cross-cutting refactor.

The product decision is now set: GoodTeam should support an automatic bilingual
posture. Chinese users should get Chinese by default when their browser language is
`zh-*`; everyone else should get English. Users must also be able to pin the
language manually.

## Goal

Add a lightweight frontend-only i18n foundation for `en` and `zh-CN`, defaulting
from browser language in `auto` mode, with a visible language switcher and a
scoped first extraction of high-traffic dashboard chrome.

## Non-Goals

- Do not translate every string in `App.jsx` in the first implementation.
- Do not move frontend copy into Python APIs or project configuration.
- Do not add a heavy translation framework unless the lightweight layer proves
  insufficient.
- Do not translate user-provided data, project names, artifact bodies, worker
  output, memory events, or backend error payloads in this pass.

## Language Behavior

GoodTeam supports three user preferences:

- `auto`
- `en`
- `zh-CN`

The preference is stored in `localStorage` under `gt-language`.

Resolution rules:

1. If `gt-language` is `en`, resolve to English.
2. If `gt-language` is `zh-CN`, resolve to Simplified Chinese.
3. If `gt-language` is missing, invalid, or `auto`, inspect
   `navigator.languages` first, then `navigator.language`.
4. If the first available browser language starts with `zh`, resolve to `zh-CN`.
5. Otherwise resolve to `en`.

English is the fallback for unsupported locale keys, invalid preferences, and
unsupported browser locales.

## Frontend Architecture

Create a small `frontend/src/i18n/` module:

- `locales/en.js`: English dictionary.
- `locales/zh-CN.js`: Simplified Chinese dictionary.
- `index.js`: exports supported language constants, language detection helpers,
  preference normalization, translation lookup, and interpolation.

The translation API should stay intentionally small:

- `normalizeLanguagePreference(value) -> 'auto' | 'en' | 'zh-CN'`
- `resolveLanguage(preference, browserLanguages) -> 'en' | 'zh-CN'`
- `translate(language, key, params) -> string`

`App.jsx` should own the React state for the selected language preference, similar
to the existing `gt-theme` and `color-scheme` localStorage patterns. It can derive
a local `language` and `t` function inside the component rather than introducing a
React context in the first slice.

## UI Surface

Add a compact segmented control beside the existing theme controls in the sidebar
footer and matching compact controls in the top-level project/header area where
theme controls already appear.

Labels:

- `Auto`
- `EN`
- `中文`

The control should:

- Use fixed or stable sizing so translated text does not shift layout.
- Persist immediately to `gt-language`.
- Expose accessible button labels such as "Use automatic language", "Use English",
  and "Use Chinese".
- Keep language switching local to the browser. It should not trigger a backend
  write or workflow action.

## First Extraction Slice

Extract high-traffic dashboard chrome first:

- Sidebar group titles: Work, Observe, Inspect, System.
- Sidebar tab labels: Overview, Approvals, Artifacts, Actions, Agile Board,
  Workflow Graph, Dev Loop, Messages, Agents & Memory, Smoke Runs, System.
- Project/dashboard chrome labels: Dashboard, Exit, Run ID.
- Header and banner copy owned by the frontend shell, including approval-needed
  banner copy and its Review button.
- Theme and language control accessibility labels.
- Common shell fallback labels such as no active run, loading, and generic retry
  labels where they appear near the app shell.

Leave deeper feature panels for later passes. This keeps the first implementation
low-conflict while proving the language-selection machinery on visible UI.

## Testing

Add focused frontend contract tests in `tests/test_web.py`:

- Locale files exist for `en` and `zh-CN`.
- `frontend/src/i18n/index.js` contains `gt-language`, `auto`, `en`, and `zh-CN`
  handling.
- Auto detection maps `zh-*` browser language values to `zh-CN`.
- Unsupported or invalid preferences fall back to English.
- `App.jsx` uses the i18n helper for sidebar group and tab labels rather than
  hardcoding those chrome labels inline.

Final verification for implementation:

- Focused `tests/test_web.py` i18n contract tests.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'i18n or frontend_contract'`.
- `cd frontend && npm run build`.
- `git diff --check`.

## Rollout

This is a foundation pass. After it lands, additional UI areas can be translated
incrementally by moving copy into the locale dictionaries and replacing hardcoded
strings with `t(key)`.

Future extraction order should follow product visibility:

1. New-project and start-run forms.
2. Approval panels and artifact review controls.
3. Dev Loop and Smoke Runs panels.
4. Memory, agents, workflow graph, and system diagnostics.

Each follow-up should be a focused slice with a small contract test rather than a
single large all-copy migration.
