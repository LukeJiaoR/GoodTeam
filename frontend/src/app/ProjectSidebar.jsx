/**
 * [INPUT]: 读取项目快照、路由目标、任务入口与外观偏好。
 * [OUTPUT]: 提供项目页面链接、任务导航挂载点与偏好控件。
 * [POS]: 由 ProductLayout 控制展开状态的共享侧栏。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { BookOpen, ChevronRight, Folder, FolderKanban, MessageSquare, Moon, Search, Settings2, Sun, Users, Workflow } from 'lucide-react';
import { Link, NavLink } from 'react-router-dom';
import { projectApi } from '../features/projects/projectApi';
import useProjectsSnapshot from '../features/projects/useProjectsSnapshot';
import { routes } from '../lib/routing/routes';

const PAGES = [
  ['work', Workflow, 'product.workNavigation.workbench'],
  ['team', Users, 'product.workNavigation.team'],
  ['knowledge', BookOpen, 'product.workNavigation.knowledge'],
];

export default function ProjectSidebar({ projectKey, workRoute, destinations, settingsDestination, preferences, navigationHost, setNavigationHost, query, setQuery, toggle, onNavigate, onEscape }) {
  const { darkMode, setDarkMode, languagePreference, setLanguagePreference, t } = preferences;
  const { projects, status, refresh } = useProjectsSnapshot(projectApi);
  const items = projects.map(item => item.project);
  if (projectKey && !items.some(item => item.project_key === projectKey)) items.unshift({ project_key: projectKey, display_name: projectKey });
  return (
    <aside id="studio-sidebar" className="studio-sidebar" aria-label={t('product.studio.workspace')} onKeyDown={onEscape}>
      <div className="studio-sidebar-heading">
        <Link className="studio-brand" to="/projects" onClick={onNavigate}>
          <span className="studio-brand__mark" aria-hidden="true"><FolderKanban size={17} /></span>
          <span>{t('product.brand')}</span>
        </Link>
        {toggle}
      </div>
      <div className="studio-sidebar-content" id="studio-sidebar-content">
        <NavLink className="studio-navigation__link" to="/projects" end aria-label={t('product.studio.projects')} title={t('product.studio.projects')} onClick={onNavigate}>
          <FolderKanban size={16} aria-hidden="true" /><span>{t('product.studio.projects')}</span>
        </NavLink>
        {projectKey ? <nav className="studio-navigation" aria-label={t('product.navigation.projectLabel')}>
          <p className="studio-project-tools-label">{t('product.studio.projectTools')}</p>
          {PAGES.map(([key, Icon, label]) => <NavLink className="studio-navigation__link" to={destinations[key]} key={key}
            aria-label={t(label)} title={t(label)} onClick={onNavigate}><Icon size={16} aria-hidden="true" /><span>{t(label)}</span></NavLink>)}
        </nav> : null}
        <label className="studio-sidebar-search"><Search size={14} aria-hidden="true" /><input aria-label={t('work.navigation.search')} placeholder={t('work.navigation.search')} value={query} onChange={event => setQuery(event.target.value)} type="search" /></label>
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
              </div> : <NavLink className="studio-project-conversation" to={routes.projectWork(key)} onClick={onNavigate}><MessageSquare size={13} aria-hidden="true" />{t('assistant.project.projectContext')}</NavLink>}
            </details>;
          })}
        </div>
        <div className="studio-sidebar__footer">
          <NavLink className="studio-navigation__link" to={settingsDestination} aria-label={t('product.studio.settings')} title={t('product.studio.settings')} onClick={onNavigate}><Settings2 size={16} aria-hidden="true" /><span>{t('product.studio.settings')}</span></NavLink>
          <div className="studio-preferences">
            <label><span className="sr-only">{t('product.language.label')}</span><select aria-label={t('product.language.label')} value={languagePreference} onChange={event => setLanguagePreference(event.target.value)}>{[['auto', 'auto'], ['en', 'en'], ['zh-CN', 'zhCN']].map(([value, label]) => <option value={value} key={value}>{t(`product.language.${label}`)}</option>)}</select></label>
            <button type="button" onClick={() => setDarkMode(!darkMode)} aria-label={`${t('product.appearance.label')}: ${t(darkMode ? 'product.appearance.dark' : 'product.appearance.light')}`} title={`${t('product.appearance.label')}: ${t(darkMode ? 'product.appearance.dark' : 'product.appearance.light')}`} aria-pressed={darkMode}>{darkMode ? <Moon size={15} /> : <Sun size={15} />}</button>
          </div>
        </div>
      </div>
    </aside>
  );
}
