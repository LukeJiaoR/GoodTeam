import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import PageState from '../../components/ui/PageState';
import StatusBadge from '../../components/ui/StatusBadge';
import { translate } from '../../i18n';
import LoadProjectPanel from './LoadProjectPanel';
import { projectApi } from './projectApi';
import { normalizeProjectsPayload } from './projectList';

const GROUP_ORDER = ['attentionRequired', 'activeRun', 'preparing', 'ready', 'legacy'];

function groupForProject(item) {
  const lifecycle = item?.readiness?.lifecycle;
  if (Number(item?.attention_count) > 0 || lifecycle === 'attention_required') {
    return 'attentionRequired';
  }
  if (item?.active_run) return 'activeRun';
  if (lifecycle === 'preparing') return 'preparing';
  if (lifecycle === 'ready') return 'ready';
  return 'legacy';
}

function lifecycleTone(lifecycle, activeRun) {
  if (lifecycle === 'attention_required') return 'warning';
  if (activeRun) return 'active';
  if (lifecycle === 'ready') return 'success';
  if (lifecycle === 'preparing') return 'pending';
  return 'neutral';
}

function isAbortError(error, signal) {
  return signal.aborted || error?.name === 'AbortError';
}

function ProjectsSkeleton({ title }) {
  return (
    <section
      className="projects-list-state projects-list-skeleton"
      role="status"
      aria-label={title}
      aria-busy="true"
    >
      <span className="sr-only">{title}</span>
      {[0, 1, 2].map((item) => (
        <div className="project-row project-row--skeleton" data-testid="project-skeleton" key={item} aria-hidden="true">
          <span className="project-skeleton-line project-skeleton-line--title" />
          <span className="project-skeleton-line project-skeleton-line--path" />
          <span className="project-skeleton-line project-skeleton-line--action" />
        </div>
      ))}
    </section>
  );
}

export default function ProjectsPage({
  api: apiOverride,
  navigate: navigateOverride,
  language: languageOverride,
  reloadPage,
}) {
  const routerNavigate = useNavigate();
  const outletContext = useOutletContext();
  const api = apiOverride || outletContext?.api || projectApi;
  const navigate = navigateOverride || routerNavigate;
  const language = languageOverride || outletContext?.language || 'en';
  const t = (key, params) => translate(language, key, params);
  const [projects, setProjects] = useState([]);
  const [status, setStatus] = useState('loading');
  const [listRequest, setListRequest] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.listProjects({ signal: controller.signal })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setProjects(normalizeProjectsPayload(payload));
        setStatus('ready');
      })
      .catch((error) => {
        if (!isAbortError(error, controller.signal)) {
          setProjects([]);
          setStatus('error');
        }
      });
    return () => controller.abort();
  }, [api, listRequest]);

  const groups = useMemo(() => {
    const grouped = Object.fromEntries(GROUP_ORDER.map((key) => [key, []]));
    projects.forEach((item) => grouped[groupForProject(item)].push(item));
    return GROUP_ORDER.map((key) => ({ key, projects: grouped[key] }))
      .filter((group) => group.projects.length > 0);
  }, [projects]);

  const openProject = (projectKey) => {
    navigate(`/projects/${encodeURIComponent(projectKey)}/home`);
  };

  return (
    <main id="main-content" className="projects-page">
      <header className="projects-page-header">
        <h1>{t('projects.title')}</h1>
        <p>{t('projects.description')}</p>
      </header>

      <div className="projects-page-grid">
        <LoadProjectPanel
          api={api}
          language={language}
          reloadPage={reloadPage}
          onImported={(payload) => navigate(payload.home_url)}
        />

        <section className="projects-portfolio" aria-labelledby="projects-list-title">
          <div className="projects-section-heading">
            <h2 id="projects-list-title">{t('projects.list.title')}</h2>
          </div>

          {status === 'loading' ? <ProjectsSkeleton title={t('projects.list.loading')} /> : null}
          {status === 'error' ? (
            <PageState
              kind="error"
              title={t('product.errors.generic')}
              action={(
                <button
                  type="button"
                  className="projects-inline-action"
                  onClick={() => {
                    setStatus('loading');
                    setListRequest((value) => value + 1);
                  }}
                >
                  {t('projects.recovery.retry')}
                </button>
              )}
            />
          ) : null}
          {status === 'ready' && projects.length === 0 ? (
            <PageState
              kind="empty"
              title={t('projects.list.empty.title')}
              description={t('projects.list.empty.description')}
            />
          ) : null}
          {status === 'ready' && projects.length > 0 ? (
            <div className="project-groups">
              {groups.map((group) => (
                <section
                  className="project-group"
                  data-testid="project-group"
                  aria-labelledby={`project-group-${group.key}`}
                  key={group.key}
                >
                  <h3 id={`project-group-${group.key}`}>
                    {t(`projects.group.${group.key}`)}
                  </h3>
                  <div className="project-group-rows">
                    {group.projects.map((item) => {
                      const project = item.project || {};
                      const lifecycle = item.readiness?.lifecycle || 'legacy';
                      const statusLabel = lifecycle === 'attention_required'
                        ? t('projects.lifecycle.attention_required')
                        : item.active_run
                          ? t('projects.lifecycle.active')
                          : t(`projects.lifecycle.${lifecycle}`);
                      const attentionCount = Number(item.attention_count) || 0;
                      return (
                        <article className="project-row" key={project.project_key}>
                          <div className="project-row-copy">
                            <div className="project-row-title">
                              <h4>{project.display_name || project.project_key}</h4>
                              <StatusBadge tone={lifecycleTone(lifecycle, item.active_run)}>
                                {statusLabel}
                              </StatusBadge>
                            </div>
                            <code className="project-row-path">{project.root}</code>
                            {attentionCount > 0 ? (
                              <p className="project-row-context">
                                {t('projects.card.attentionCount', {
                                  count: attentionCount,
                                  plural: attentionCount === 1 ? '' : 's',
                                })}
                              </p>
                            ) : item.active_run ? (
                              <p className="project-row-context">
                                {t('projects.card.activeRun')}: {item.active_run.run_id}
                              </p>
                            ) : null}
                          </div>
                          <button
                            type="button"
                            className="projects-open-project"
                            onClick={() => openProject(project.project_key)}
                            aria-label={`${t('projects.openHome')}: ${project.display_name || project.project_key}`}
                          >
                            {t('projects.openHome')}
                          </button>
                        </article>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}
