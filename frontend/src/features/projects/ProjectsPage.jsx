/**
 * [INPUT]: 读取项目快照、URL 筛选并调用项目登记 API。
 * [OUTPUT]: 提供项目目录、导入和移除入口及跨项目待处理事项。
 * [POS]: 作为 /projects 的入口，将用户导向项目工作台。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { ArrowRight, FolderGit2, FolderPlus, Play, Plus } from 'lucide-react';
import { useRef, useState } from 'react';
import { Link, useNavigate, useOutletContext, useSearchParams } from 'react-router-dom';
import FileLocation from '../../components/ui/FileLocation';
import { fileName } from '../../lib/presentation/filePresentation';
import CollectionToolbar from '../../components/ui/CollectionToolbar';
import { browseProjects, projectActivity } from './projectBrowse';
import Button from '../../components/ui/Button';
import InlineNotice from '../../components/ui/InlineNotice';
import PageState from '../../components/ui/PageState';
import PageHeading from '../../components/ui/PageHeading';
import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';
import { routes } from '../../lib/routing/routes';
import LoadProjectPanel from './LoadProjectPanel';
import { projectApi } from './projectApi';
import useProjectsSnapshot, { notifyProjectsChanged } from './useProjectsSnapshot';
import ProjectAttentionOverview from './ProjectAttentionOverview';

function ProjectsSkeleton({ title }) {
  return (
    <section className="projects-launcher-list projects-list-skeleton" role="status" aria-label={title} aria-busy="true">
      <span className="sr-only">{title}</span>
      {[0, 1, 2].map((item) => (
        <div className="project-launcher-row project-row--skeleton" data-testid="project-skeleton" key={item} aria-hidden="true">
          <span className="project-skeleton-line project-skeleton-line--title" />
          <span className="project-skeleton-line project-skeleton-line--path" />
          <span className="project-skeleton-line project-skeleton-line--action" />
        </div>
      ))}
    </section>
  );
}

export default function ProjectsPage({ api: apiOverride, navigate: navigateOverride, language: languageOverride, reloadPage }) {
  const routerNavigate = useNavigate();
  const outletContext = useOutletContext();
  const api = apiOverride || outletContext?.api || projectApi;
  const navigate = navigateOverride || routerNavigate;
  const language = languageOverride || outletContext?.language || 'en';
  const t = (key, params) => translate(language, key, params);
  const { projects, status, refresh } = useProjectsSnapshot(api);
  const hasProjects = ['ready', 'refreshing', 'stale'].includes(status);
  const [importOpen, setImportOpen] = useState(false);
  const [projectToRemove, setProjectToRemove] = useState(null);
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState(false);
  const removeDialog = useRef(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get('q') || '';
  const requestedFilter = searchParams.get('filter');
  const filter = ['attention_required', 'active'].includes(requestedFilter) ? requestedFilter : 'all';
  const updateBrowse = (changes, replace = false) => setSearchParams(previous => {
    const next = new URLSearchParams(previous);
    for (const [key, value] of Object.entries(changes)) {
      if (!value || (key === 'filter' && value === 'all')) next.delete(key);
      else next.set(key, value);
    }
    return next;
  }, { replace });
  const setQuery = value => updateBrowse({ q: value }, true);
  const setFilter = value => updateBrowse({ filter: value });
  const visibleProjects = browseProjects(projects, { query, filter });
  const filters = ['all', 'attention_required', 'active'].map((key) => [
    key,
    t(key === 'all' ? 'collection.all' : `projects.lifecycle.${key}`),
    status === 'ready' && (key !== 'attention_required' || projects.every(item => item.attention))
      ? browseProjects(projects, { query: '', filter: key }).length : undefined,
  ]);

  const closeImport = () => {
    setImportOpen(false);
    requestAnimationFrame(() => document.getElementById('projects-add-project')?.focus());
  };

  const handleImported = (payload) => {
    const projectKey = payload?.project?.project_key;
    if (!projectKey) return;
    notifyProjectsChanged(api);
    navigate(routes.projectWork(projectKey));
  };

  const openRemove = (project) => {
    setProjectToRemove(project);
    setRemoveError(false);
    removeDialog.current.showModal();
  };

  const confirmRemove = async () => {
    setRemoving(true);
    setRemoveError(false);
    try {
      await api.removeProject(projectToRemove);
      removeDialog.current?.close();
      document.getElementById('projects-add-project')?.focus();
      notifyProjectsChanged(api);
    } catch {
      setRemoveError(true);
    } finally {
      setRemoving(false);
    }
  };

  return (
    <main id="main-content" className="projects-page" tabIndex={-1}>
      <PageHeading
        compact
        title={t('projects.launcher.title')}
        actions={(
          <Button id="projects-add-project" className="projects-add-button" onClick={() => setImportOpen(true)}>
            <Plus aria-hidden="true" size={17} />
            <span>{t('projects.launcher.add')}</span>
          </Button>
        )}
      />

      <div className="projects-overview">
        <section className="projects-launcher" aria-labelledby="projects-list-title">
          <h2 id="projects-list-title" className="sr-only">{t('projects.launcher.workspaces')}</h2>

          {hasProjects && projects.length > 0 ? (
            <CollectionToolbar label={t('projects.search')} query={query} onQueryChange={setQuery}
              filters={filters}
              selected={filter} onSelect={setFilter} count={visibleProjects.length} t={t} />
          ) : null}
          {hasProjects && projects.length > 0 && !visibleProjects.length ? (
            <div className="collection-empty"><p>{t('collection.noResults')}</p><Button variant="secondary" onClick={() => { updateBrowse({ q: '', filter: 'all' }); }}>{t('collection.reset')}</Button></div>
          ) : null}
          {status === 'loading' ? <ProjectsSkeleton title={t('projects.list.loading')} /> : null}
          {status === 'error' ? (
            <PageState
              kind="error"
              title={t('product.errors.generic')}
              action={(
                <Button variant="secondary" onClick={refresh}>
                  {t('projects.recovery.retry')}
                </Button>
              )}
            />
          ) : null}
          {status === 'ready' && projects.length === 0 ? (
            <div className="projects-launcher-empty">
              <span aria-hidden="true"><FolderPlus size={24} /></span>
              <h3>{t('projects.launcher.emptyTitle')}</h3>
              <p>{t('projects.launcher.emptyDescription')}</p>
              <Button variant="secondary" onClick={() => setImportOpen(true)}>
                <Plus aria-hidden="true" size={16} />
                {t('projects.launcher.add')}
              </Button>
            </div>
          ) : null}
          {hasProjects && projects.length > 0 ? (
            <ul className="projects-launcher-list" role="list">
              {visibleProjects.map((item) => {
                const project = item.project;
                const activity = projectActivity(item);
                return (
                  <li className="project-launcher-entry" key={project.project_key}>
                    <Link
                      className="project-launcher-row"
                      to={routes.projectWork(project.project_key)}
                      aria-label={`${t('projects.launcher.enter')}: ${project.display_name || project.project_key}`}
                    >
                      <span className="project-launcher-row__icon" aria-hidden="true">
                        <FolderGit2 size={18} />
                      </span>
                      <span className="project-launcher-row__copy">
                        <strong>{project.display_name || project.project_key}</strong>
                      </span>
                      {status === 'ready' ? <StatusBadge className="project-launcher-row__activity" data-state={activity}>
                        {activity === 'active' ? <Play aria-hidden="true" size={13} /> : null}
                        {t(activity === 'active' ? 'projects.launcher.active' : `projects.lifecycle.${activity}`)}
                      </StatusBadge> : null}
                      <span className="project-launcher-row__action">
                        <ArrowRight aria-hidden="true" size={17} />
                      </span>
                    </Link>
                    <Button variant="secondary" className="project-remove-button"
                      aria-label={t('projects.remove.label', { name: project.display_name })}
                      onClick={() => openRemove(project)}>
                      {t('projects.remove.action')}
                    </Button>
                    <FileLocation path={project.root} label={t('projects.folderLabel', { name: fileName(project.root) })} t={t} />
                  </li>
                );
              })}
            </ul>
          ) : null}
        </section>

        <ProjectAttentionOverview projects={projects} status={status} onRefresh={refresh} t={t} />
      </div>

      <dialog className="projects-import-dialog projects-remove-dialog" ref={removeDialog}
        aria-labelledby="projects-remove-title" aria-describedby="projects-remove-description"
        onCancel={(event) => { if (removing) event.preventDefault(); }}>
        <header className="projects-import-dialog__header">
          <div>
            <h1 id="projects-remove-title">{t('projects.remove.title')}</h1>
            <p id="projects-remove-description">
              {t('projects.remove.description', { name: projectToRemove?.display_name || '' })}
            </p>
          </div>
        </header>
        {removeError ? <InlineNotice tone="error" className="projects-remove-dialog__error">{t('projects.remove.error')}</InlineNotice> : null}
        <form method="dialog" className="projects-import-dialog__footer">
          <Button type="submit" variant="secondary" disabled={removing}>{t('projects.load.cancel')}</Button>
          <Button variant="danger" onClick={confirmRemove} busy={removing}>{t('projects.remove.title')}</Button>
        </form>
      </dialog>

      {importOpen ? (
        <LoadProjectPanel
          api={api}
          language={language}
          reloadPage={reloadPage}
          onClose={closeImport}
          onImported={handleImported}
        />
      ) : null}
    </main>
  );
}
