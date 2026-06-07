# Lightweight I18n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight frontend-only i18n foundation for `en` and `zh-CN`, with browser-language auto detection, a persisted language switcher, and a scoped translation pass for dashboard chrome.

**Architecture:** Create a small `frontend/src/i18n/` module with locale dictionaries, preference normalization, browser-language resolution, and translation lookup. Integrate it into `frontend/src/App.jsx` using local React state and `localStorage`, mirroring the existing theme preference pattern. Translate only high-traffic app chrome in this first slice.

**Tech Stack:** React 19, Vite, Tailwind v4, Python pytest contract tests in `tests/test_web.py`.

---

## File Structure

- Create `frontend/src/i18n/locales/en.js`: English dictionary for the first chrome slice.
- Create `frontend/src/i18n/locales/zh-CN.js`: Simplified Chinese dictionary matching the English keys.
- Create `frontend/src/i18n/index.js`: constants, preference normalization, browser-language detection, interpolation, and `translate`.
- Modify `frontend/src/App.jsx`: import i18n helpers, add `languagePreference` state, derive `language` and `t`, add language controls, and replace selected shell copy with `t(...)`.
- Modify `tests/test_web.py`: add contract tests for locale files, helper behavior, and App chrome usage.
- No backend files should change.

## Task 1: Add I18n Module Contract Tests

**Files:**
- Modify: `tests/test_web.py`
- Create later: `frontend/src/i18n/index.js`
- Create later: `frontend/src/i18n/locales/en.js`
- Create later: `frontend/src/i18n/locales/zh-CN.js`

- [ ] **Step 1: Add failing tests for the i18n module files and language behavior**

Add these tests near the existing frontend contract tests in `tests/test_web.py`:

```python
def test_frontend_i18n_locale_files_exist_and_define_core_chrome_keys():
    root = Path(__file__).resolve().parents[1]
    en = root / "frontend" / "src" / "i18n" / "locales" / "en.js"
    zh = root / "frontend" / "src" / "i18n" / "locales" / "zh-CN.js"

    assert en.exists()
    assert zh.exists()

    en_text = en.read_text(encoding="utf-8")
    zh_text = zh.read_text(encoding="utf-8")
    for key in [
        "nav.groups.work",
        "nav.tabs.overview",
        "nav.tabs.approvals",
        "chrome.dashboard",
        "chrome.exit",
        "chrome.runId",
        "language.auto",
        "language.english",
        "language.chinese",
        "theme.toggle",
        "approvalBanner.review",
    ]:
        assert key in en_text
        assert key in zh_text


def test_frontend_i18n_resolves_auto_language_and_falls_back_to_english():
    i18n_path = Path(__file__).resolve().parents[1] / "frontend" / "src" / "i18n" / "index.js"
    assert i18n_path.exists()
    i18n = i18n_path.read_text(encoding="utf-8")

    assert "gt-language" in i18n
    assert "LANGUAGE_PREFERENCE_AUTO = 'auto'" in i18n
    assert "SUPPORTED_LANGUAGE_PREFERENCES = ['auto', 'en', 'zh-CN']" in i18n
    assert "startsWith('zh')" in i18n or 'startsWith("zh")' in i18n
    assert "return 'zh-CN'" in i18n
    assert "return 'en'" in i18n
    assert "normalizeLanguagePreference" in i18n
    assert "resolveLanguage" in i18n
    assert "translate" in i18n
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing files**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n'
```

Expected: both new tests fail because `frontend/src/i18n/` files do not exist yet.

## Task 2: Implement the I18n Module

**Files:**
- Create: `frontend/src/i18n/locales/en.js`
- Create: `frontend/src/i18n/locales/zh-CN.js`
- Create: `frontend/src/i18n/index.js`
- Test: `tests/test_web.py`

- [ ] **Step 1: Create `frontend/src/i18n/locales/en.js`**

```javascript
export const en = {
  'nav.groups.work': 'Work',
  'nav.groups.observe': 'Observe',
  'nav.groups.inspect': 'Inspect',
  'nav.groups.system': 'System',
  'nav.tabs.overview': 'Overview',
  'nav.tabs.approvals': 'Approvals',
  'nav.tabs.artifacts': 'Artifacts',
  'nav.tabs.actions': 'Actions',
  'nav.tabs.board': 'Agile Board',
  'nav.tabs.workflow': 'Workflow Graph',
  'nav.tabs.devloop': 'Dev Loop',
  'nav.tabs.messages': 'Messages',
  'nav.tabs.agentsMemory': 'Agents & Memory',
  'nav.tabs.smoke': 'Smoke Runs',
  'nav.tabs.system': 'System',
  'chrome.dashboard': 'Dashboard',
  'chrome.exit': 'Exit',
  'chrome.runId': 'Run ID',
  'chrome.none': 'none',
  'chrome.multiProjectEyebrow': 'Multi-project control room',
  'chrome.goodteamProjects': 'GoodTeam Projects',
  'chrome.newProject': '+ New Project',
  'chrome.noActiveRun': 'No Activity',
  'chrome.loading': 'Loading...',
  'status.approved': 'Approved',
  'status.pending': 'Pending',
  'status.complete': 'Complete',
  'status.active': 'Active',
  'status.artifactReady': 'Artifact Ready',
  'approvalBanner.message': '{{count}} gate{{plural}} awaiting your approval{{questions}}',
  'approvalBanner.questions': ' · {{count}} Questions',
  'approvalBanner.review': 'Review',
  'theme.vivid': 'Vivid',
  'theme.minimal': 'Minimal',
  'theme.toggle': 'Toggle theme',
  'language.auto': 'Auto',
  'language.english': 'EN',
  'language.chinese': '中文',
  'language.useAuto': 'Use automatic language',
  'language.useEnglish': 'Use English',
  'language.useChinese': 'Use Chinese',
};
```

- [ ] **Step 2: Create `frontend/src/i18n/locales/zh-CN.js`**

```javascript
export const zhCN = {
  'nav.groups.work': '工作',
  'nav.groups.observe': '观察',
  'nav.groups.inspect': '检查',
  'nav.groups.system': '系统',
  'nav.tabs.overview': '概览',
  'nav.tabs.approvals': '审批',
  'nav.tabs.artifacts': '产物',
  'nav.tabs.actions': '动作',
  'nav.tabs.board': '敏捷看板',
  'nav.tabs.workflow': '工作流图',
  'nav.tabs.devloop': '开发循环',
  'nav.tabs.messages': '消息',
  'nav.tabs.agentsMemory': '智能体与记忆',
  'nav.tabs.smoke': '烟测运行',
  'nav.tabs.system': '系统',
  'chrome.dashboard': '仪表盘',
  'chrome.exit': '退出',
  'chrome.runId': '运行 ID',
  'chrome.none': '无',
  'chrome.multiProjectEyebrow': '多项目控制台',
  'chrome.goodteamProjects': 'GoodTeam 项目',
  'chrome.newProject': '+ 新建项目',
  'chrome.noActiveRun': '无活动',
  'chrome.loading': '加载中...',
  'status.approved': '已批准',
  'status.pending': '待处理',
  'status.complete': '已完成',
  'status.active': '活动中',
  'status.artifactReady': '产物就绪',
  'approvalBanner.message': '{{count}} 个审批门等待你确认{{questions}}',
  'approvalBanner.questions': ' · {{count}} 个问题',
  'approvalBanner.review': '审查',
  'theme.vivid': '鲜明',
  'theme.minimal': '极简',
  'theme.toggle': '切换主题',
  'language.auto': '自动',
  'language.english': 'EN',
  'language.chinese': '中文',
  'language.useAuto': '使用自动语言',
  'language.useEnglish': '使用英文',
  'language.useChinese': '使用中文',
};
```

- [ ] **Step 3: Create `frontend/src/i18n/index.js`**

```javascript
import { en } from './locales/en';
import { zhCN } from './locales/zh-CN';

export const LANGUAGE_STORAGE_KEY = 'gt-language';
export const LANGUAGE_PREFERENCE_AUTO = 'auto';
export const LANGUAGE_EN = 'en';
export const LANGUAGE_ZH_CN = 'zh-CN';
export const SUPPORTED_LANGUAGE_PREFERENCES = ['auto', 'en', 'zh-CN'];

const LOCALES = {
  [LANGUAGE_EN]: en,
  [LANGUAGE_ZH_CN]: zhCN,
};

export function normalizeLanguagePreference(value) {
  return SUPPORTED_LANGUAGE_PREFERENCES.includes(value) ? value : LANGUAGE_PREFERENCE_AUTO;
}

export function browserLanguagesFromNavigator(navigatorObject = globalThis.navigator) {
  if (!navigatorObject) return [];
  if (Array.isArray(navigatorObject.languages) && navigatorObject.languages.length > 0) {
    return navigatorObject.languages;
  }
  return navigatorObject.language ? [navigatorObject.language] : [];
}

export function resolveLanguage(preference, browserLanguages = browserLanguagesFromNavigator()) {
  const normalizedPreference = normalizeLanguagePreference(preference);
  if (normalizedPreference === LANGUAGE_EN || normalizedPreference === LANGUAGE_ZH_CN) {
    return normalizedPreference;
  }

  const firstLanguage = String(browserLanguages.find(Boolean) || '').toLowerCase();
  if (firstLanguage.startsWith('zh')) {
    return LANGUAGE_ZH_CN;
  }
  return LANGUAGE_EN;
}

function interpolate(template, params = {}) {
  return String(template).replace(/\{\{(\w+)\}\}/g, (_, key) => (
    Object.prototype.hasOwnProperty.call(params, key) ? String(params[key]) : ''
  ));
}

export function translate(language, key, params = {}) {
  const resolvedLanguage = language === LANGUAGE_ZH_CN ? LANGUAGE_ZH_CN : LANGUAGE_EN;
  const template = LOCALES[resolvedLanguage]?.[key] ?? LOCALES[LANGUAGE_EN]?.[key] ?? key;
  return interpolate(template, params);
}
```

- [ ] **Step 4: Run the focused i18n module tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n'
```

Expected: `2 passed`.

## Task 3: Add App Chrome I18n Contract Tests

**Files:**
- Modify: `tests/test_web.py`
- Modify later: `frontend/src/App.jsx`

- [ ] **Step 1: Add a failing contract test for App integration**

Add this test near the other frontend contract tests in `tests/test_web.py`:

```python
def test_frontend_contract_uses_i18n_for_dashboard_chrome():
    app = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")

    assert "LANGUAGE_STORAGE_KEY" in app
    assert "normalizeLanguagePreference" in app
    assert "resolveLanguage" in app
    assert "translate(language" in app or "translate(resolvedLanguage" in app
    assert "languagePreference" in app
    assert "setLanguagePreference" in app
    assert "navGroups" in app
    assert "t('nav.groups.work')" in app
    assert "t('nav.tabs.overview')" in app
    assert "t('chrome.dashboard')" in app
    assert "t('approvalBanner.review')" in app
    assert "t('chrome.loading')" in app
    assert "language.useAuto" in app
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" in app
```

- [ ] **Step 2: Run the new contract test and verify it fails**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_uses_i18n_for_dashboard_chrome -q
```

Expected: fail because `App.jsx` does not import or use the i18n module yet.

## Task 4: Integrate I18n State and Language Controls in `App.jsx`

**Files:**
- Modify: `frontend/src/App.jsx`
- Test: `tests/test_web.py`

- [ ] **Step 1: Import i18n helpers**

At the top of `frontend/src/App.jsx`, after the `MessageFeed` import, add:

```javascript
import {
  LANGUAGE_PREFERENCE_AUTO,
  LANGUAGE_STORAGE_KEY,
  normalizeLanguagePreference,
  resolveLanguage,
  translate,
} from './i18n';
```

- [ ] **Step 2: Add language preference state**

Inside `App()`, after the existing `theme` state, add:

```javascript
  const [languagePreference, setLanguagePreferenceState] = useState(() => {
    return normalizeLanguagePreference(localStorage.getItem(LANGUAGE_STORAGE_KEY));
  });
```

- [ ] **Step 3: Add derived translation helpers**

After the `languagePreference` state, add:

```javascript
  const language = resolveLanguage(languagePreference);
  const t = (key, params) => translate(language, key, params);
  const setLanguagePreference = (preference) => {
    setLanguagePreferenceState(normalizeLanguagePreference(preference));
  };
```

- [ ] **Step 4: Persist language preference**

After the existing theme persistence effect, add:

```javascript
  useEffect(() => {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, languagePreference);
  }, [languagePreference]);
```

- [ ] **Step 5: Add reusable language preference options**

After the derived `t` function and before existing computed constants, add:

```javascript
  const languageOptions = [
    { value: LANGUAGE_PREFERENCE_AUTO, label: t('language.auto'), ariaLabel: t('language.useAuto') },
    { value: 'en', label: t('language.english'), ariaLabel: t('language.useEnglish') },
    { value: 'zh-CN', label: t('language.chinese'), ariaLabel: t('language.useChinese') },
  ];
```

- [ ] **Step 6: Add inline language segmented controls**

Define this helper inside `App()` near `languageOptions`:

```javascript
  const renderLanguageSwitcher = (size = 'compact') => {
    const paddingClass = size === 'wide' ? 'px-3 py-1' : 'px-2 py-0.5';
    const roundedClass = size === 'wide' ? 'rounded-lg' : 'rounded';
    return (
      <div className="flex bg-surface-raised p-0.5 rounded-lg border border-border-subtle select-none" aria-label="Language">
        {languageOptions.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setLanguagePreference(option.value)}
            className={`${paddingClass} ${roundedClass} min-w-10 text-center text-2xs font-bold transition-all cursor-pointer ${languagePreference === option.value ? 'bg-surface-sunken shadow-sm text-accent-text' : 'text-text-muted hover:text-text-secondary'}`}
            aria-label={option.ariaLabel}
          >
            {option.label}
          </button>
        ))}
      </div>
    );
  };
```

- [ ] **Step 7: Add the language switcher to the root overview header controls**

In the root overview header, locate the controls containing the `Vivid` / `Minimal` buttons. After that theme segmented control and before the dark-mode button, insert:

```jsx
            {renderLanguageSwitcher('wide')}
```

- [ ] **Step 8: Add the language switcher to the project sidebar footer**

In the project sidebar footer, after the `Vivid` / `Minimal` segmented control and before the dark-mode button, insert:

```jsx
          {renderLanguageSwitcher()}
```

- [ ] **Step 9: Replace dark-mode aria labels**

Replace both hardcoded dark-mode button labels:

```jsx
aria-label="Toggle theme"
```

with:

```jsx
aria-label={t('theme.toggle')}
```

- [ ] **Step 10: Run the App integration contract test**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_uses_i18n_for_dashboard_chrome -q
```

Expected: still fail until chrome copy is replaced in Task 5, but failures should now be about missing `t(...)` usages rather than missing imports/state.

## Task 5: Extract First-Slice Chrome Copy in `App.jsx`

**Files:**
- Modify: `frontend/src/App.jsx`
- Test: `tests/test_web.py`

- [ ] **Step 1: Make status labels translatable**

Change `getStatusLabel(status)` to accept a translation function:

```javascript
function getStatusLabel(status, t = (key) => key) {
  const norm = String(status || '').toLowerCase().trim();
  if (norm === 'approved') return `✓ ${t('status.approved')}`;
  if (norm === 'pending') return `⏳ ${t('status.pending')}`;
  if (norm === 'complete') return `✓ ${t('status.complete')}`;
  if (norm === 'active' || norm === 'running') return t('status.active');
  if (norm === 'artifact ready' || norm === 'artifact_ready') return t('status.artifactReady');
  if (norm === 'no activity' || norm === 'no_activity' || norm === 'no_active_run') return t('chrome.noActiveRun');
  return status.replace(/_/g, ' ');
}
```

Then replace visible calls in the shell:

```jsx
{getStatusLabel(headerStatus, t)}
```

- [ ] **Step 2: Make the app shell spinner label translatable**

Change `Spinner` to accept a label:

```javascript
function Spinner({ label = 'Loading...' }) {
  return (
    <div className="flex justify-center items-center p-12">
      <RefreshCw className="h-8 w-8 animate-spin text-accent" />
      <span className="ml-3 text-sm text-text-secondary font-bold">{label}</span>
    </div>
  );
}
```

Then replace only these two app-shell spinner calls with a translated label:

- The root overview project-list loading spinner.
- The main project content loading spinner.

```jsx
<Spinner label={t('chrome.loading')} />
```

Leave deeper feature-panel spinners, such as workflow graph and devloop panel
spinners, for their later panel-specific translation passes.

- [ ] **Step 3: Replace sidebar navigation arrays with translated labels**

Replace the inline navigation array with a `navGroups` constant near the existing computed values before `return`:

```javascript
  const navGroups = [
    {
      title: t('nav.groups.work'),
      items: [
        { id: 'overview', label: t('nav.tabs.overview'), icon: LayoutDashboard },
        { id: 'approvals', label: t('nav.tabs.approvals'), icon: CheckCircle2 },
        { id: 'artifacts', label: t('nav.tabs.artifacts'), icon: FileCode },
        { id: 'actions', label: t('nav.tabs.actions'), icon: Activity },
        { id: 'board', label: t('nav.tabs.board'), icon: Kanban },
      ],
    },
    {
      title: t('nav.groups.observe'),
      items: [
        { id: 'workflow', label: t('nav.tabs.workflow'), icon: GitBranch },
        { id: 'devloop', label: t('nav.tabs.devloop'), icon: GitPullRequest },
        { id: 'messages', label: t('nav.tabs.messages'), icon: MessageSquare },
      ],
    },
    {
      title: t('nav.groups.inspect'),
      items: [
        { id: 'agents_memory', label: t('nav.tabs.agentsMemory'), icon: Users },
        { id: 'smoke', label: t('nav.tabs.smoke'), icon: PlaySquare },
      ],
    },
    {
      title: t('nav.groups.system'),
      items: [
        { id: 'system', label: t('nav.tabs.system'), icon: Settings },
      ],
    },
  ];
```

Then replace the current hardcoded array before `.map((group) => (` with:

```jsx
          {navGroups.map((group) => (
```

- [ ] **Step 4: Replace project shell labels**

Replace:

```jsx
{state?.project_name || startContext.project_name || 'Dashboard'}
```

with:

```jsx
{state?.project_name || startContext.project_name || t('chrome.dashboard')}
```

Replace:

```jsx
{state?.project_name || 'Dashboard'}
```

with:

```jsx
{state?.project_name || t('chrome.dashboard')}
```

Replace:

```jsx
Exit
```

inside the sidebar exit button with:

```jsx
{t('chrome.exit')}
```

Replace:

```jsx
Run ID: <code
```

with:

```jsx
{t('chrome.runId')}: <code
```

Replace:

```jsx
{state?.run_id || 'none'}
```

with:

```jsx
{state?.run_id || t('chrome.none')}
```

- [ ] **Step 5: Replace root overview header labels**

In the no-project root overview header, replace:

```jsx
Multi-project control room
```

with:

```jsx
{t('chrome.multiProjectEyebrow')}
```

Replace:

```jsx
GoodTeam Projects
```

with:

```jsx
{t('chrome.goodteamProjects')}
```

Replace both root header `+ New Project` button labels in this slice with:

```jsx
{t('chrome.newProject')}
```

- [ ] **Step 6: Replace theme button labels**

Replace each visible `Vivid` and `Minimal` label in theme segmented controls with:

```jsx
{t('theme.vivid')}
```

and:

```jsx
{t('theme.minimal')}
```

- [ ] **Step 7: Replace approval banner copy**

Before the return, add:

```javascript
  const approvalBannerQuestions = openQuestionsGateCount > 0
    ? t('approvalBanner.questions', { count: openQuestionsGateCount })
    : '';
```

Replace:

```jsx
<span>{actionableCount} gate{actionableCount > 1 ? 's' : ''} awaiting your approval{openQuestionsGateCount > 0 ? ` · ${openQuestionsGateCount} Questions` : ''}</span>
```

with:

```jsx
<span>{t('approvalBanner.message', {
  count: actionableCount,
  plural: actionableCount > 1 ? 's' : '',
  questions: approvalBannerQuestions,
})}</span>
```

Replace the banner button text:

```jsx
Review
```

with:

```jsx
{t('approvalBanner.review')}
```

- [ ] **Step 8: Run the App chrome contract test**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py::test_frontend_contract_uses_i18n_for_dashboard_chrome -q
```

Expected: `1 passed`.

## Task 6: Verify Build and Focused Frontend Contracts

**Files:**
- Verify only.

- [ ] **Step 1: Run the i18n and frontend contract tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n or frontend_contract'
```

Expected: all selected tests pass.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: Vite build succeeds without import or JSX errors.

- [ ] **Step 3: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output, exit code 0.

- [ ] **Step 4: Inspect the diff**

Run:

```bash
git diff -- frontend/src/i18n frontend/src/App.jsx tests/test_web.py
```

Expected: only the i18n module, scoped dashboard chrome translation, language controls, and contract tests changed. No backend files changed.

## Task 7: Update Progress Ledgers and Final Verification

**Files:**
- Modify: `docs/current-progress.md`
- Modify: `docs/mainline-progress.md`

- [ ] **Step 1: Update `docs/current-progress.md`**

Add a new top entry:

```markdown
- Footprint 2026-06-07 (s) — i18n target-language gate resolved and lightweight frontend i18n foundation landed. GoodTeam now supports `gt-language=auto|en|zh-CN`, auto-detects `zh-*` browsers to Simplified Chinese and falls back to English otherwise, exposes a compact language switcher beside theme controls, and translates the first high-traffic dashboard chrome slice through locale dictionaries instead of hardcoded JSX labels. Verification: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n or frontend_contract'` -> pass; `cd frontend && npm run build` -> pass; `git diff --check` clean.
```

- [ ] **Step 2: Update `docs/mainline-progress.md` active increment status**

In the execution order table, change the i18n row from gated/parked to foundation completed:

```markdown
| 6 | i18n | Lightweight foundation completed 2026-06-07 | Browser-language auto mode plus persisted `en`/`zh-CN` switcher and first dashboard chrome extraction. | Focused frontend contracts; frontend build passed |
```

In `## Active Increment`, remove `i18n target market/primary language` from the parked list because this decision is now resolved.

- [ ] **Step 3: Run final verification**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_web.py -q -k 'frontend_i18n or frontend_contract'
```

Expected: selected tests pass.

Run:

```bash
cd frontend && npm run build
```

Expected: build passes.

Run:

```bash
git diff --check
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit implementation**

```bash
git add frontend/src/i18n frontend/src/App.jsx tests/test_web.py docs/current-progress.md docs/mainline-progress.md
git commit -m "feat(ui): add lightweight i18n foundation"
```

Expected: commit succeeds.

## Self-Review Checklist

- The plan implements the approved spec's lightweight frontend-only approach.
- The first slice is limited to app chrome and does not attempt all 260+ strings.
- Tests are written before production code in each behavior slice.
- The language preference supports `auto`, `en`, and `zh-CN`.
- `zh-*` browser languages resolve to `zh-CN`; unsupported locales fall back to English.
- The language switcher is local-only and does not call backend APIs.
- Backend files are untouched.
