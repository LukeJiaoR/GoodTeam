import { Moon, Sun } from 'lucide-react';
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

  return (
    <ToastProvider>
      <div className={`product-shell${isWorkRoute ? ' product-shell--work' : ''}`}>
        <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
        <header className="product-chrome">
          <div className="product-chrome__inner">
            <NavLink className="product-brand" to="/projects" aria-label={t('product.brand')}>
              <span className="product-brand__mark" aria-hidden="true">G</span>
              <span>{t('product.brand')}</span>
            </NavLink>

            <nav className="product-navigation" aria-label={t('product.navigation.label')}>
              <NavLink className={navClass} to={routes.projects()}>{t('product.navigation.projects')}</NavLink>
              <NavLink className={navClass} to={routes.inbox()}>{t('product.navigation.inbox')}</NavLink>
              <NavLink className={navClass} to={routes.activity()}>{t('product.navigation.activity')}</NavLink>
              <NavLink className={navClass} to={routes.settings()}>{t('product.navigation.settings')}</NavLink>
            </nav>

            <div className="product-preferences">
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

        {projectKey ? (
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

        <Breadcrumb projectKey={projectKey} t={t} />

        <Outlet context={{ language, t }} />
      </div>
    </ToastProvider>
  );
}
