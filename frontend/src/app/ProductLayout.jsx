import { Moon, Sun } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';
import { useAppearancePreferences } from '../hooks/useAppearancePreferences';

const LANGUAGE_OPTIONS = [
  ['auto', 'product.language.auto'],
  ['en', 'product.language.en'],
  ['zh-CN', 'product.language.zhCN'],
];

export default function ProductLayout() {
  const {
    darkMode,
    setDarkMode,
    language,
    languagePreference,
    setLanguagePreference,
    t,
  } = useAppearancePreferences();

  return (
    <div className="product-shell">
      <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
      <header className="product-chrome">
        <div className="product-chrome__inner">
          <NavLink className="product-brand" to="/projects" aria-label={t('product.brand')}>
            <span className="product-brand__mark" aria-hidden="true">G</span>
            <span>{t('product.brand')}</span>
          </NavLink>

          <nav className="product-navigation" aria-label={t('product.navigation.label')}>
            <NavLink
              className={({ isActive }) => `product-navigation__link${isActive ? ' is-active' : ''}`}
              to="/projects"
            >
              {t('product.navigation.projects')}
            </NavLink>
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

      <Outlet context={{ language, t }} />
    </div>
  );
}
