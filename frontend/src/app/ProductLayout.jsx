import {
  BookOpen,
  Bot,
  ClipboardCheck,
  Moon,
  Sun,
  Users,
  Workflow,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
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
  const projectMatch = pathname.match(/^\/projects\/([^/]+)\/(?:home|team|work|review|knowledge|settings)/);
  const projectKey = (() => {
    try { return projectMatch ? decodeURIComponent(projectMatch[1]) : ''; } catch { return ''; }
  })();
  const isProjectsRoute = pathname === '/projects';
  const isWorkRoute = Boolean(projectKey && /^\/projects\/[^/]+\/(?:team|work|review|knowledge)(?:\/|$)/.test(pathname));
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
      <div className={`product-shell${isWorkRoute || isProjectsRoute ? ' product-shell--neumorphic' : ''}${isWorkRoute ? ' product-shell--work' : ''}${isProjectsRoute ? ' product-shell--projects' : ''}`}>
        <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
        <header className={`product-chrome${isWorkRoute ? ' product-work-chrome' : ''}`}>
          <div className="product-chrome__inner">
            <NavLink className="product-brand" to="/projects" aria-label={t('product.brand')}>
              <span className="product-brand__mark" aria-hidden="true">
                {isWorkRoute || isProjectsRoute ? <Bot size={21} strokeWidth={1.9} /> : 'G'}
              </span>
              <span>{t('product.brand')}</span>
            </NavLink>

            {isWorkRoute ? (
              <nav className="product-work-navigation" aria-label={t('product.navigation.projectLabel')}>
                <NavLink className={workNavClass} to={routes.projectTeam(projectKey)}>
                  <Users aria-hidden="true" size={17} />
                  <span>{t('product.workNavigation.team')}</span>
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
              </nav>
            ) : <span className="product-chrome__spacer" aria-hidden="true" />}

            <div className={`product-preferences${isWorkRoute ? ' product-work-tools' : ''}`}>
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
