import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';
import Button from '../../components/ui/Button';
import ProductError from '../../components/ui/ProductError';
import StatusBadge from '../../components/ui/StatusBadge';
import { useToast } from '../../components/ui/ToastProvider';
import { useAppearancePreferences } from '../../hooks/useAppearancePreferences';
import { settingsApi } from './settingsApi';

export default function SettingsPage() {
  const { t } = useOutletContext();
  const appearance = useAppearancePreferences();
  const toast = useToast();
  const [health, setHealth] = useState(null);
  const [agents, setAgents] = useState([]);
  const [workspaceSettings, setWorkspaceSettings] = useState(null);
  const [defaults, setDefaults] = useState({ worker_backend: '', model_profile: '' });
  const [error, setError] = useState(null);
  const [mutationError, setMutationError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([settingsApi.getSettingsOverview({ signal: controller.signal }), settingsApi.getSettingsAgents({ signal: controller.signal }), settingsApi.getWorkspaceSettings({ signal: controller.signal })])
      .then(([overview, agentPayload, settings]) => { setHealth(overview); setAgents(agentPayload.agents || []); setWorkspaceSettings(settings); setDefaults(settings.defaults); })
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught); });
    return () => controller.abort();
  }, []);
  const saveDefaults = async (event) => {
    event.preventDefault(); setSaving(true); setSaved(false); setMutationError(null);
    try {
      const settings = await settingsApi.saveWorkspaceSettings(defaults);
      setWorkspaceSettings(settings);
      setDefaults(settings.defaults);
      setSaved(true);
      toast.success(t('toast.settingsSaved'));
    }
    catch (caught) { setMutationError(caught); }
    finally { setSaving(false); }
  };
  return (
    <main id="main-content" className="product-page">
      <header className="product-page-header"><div><h1>{t('settings.title')}</h1><p>{t('settings.description')}</p></div></header>
      {error ? <ProductError error={error} t={t} /> : null}
      <div className="settings-section-list">
        <section><header><h2>{t('settings.appearance.title')}</h2><p>{t('settings.appearance.description')}</p></header><div className="settings-controls"><label><span>{t('product.language.label')}</span><select value={appearance.languagePreference} onChange={(event) => appearance.setLanguagePreference(event.target.value)}><option value="auto">{t('product.language.auto')}</option><option value="en">{t('product.language.en')}</option><option value="zh-CN">{t('product.language.zhCN')}</option></select></label><label className="settings-check"><input type="checkbox" checked={appearance.darkMode} onChange={(event) => appearance.setDarkMode(event.target.checked)} /><span>{t('settings.appearance.dark')}</span></label></div></section>
        <section><header><h2>{t('settings.health.title')}</h2><p>{t('settings.health.description')}</p></header><div className="settings-health"><StatusBadge tone={health ? 'success' : 'pending'}>{health ? t('settings.health.available') : t('settings.health.checking')}</StatusBadge><span>{t('settings.health.projects', { count: health?.project_items?.length || 0 })}</span></div></section>
        <section><header><h2>{t('settings.workers.title')}</h2><p>{t('settings.workers.description')}</p></header><div className="settings-controls"><form className="settings-controls" onSubmit={saveDefaults}><label><span>{t('settings.workers.defaultBackend')}</span><select value={defaults.worker_backend} onChange={(event) => { setDefaults({ ...defaults, worker_backend: event.target.value }); setSaved(false); }}>{(workspaceSettings?.options?.worker_backends || []).map((value) => <option key={value}>{value}</option>)}</select></label><label><span>{t('settings.workers.defaultProfile')}</span><select value={defaults.model_profile} onChange={(event) => { setDefaults({ ...defaults, model_profile: event.target.value }); setSaved(false); }}>{(workspaceSettings?.options?.model_profiles || []).map((value) => <option key={value}>{value}</option>)}</select></label>{mutationError ? <ProductError error={mutationError} t={t} /> : null}{saved ? <p role="status">{t('settings.workers.saved')}</p> : null}<Button busy={saving} disabled={!defaults.worker_backend || !defaults.model_profile} type="submit">{t('settings.workers.save')}</Button><p className="product-boundary-note">{t('settings.workers.boundary')}</p></form>{agents.length ? <details><summary>{t('settings.workers.detected', { count: agents.length })}</summary><ul className="settings-agent-list">{agents.map((agent) => <li key={agent.mode || agent.name}><div><strong>{agent.label || agent.mode || agent.name}</strong><code>{agent.command_path || agent.command || ''}</code></div><StatusBadge tone={agent.available || agent.ready ? 'success' : 'pending'}>{agent.available || agent.ready ? t('settings.workers.ready') : t('settings.workers.unavailable')}</StatusBadge></li>)}</ul></details> : <p className="product-empty-copy">{t('settings.workers.empty')}</p>}</div></section>
        <section><header><h2>{t('settings.security.title')}</h2><p>{t('settings.security.description')}</p></header><ul className="settings-boundaries"><li>{t('settings.security.sameOrigin')}</li><li>{t('settings.security.approvals')}</li><li>{t('settings.security.targetWrites')}</li></ul></section>
        <section><header><h2>{t('settings.diagnostics.title')}</h2><p>{t('settings.diagnostics.description')}</p></header><details><summary>{t('settings.diagnostics.open')}</summary><pre>{JSON.stringify(workspaceSettings?.diagnostics || {}, null, 2)}</pre></details></section>
      </div>
    </main>
  );
}
