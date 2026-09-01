import {
  Bell,
  BookOpen,
  Bot,
  ClipboardCheck,
  LayoutDashboard,
  Moon,
  Settings2,
  Sun,
  Workflow,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import Breadcrumb from '../components/ui/Breadcrumb';
import { ToastProvider } from '../components/ui/ToastProvider';
import { useAppearancePreferences } from '../hooks/useAppearancePreferences';
import { routes } from '../lib/routing/routes';

const LANGUAGE_OPTIONS = [
  ['auto', 'product.language.auto'],
  ['en', 'product.language.en'],
  ['zh-CN', 'product.language.zhCN'],
];

export default function ProductLayout() {
  const { pathname } = useLocation();
  const [workDrawerOpen, setWorkDrawerOpen] = useState(false);
  const workDrawerCloseTimer = useRef(null);
  const {
    darkMode,
    setDarkMode,
    language,
    languagePreference,
    setLanguagePreference,
    t,
  } = useAppearancePreferences();
  const projectMatch = pathname.match(/^\/projects\/([^/]+)\/(?:home|work|review|knowledge|settings)/);
  const projectKey = (() => {
    try { return projectMatch ? decodeURIComponent(projectMatch[1]) : ''; } catch { return ''; }
  })();
  const isWorkRoute = Boolean(projectKey && /^\/projects\/[^/]+\/work(?:\/|$)/.test(pathname));
  const navClass = ({ isActive }) => `product-navigation__link${isActive ? ' is-active' : ''}`;
  const workNavClass = ({ isActive }) => `product-work-navigation__link${isActive ? ' is-active' : ''}`;
  const openWorkDrawer = useCallback(() => {
    globalThis.clearTimeout(workDrawerCloseTimer.current);
    setWorkDrawerOpen(true);
  }, []);
  const closeWorkDrawerSoon = useCallback(() => {
    globalThis.clearTimeout(workDrawerCloseTimer.current);
    workDrawerCloseTimer.current = globalThis.setTimeout(() => setWorkDrawerOpen(false), 140);
  }, []);

  useEffect(() => () => globalThis.clearTimeout(workDrawerCloseTimer.current), []);

  return (
    <ToastProvider>
      <div className={`product-shell${isWorkRoute ? ' product-shell--work' : ''}`}>
        <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
        <header className={`product-chrome${isWorkRoute ? ' product-work-chrome' : ''}`}>
          <div className="product-chrome__inner">
            <NavLink className="product-brand" to="/projects" aria-label={t('product.brand')}>
              <span className="product-brand__mark" aria-hidden="true">
                {isWorkRoute ? <Bot size={21} strokeWidth={1.9} /> : 'G'}
              </span>
              <span>{t('product.brand')}</span>
            </NavLink>

            {isWorkRoute ? (
              <nav className="product-work-navigation" aria-label={t('product.navigation.projectLabel')}>
                <NavLink className={workNavClass} to={routes.projectHome(projectKey)}>
                  <LayoutDashboard aria-hidden="true" size={17} />
                  <span>{t('product.workNavigation.overview')}</span>
                </NavLink>
                <NavLink
                  aria-controls="work-context-drawer"
                  aria-expanded={workDrawerOpen}
                  className={workNavClass}
                  onBlur={closeWorkDrawerSoon}
                  onFocus={openWorkDrawer}
                  onPointerEnter={openWorkDrawer}
                  onPointerLeave={closeWorkDrawerSoon}
                  to={routes.projectWork(projectKey)}
                >
                  <Workflow aria-hidden="true" size={17} />
                  <span>{t('product.workNavigation.workbench')}</span>
                </NavLink>
                <NavLink className={workNavClass} to={routes.projectReview(projectKey)}>
                  <ClipboardCheck aria-hidden="true" size={17} />
                  <span>{t('product.workNavigation.reviews')}</span>
                </NavLink>
                <NavLink className={workNavClass} to={routes.projectKnowledge(projectKey)}>
                  <BookOpen aria-hidden="true" size={17} />
                  <span>{t('product.workNavigation.knowledge')}</span>
                </NavLink>
                <NavLink className={workNavClass} to={routes.projectSettings(projectKey, 'general')}>
                  <Settings2 aria-hidden="true" size={17} />
                  <span>{t('product.workNavigation.settings')}</span>
                </NavLink>
              </nav>
            ) : (
              <nav className="product-navigation" aria-label={t('product.navigation.label')}>
                <NavLink className={navClass} to={routes.projects()}>{t('product.navigation.projects')}</NavLink>
                <NavLink className={navClass} to={routes.inbox()}>{t('product.navigation.inbox')}</NavLink>
                <NavLink className={navClass} to={routes.activity()}>{t('product.navigation.activity')}</NavLink>
                <NavLink className={navClass} to={routes.settings()}>{t('product.navigation.settings')}</NavLink>
              </nav>
            )}

            <div className={`product-preferences${isWorkRoute ? ' product-work-tools' : ''}`}>
              {isWorkRoute ? (
                <NavLink
                  className="product-work-tool product-work-tool--inbox"
                  to={routes.inbox()}
                  aria-label={t('product.navigation.inbox')}
                  title={t('product.navigation.inbox')}
                >
                  <Bell aria-hidden="true" size={18} />
                </NavLink>
              ) : null}
              <label className="product-language-control">
                <span className="sr-only">{t('product.language.label')}</span>
                <select
                  aria-label={t('product.language.label')}
                  value={languagePreference}
                  onChange={(event) => setLanguagePreference(event.target.value)}
                >
                  {LANGUAGE_OPTIONS.map(([value, labelKey]) => (
                    <option value={value} key={value}>{t(labelKey)}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="product-appearance-control"
                onClick={() => setDarkMode(!darkMode)}
                aria-label={`${t('product.appearance.label')}: ${t(darkMode ? 'product.appearance.dark' : 'product.appearance.light')}`}
                aria-pressed={darkMode}
                title={t('product.appearance.label')}
              >
                {darkMode ? <Moon aria-hidden="true" size={16} /> : <Sun aria-hidden="true" size={16} />}
                <span>{t(darkMode ? 'product.appearance.dark' : 'product.appearance.light')}</span>
              </button>
            </div>
          </div>
        </header>

        {projectKey && !isWorkRoute ? (
          <nav className="product-project-navigation" aria-label={t('product.navigation.projectLabel')}>
            <div>
              <span className="product-project-navigation__identity">{projectKey}</span>
              <NavLink className={navClass} to={routes.projectHome(projectKey)}>{t('product.navigation.home')}</NavLink>
              <NavLink className={navClass} to={routes.projectWork(projectKey)}>{t('product.navigation.work')}</NavLink>
              <NavLink className={navClass} to={routes.projectReview(projectKey)}>{t('product.navigation.review')}</NavLink>
              <NavLink className={navClass} to={routes.projectKnowledge(projectKey)}>{t('product.navigation.knowledge')}</NavLink>
              <NavLink className={navClass} to={routes.projectSettings(projectKey, 'general')}>{t('product.navigation.projectSettings')}</NavLink>
            </div>
          </nav>
        ) : null}

        {isWorkRoute ? null : <Breadcrumb projectKey={projectKey} t={t} />}

        <Outlet context={{
          language,
          t,
          workDrawer: isWorkRoute ? {
            open: workDrawerOpen,
            onEnter: openWorkDrawer,
            onLeave: closeWorkDrawerSoon,
          } : null,
        }} />
      </div>
    </ToastProvider>
  );
}
