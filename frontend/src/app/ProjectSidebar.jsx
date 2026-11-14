/**
 * [INPUT]: 依赖路由、项目状态与翻译
 * [OUTPUT]: 提供ProjectSidebar
 * [POS]: 项目导航入口，持续保留工作页链接
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { BookOpen, ChevronRight, ClipboardCheck, Folder, FolderKanban, MessageSquare, Moon, PanelLeftClose, PanelLeftOpen, Search, Settings2, Sun, Users, Workflow } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { projectApi } from '../features/projects/projectApi';
import useProjectsSnapshot from '../features/projects/useProjectsSnapshot';
import { routes } from '../lib/routing/routes';

const PAGES = [
  ['work', Workflow, 'product.workNavigation.workbench'],
  ['review', ClipboardCheck, 'product.workNavigation.reviews'],
  ['team', Users, 'product.workNavigation.team'],
  ['knowledge', BookOpen, 'product.workNavigation.knowledge'],
];

export default function ProjectSidebar({ projectKey, workRoute, destinations, settingsDestination, preferences, navigationHost, setNavigationHost, query, setQuery, expanded, setExpanded }) {
  const { darkMode, setDarkMode, languagePreference, setLanguagePreference, t } = preferences;
  const { projects, status, refresh } = useProjectsSnapshot(projectApi);
  const items = projects.map(item => item.project);
  if (projectKey && !items.some(item => item.project_key === projectKey)) items.unshift({ project_key: projectKey, display_name: projectKey });
  const navClass = ({ isActive }) => `studio-navigation__link${isActive ? ' is-active' : ''}`;
  const close = () => setExpanded(false);
  return <>
    <aside className="studio-sidebar" aria-label={t('product.studio.workspace')} onKeyDown={event => {
      if (event.key === 'Escape') { close(); event.currentTarget.querySelector('.studio-sidebar-toggle')?.focus(); }
    }}>
      <div className="studio-sidebar-heading">
        <NavLink className="studio-brand" to="/projects" aria-label={t('product.brand')}><FolderKanban size={18} aria-hidden="true" /><span>{t('product.brand')}</span></NavLink>
        <button type="button" className="studio-sidebar-toggle" aria-label={t(expanded ? 'work.navigation.collapse' : 'work.navigation.expand')} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}</button>
      </div>
      <div className="studio-sidebar-content">
        <label className="studio-sidebar-search"><Search size={14} aria-hidden="true" /><input aria-label={t('work.navigation.search')} placeholder={t('work.navigation.search')} value={query} onChange={event => setQuery(event.target.value)} type="search" /></label>
        <NavLink className={navClass} to="/projects" end aria-label={t('product.studio.projects')}><FolderKanban size={15} aria-hidden="true" /><span>{t('product.studio.projects')}</span></NavLink>
        <div className="studio-project-list">
          {['error', 'stale'].includes(status) ? <button type="button" className="studio-project-refresh" onClick={refresh}>{t('work.navigation.reloadProjects')}</button> : null}
          {items.map(project => {
            const key = project.project_key;
            const current = key === projectKey;
            if (!current && query && !`${key} ${project.display_name}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())) return null;
            return <details className="studio-project-group" key={`${projectKey}:${key}`} open={current}>
              <summary><Folder size={14} aria-hidden="true" /><span title={project.display_name}>{project.display_name}</span><ChevronRight size={12} aria-hidden="true" /></summary>
              {current && workRoute ? <div ref={setNavigationHost} className="studio-project-navigation-host">
                {!navigationHost ? <span className="studio-project-loading">{t('common.loading')}</span> : null}
              </div> : <NavLink className="studio-project-conversation" to={routes.projectWork(key)} onClick={() => { if (globalThis.matchMedia?.('(max-width: 767px)').matches) close(); }}><MessageSquare size={13} aria-hidden="true" />{t('assistant.project.projectContext')}</NavLink>}
            </details>;
          })}
        </div>
        {projectKey ? <nav className="studio-navigation" aria-label={t('product.navigation.projectLabel')}>
          <p className="studio-project-tools-label">{projectKey} · {t('product.studio.projectTools')}</p>
          {PAGES.map(([key, Icon, label]) => <NavLink className={navClass} to={destinations[key]} key={key} aria-label={t(label)}><Icon size={15} aria-hidden="true" /><span>{t(label)}</span></NavLink>)}
        </nav> : null}
        <div className="studio-sidebar__footer">
          <NavLink className={navClass} to={settingsDestination} aria-label={t('product.studio.settings')}><Settings2 size={15} aria-hidden="true" /><span>{t('product.studio.settings')}</span></NavLink>
          <div className="studio-preferences">
            <label><span className="sr-only">{t('product.language.label')}</span><select aria-label={t('product.language.label')} value={languagePreference} onChange={event => setLanguagePreference(event.target.value)}>{[['auto', 'auto'], ['en', 'en'], ['zh-CN', 'zhCN']].map(([value, label]) => <option value={value} key={value}>{t(`product.language.${label}`)}</option>)}</select></label>
            <button type="button" onClick={() => setDarkMode(!darkMode)} aria-label={`${t('product.appearance.label')}: ${t(darkMode ? 'product.appearance.dark' : 'product.appearance.light')}`} aria-pressed={darkMode}>{darkMode ? <Moon size={15} /> : <Sun size={15} />}</button>
          </div>
        </div>
      </div>
    </aside>
    {expanded ? <button type="button" className="studio-sidebar-backdrop" aria-label={t('work.navigation.collapse')} onClick={close} tabIndex={-1} /> : null}
  </>;
}
