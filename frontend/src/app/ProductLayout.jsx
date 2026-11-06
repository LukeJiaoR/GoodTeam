import { BookOpen, ChevronRight, ClipboardCheck, Users, Workflow } from 'lucide-react';
import { useEffect, useState } from 'react';
import ProjectSidebar from './ProjectSidebar';
import { Outlet, useLocation } from 'react-router-dom';
import { ToastProvider } from '../components/ui/ToastProvider';
import { useAppearancePreferences } from '../hooks/useAppearancePreferences';
import { projectNavigationDestinations, rememberProjectLocation } from '../lib/routing/projectNavigation';

const PROJECT_PAGES = [
  ['work', Workflow, 'product.workNavigation.workbench'],
  ['review', ClipboardCheck, 'product.workNavigation.reviews'],
  ['team', Users, 'product.workNavigation.team'],
  ['knowledge', BookOpen, 'product.workNavigation.knowledge'],
];

export default function ProductLayout() {
  const { pathname, search } = useLocation();
  const preferences = useAppearancePreferences();
  const { language, t } = preferences;
  const [projectNavigationHost, setProjectNavigationHost] = useState(null);
  const [workQuery, setWorkQuery] = useState('');
  const [sidebarExpanded, setSidebarExpanded] = useState(() => !globalThis.matchMedia?.('(max-width: 767px)').matches);
  useEffect(() => {
    const query = globalThis.matchMedia?.('(max-width: 767px)');
    const closeOnNarrow = event => { if (event.matches) setSidebarExpanded(false); };
    query?.addEventListener?.('change', closeOnNarrow);
    return () => query?.removeEventListener?.('change', closeOnNarrow);
  }, []);
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
  const workRoute = currentSection === 'work';
  const openProjectNavigation = () => {
    setSidebarExpanded(true);
    const group = projectNavigationHost?.closest('details');
    if (group) group.open = true;
    requestAnimationFrame(() => projectNavigationHost?.querySelector('#project-task-navigation')?.focus());
  };
  return (
    <ToastProvider>
      <div data-sidebar={sidebarExpanded ? 'expanded' : 'collapsed'} className={`product-shell product-shell--workspace product-shell--studio${isWorkRoute ? ' product-shell--work' : ''}${isProjectsRoute ? ' product-shell--projects' : ''}`}>
        <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
        <ProjectSidebar projectKey={projectKey} workRoute={workRoute} destinations={destinations} settingsDestination={settingsDestination}
          preferences={preferences} navigationHost={projectNavigationHost} setNavigationHost={setProjectNavigationHost}
          query={workQuery} setQuery={setWorkQuery} expanded={sidebarExpanded} setExpanded={setSidebarExpanded} />
        <div className="studio-main">
          <header className="studio-topbar"><span>{projectKey || t('product.studio.workspace')}</span><ChevronRight size={13} aria-hidden="true" /><strong>{sectionTitle}</strong><span className="studio-topbar__label">{t('product.studio.localFirst')}</span></header>
          <Outlet context={{ language, t, preferences, projectNavigationHost, workQuery, openProjectNavigation, closeProjectNavigation: () => { if (globalThis.matchMedia?.('(max-width: 767px)').matches) setSidebarExpanded(false); } }} />
        </div>
      </div>
    </ToastProvider>
  );
}
