import { ArrowRight, FolderGit2, FolderPlus, Play, Plus } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useOutletContext, useSearchParams } from 'react-router-dom';
import FileLocation from '../../components/ui/FileLocation';
import { fileName } from '../../lib/presentation/filePresentation';
import CollectionToolbar from '../../components/ui/CollectionToolbar';
import { browseProjects, projectActivity } from './projectBrowse';
import Button from '../../components/ui/Button';
import PageState from '../../components/ui/PageState';
import PageHeading from '../../components/ui/PageHeading';
import { translate } from '../../i18n';
import { routes } from '../../lib/routing/routes';
import LoadProjectPanel from './LoadProjectPanel';
import { projectApi } from './projectApi';
import useProjectsSnapshot from './useProjectsSnapshot';
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
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get('q') || '';
  const requestedFilter = searchParams.get('filter');
  const filter = ['attention_required', 'active'].includes(requestedFilter) ? requestedFilter : 'all';
  const updateBrowse = (changes, replace = false) => setSearchParams(previous => {
    const next = new URLSearchParams(previous);
    for (const [key, value] of Object.entries(changes)) {
      if (!value || value === 'all') next.delete(key);
      else next.set(key, value);
    }
    return next;
  }, { replace });
  const setQuery = value => updateBrowse({ q: value }, true);
  const setFilter = value => updateBrowse({ filter: value });
  const visibleProjects = browseProjects(projects, { query, filter });

  const closeImport = () => {
    setImportOpen(false);
    requestAnimationFrame(() => document.getElementById('projects-add-project')?.focus());
  };

  const handleImported = (payload) => {
    const projectKey = payload?.project?.project_key;
    if (!projectKey) return;
    navigate(routes.projectWork(projectKey));
  };

  return (
    <main id="main-content" className="projects-page">
      <PageHeading
        eyebrow={t('projects.eyebrow')}
        title={t('projects.launcher.title')}
        description={t('projects.launcher.description')}
        actions={(
          <Button id="projects-add-project" className="projects-add-button" onClick={() => setImportOpen(true)}>
            <Plus aria-hidden="true" size={17} />
            <span>{t('projects.launcher.add')}</span>
          </Button>
        )}
      />

      {status === 'ready' && projects.length > 0 ? <section className="studio-project-overview" aria-label={t('product.studio.projectOverview')}>
        {[
          ['all', 'allProjects', projects.length],
          ['attention_required', 'needAttention', projects.filter(item => projectActivity(item) === 'attention_required').length],
          ['active', 'activeProjects', projects.filter(item => Boolean(item.active_run)).length],
        ].filter(([key]) => key !== 'attention_required' || projects.every(item => item.attention)).map(([key, label, count]) => <button type="button" key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}><span>{t(`product.studio.${label}`)}</span><strong>{count}</strong><ArrowRight size={16} aria-hidden="true" /></button>)}
      </section> : null}

      <ProjectAttentionOverview projects={projects} status={status} onRefresh={refresh} t={t} />

      <section className="projects-launcher" aria-labelledby="projects-list-title">
        <header className="projects-launcher__heading">
          <div>
            <h2 id="projects-list-title">{t('projects.launcher.workspaces')}</h2>
            <p>{t('projects.launcher.workspacesDescription')}</p>
          </div>
          {hasProjects && projects.length > 0 ? (
            <span>{t('projects.launcher.count', { count: projects.length })}</span>
          ) : null}
        </header>

        {hasProjects && projects.length > 0 ? (
          <CollectionToolbar label={t('projects.search')} query={query} onQueryChange={setQuery}
            filters={['all', 'attention_required', 'active'].map((key) => [key, t(key === 'all' ? 'collection.all' : `projects.lifecycle.${key}`)])}
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
          <ul className="projects-launcher-list">
            {visibleProjects.map((item) => {
              const project = item.project;
              return (
                <li className="project-launcher-entry" key={project.project_key}>
                <Link
                  className="project-launcher-row"
                  to={routes.projectWork(project.project_key)}
                  aria-label={`${t('projects.launcher.enter')}: ${project.display_name || project.project_key}`}
                >
                  <span className="project-launcher-row__icon" aria-hidden="true">
                    <FolderGit2 size={19} />
                  </span>
                  <span className="project-launcher-row__copy">
                    <strong>{project.display_name || project.project_key}</strong>
                    <small>{t('projects.folderLabel', { name: fileName(project.root) })}</small>
                  </span>
                  {status === 'ready' ? <span className="project-launcher-row__activity" data-state={projectActivity(item)}>
                    {projectActivity(item) === 'active' ? <Play aria-hidden="true" size={13} /> : null}
                    {t(projectActivity(item) === 'active' ? 'projects.launcher.active' : `projects.lifecycle.${projectActivity(item)}`)}
                  </span> : null}
                  <span className="project-launcher-row__action">
                    {t('projects.launcher.enter')}
                    <ArrowRight aria-hidden="true" size={16} />
                  </span>
                </Link>
                <FileLocation path={project.root} t={t} />
                </li>
              );
            })}
          </ul>
        ) : null}
      </section>

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
