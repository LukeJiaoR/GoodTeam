import { useMemo } from 'react';
import { Link, Navigate, useOutletContext, useParams } from 'react-router-dom';
import ProductError from '../../components/ui/ProductError';
import { routes } from '../../lib/routing/routes';
import useProjectState from '../runs/useProjectState';
import { settingsApi } from './settingsApi';

const SECTIONS = ['general', 'integrations', 'delivery', 'diagnostics'];

export default function ProjectSettingsPage() {
  const { projectKey = '', section = 'general' } = useParams();
  const { t } = useOutletContext();
  const api = useMemo(() => ({ getProjectState: settingsApi.getProjectSettings }), []);
  const { data, error, refresh, status } = useProjectState(projectKey, { api, poll: false });
  const activeSection = SECTIONS.includes(section) ? section : 'general';
  if (section === 'workers') return <Navigate to={routes.projectTeam(projectKey)} replace />;
  if (status === 'loading') return <main id="main-content" className="product-page"><div className="product-workspace-skeleton" role="status"><span /><span /><span /></div></main>;
  if (status === 'error' && !data) return <main id="main-content" className="product-page"><ProductError error={error} onRetry={() => refresh()} t={t} /></main>;
  const target = data?.start_context?.project_report || {};
  return (
    <main id="main-content" className="product-page">
      <header className="product-page-header"><div><p className="product-eyebrow">{data?.start_context?.project_name || projectKey}</p><h1>{t('settings.project.title')}</h1><p>{t('settings.project.description')}</p></div><Link to={routes.projectHome(projectKey)}>{t('settings.project.back')}</Link></header>
      <div className="project-settings-layout"><nav aria-label={t('settings.project.sections')}>{SECTIONS.map((item) => <Link aria-current={activeSection === item ? 'true' : undefined} className={activeSection === item ? 'is-active' : ''} key={item} to={routes.projectSettings(projectKey, item)}>{t(`settings.project.${item}`)}</Link>)}</nav><section>
        {activeSection === 'general' ? <><h2>{t('settings.project.general')}</h2><dl className="settings-data-list"><div><dt>{t('settings.project.key')}</dt><dd><code>{projectKey}</code></dd></div><div><dt>{t('settings.project.root')}</dt><dd><code>{data?.start_context?.project_base || ''}</code></dd></div><div><dt>{t('settings.project.config')}</dt><dd><code>{data?.start_context?.project_config || ''}</code></dd></div></dl></> : null}
        {activeSection === 'integrations' ? <><h2>{t('settings.project.integrations')}</h2><pre>{JSON.stringify({ providers: target.providers || {}, task_backend: data?.start_context?.task_backend || {} }, null, 2)}</pre></> : null}
        {activeSection === 'delivery' ? <><h2>{t('settings.project.delivery')}</h2><pre>{JSON.stringify({ deployment: data?.start_context?.deployment_backend || {}, contract: data?.start_context?.delivery_contract || {} }, null, 2)}</pre></> : null}
        {activeSection === 'diagnostics' ? <><h2>{t('settings.project.diagnostics')}</h2><p className="product-boundary-note">{t('settings.project.diagnosticsBoundary')}</p><pre>{JSON.stringify({ worker_preflight: target.worker_backend_preflight || {}, reviewer_dispatch: target.reviewer_dispatch || {} }, null, 2)}</pre></> : null}
      </section></div>
    </main>
  );
}
