/**
 * [INPUT]: 读取产品路由、项目导航目标与外观偏好。
 * [OUTPUT]: 提供共享顶栏、侧栏与页面 Outlet 上下文。
 * [POS]: 在产品路由外层协调项目与任务导航的展开和焦点。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { ChevronRight, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import ProjectSidebar from './ProjectSidebar';
import { Outlet, useLocation } from 'react-router-dom';
import { useAppearancePreferences } from '../hooks/useAppearancePreferences';
import { projectNavigationDestinations, rememberProjectLocation } from '../lib/routing/projectNavigation';

const PROJECT_PAGE_LABELS = {
  work: 'product.workNavigation.workbench',
  team: 'product.workNavigation.team',
  knowledge: 'product.workNavigation.knowledge',
  settings: 'product.studio.settings',
};
const NARROW_SIDEBAR = '(max-width: 767px)';
const isNarrowSidebar = () => globalThis.matchMedia?.(NARROW_SIDEBAR).matches ?? false;

export default function ProductLayout() {
  const { pathname, search } = useLocation();
  const preferences = useAppearancePreferences();
  const { language, t } = preferences;
  const [projectNavigationHost, setProjectNavigationHost] = useState(null);
  const [workQuery, setWorkQuery] = useState('');
  const [narrowSidebar, setNarrowSidebar] = useState(isNarrowSidebar);
  const [sidebarExpanded, setSidebarExpanded] = useState(() => !isNarrowSidebar());
  const sidebarToggleRef = useRef(null);
  useEffect(() => {
    const query = globalThis.matchMedia?.(NARROW_SIDEBAR);
    const syncSidebar = event => {
      setNarrowSidebar(event.matches);
      if (event.matches) setSidebarExpanded(false);
    };
    query?.addEventListener?.('change', syncSidebar);
    return () => query?.removeEventListener?.('change', syncSidebar);
  }, []);
  const projectMatch = pathname.match(/^\/projects\/([^/]+)\/(?:home|team|work|knowledge|settings)(?:\/|$)/);
  const projectKey = (() => {
    try { return projectMatch ? decodeURIComponent(projectMatch[1]) : ''; } catch { return ''; }
  })();
  const destinations = projectNavigationDestinations(projectKey, pathname, search);
  const settingsDestination = projectKey ? `/projects/${encodeURIComponent(projectKey)}/settings` : '/settings';
  useEffect(() => { rememberProjectLocation(pathname, search); }, [pathname, search]);
  const isProjectsRoute = pathname === '/projects';
  const currentSection = pathname.match(/^\/projects\/[^/]+\/(work|team|knowledge|settings)(?:\/|$)/)?.[1];
  const isWorkRoute = Boolean(projectKey);
  const sectionTitle = t(PROJECT_PAGE_LABELS[pathname === '/settings' ? 'settings' : currentSection] || 'product.studio.projects');
  const workRoute = currentSection === 'work';
  const sidebarLabel = t(sidebarExpanded ? 'work.navigation.collapse' : 'work.navigation.expand');
  const closeProjectNavigation = () => {
    if (!narrowSidebar || !sidebarExpanded) return;
    if (document.activeElement?.closest('#studio-sidebar')) sidebarToggleRef.current?.focus();
    setSidebarExpanded(false);
  };
  const handleSidebarEscape = event => {
    if (event.key !== 'Escape' || event.defaultPrevented || !sidebarExpanded) return;
    event.preventDefault();
    setSidebarExpanded(false);
    sidebarToggleRef.current?.focus();
  };
  const sidebarToggle = (
    <button type="button" ref={sidebarToggleRef} className="studio-sidebar-toggle"
      aria-label={sidebarLabel} title={sidebarLabel} aria-expanded={sidebarExpanded} aria-controls="studio-sidebar-content"
      onClick={() => setSidebarExpanded(!sidebarExpanded)} onKeyDown={handleSidebarEscape}>
      {sidebarExpanded ? <PanelLeftClose size={18} aria-hidden="true" /> : <PanelLeftOpen size={18} aria-hidden="true" />}
    </button>
  );
  const openProjectNavigation = () => {
    setSidebarExpanded(true);
    const group = projectNavigationHost?.closest('details');
    if (group) group.open = true;
    requestAnimationFrame(() => {
      const nav = projectNavigationHost?.querySelector('#project-task-navigation');
      nav?.scrollIntoView?.({ block: 'nearest' });
      nav?.focus();
    });
  };
  return (
    <div data-sidebar={sidebarExpanded ? 'expanded' : 'collapsed'} className={`product-shell product-shell--workspace product-shell--studio${isWorkRoute ? ' product-shell--work' : ''}${isProjectsRoute ? ' product-shell--projects' : ''}`}>
      <a className="product-skip-link" href="#main-content">{t('product.navigation.skipToContent')}</a>
      <header className="studio-topbar">
        {narrowSidebar ? sidebarToggle : null}
        <span className="studio-topbar__context" title={projectKey || undefined}>{projectKey || t('product.studio.workspace')}</span>
        <ChevronRight size={13} aria-hidden="true" />
        <strong>{sectionTitle}</strong>
      </header>
      <ProjectSidebar projectKey={projectKey} workRoute={workRoute} destinations={destinations} settingsDestination={settingsDestination}
        preferences={preferences} navigationHost={projectNavigationHost} setNavigationHost={setProjectNavigationHost}
        query={workQuery} setQuery={setWorkQuery} toggle={narrowSidebar ? null : sidebarToggle}
        onNavigate={closeProjectNavigation} onEscape={handleSidebarEscape} />
      <div className="studio-main">
        <Outlet context={{ language, t, preferences, projectNavigationHost, workQuery, openProjectNavigation, closeProjectNavigation }} />
      </div>
    </div>
  );
}
