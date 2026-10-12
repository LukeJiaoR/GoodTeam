import { BookOpen, Bot, ChevronRight, ClipboardCheck, FolderKanban, Moon, Settings2, Sun, Users, Workflow } from 'lucide-react';
import { useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { ToastProvider } from '../components/ui/ToastProvider';
import { useAppearancePreferences } from '../hooks/useAppearancePreferences';
import { projectNavigationDestinations, rememberProjectLocation } from '../lib/routing/projectNavigation';

const LANGUAGE_OPTIONS = [['auto', 'product.language.auto'], ['en', 'product.language.en'], ['zh-CN', 'product.language.zhCN']];
const PROJECT_PAGES = [
  ['work', Workflow, 'product.workNavigation.workbench'],
  ['review', ClipboardCheck, 'product.workNavigation.reviews'],
  ['team', Users, 'product.workNavigation.team'],
  ['knowledge', BookOpen, 'product.workNavigation.knowledge'],
];

export default function ProductLayout() {
  const { pathname, search } = useLocation();
  const preferences = useAppearancePreferences();
  const { darkMode, setDarkMode, language, languagePreference, setLanguagePreference, t } = preferences;
  const projectMatch = pathname.match(/^\/projects\/([^/]+)\/(?:home|team|work|review|knowledge|settings)(?:\/|$)/);
  const projectKey = (() => {
    try { return projectMatch ? decodeURIComponent(projectMatch[1]) : ''; } catch { return ''; }
  })();
  const destinations = projectNavigationDestinations(projectKey, pathname, search);
  const settingsDestination = projectKey ? `/projects/${encodeURIComponent(projectKey)}/settings` : '/settings';
  useEffect(() => { rememberProjectLocation(pathname, search); }, [pathname, search]);
  const isProjectsRoute = pathname === '/projects';
  const currentSection = pathname.match(/^\/projects\/[^/]+\/(work|review|team|knowledge|settings)(?:\/|$)/)?.[1];
  const currentPage = PROJECT_PAGES.find(([key]) => key === currentSection);
  const isWorkRoute = Boolean(projectKey);
  const sectionTitle = (pathname === '/settings' || currentSection === 'settings') ? t('product.studio.settings') : currentPage ? t(currentPage[2]) : t('product.studio.projects');
  const navClass = ({ isActive }) => `studio-navigation__link${isActive ? ' is-active' : ''}`;
  return (
    <ToastProvider>
      <div className={`product-shell product-shell--workspace product-shell--studio${isWorkRoute ? ' product-shell--work' : ''}${isProjectsRoute ? ' product-shell--projects' : ''}`}>
        <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
        <aside className="studio-sidebar" aria-label={t('product.studio.workspace')}>
          <NavLink className="studio-brand" to="/projects" aria-label={t('product.brand')}>
            <span className="studio-brand__mark"><Bot size={22} aria-hidden="true" /></span>
            <span>{t('product.brand')}<small>{t('product.studio.tagline')}</small></span>
          </NavLink>
          <NavLink className={navClass} to="/projects" end aria-label={t('product.studio.projects')} title={t('product.studio.projects')}><FolderKanban size={17} aria-hidden="true" /><span>{t('product.studio.projects')}</span></NavLink>
          {projectKey ? <>
            <div className="studio-project-context"><span className="studio-project-context__avatar" aria-hidden="true">{projectKey.slice(0, 1).toUpperCase()}</span><span><small>{t('product.studio.currentProject')}</small><strong title={projectKey}>{projectKey}</strong></span></div>
            <nav className="studio-navigation" aria-label={t('product.navigation.projectLabel')}>
              {PROJECT_PAGES.map(([key, Icon, label]) => <NavLink className={navClass} to={destinations[key]} key={key} aria-label={t(label)} title={t(label)}><Icon size={17} aria-hidden="true" /><span>{t(label)}</span></NavLink>)}
            </nav>
          </> : <div className="studio-sidebar__hint"><Workflow size={20} aria-hidden="true" /><p>{t('product.studio.chooseProject')}</p></div>}
          <div className="studio-sidebar__footer">
            <NavLink className={navClass} to={settingsDestination} aria-label={t('product.studio.settings')} title={t('product.studio.settings')}><Settings2 size={17} aria-hidden="true" /><span>{t('product.studio.settings')}</span></NavLink>
            <div className="studio-preferences">
              <label><span className="sr-only">{t('product.language.label')}</span><select aria-label={t('product.language.label')} value={languagePreference} onChange={(event) => setLanguagePreference(event.target.value)}>{LANGUAGE_OPTIONS.map(([value, key]) => <option value={value} key={value}>{t(key)}</option>)}</select></label>
              <button type="button" onClick={() => setDarkMode(!darkMode)} aria-label={`${t('product.appearance.label')}: ${t(darkMode ? 'product.appearance.dark' : 'product.appearance.light')}`} aria-pressed={darkMode} title={t('product.appearance.label')}>{darkMode ? <Moon size={16} aria-hidden="true" /> : <Sun size={16} aria-hidden="true" />}</button>
            </div>
          </div>
        </aside>
        <div className="studio-main">
          <header className="studio-topbar"><span>{projectKey || t('product.studio.workspace')}</span><ChevronRight size={13} aria-hidden="true" /><strong>{sectionTitle}</strong><span className="studio-topbar__label">{t('product.studio.localFirst')}</span></header>
          <Outlet context={{ language, t, preferences }} />
        </div>
      </div>
    </ToastProvider>
  );
}
