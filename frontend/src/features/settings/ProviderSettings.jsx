import { useEffect, useState } from 'react';
import { Cable, Plus, RefreshCw } from 'lucide-react';
import { fetchProviderModels, getProviders, getProviderUsage, updateProvider } from './providerApi';
import './providerSettings.css';

const emptyDraft = () => ({ id: '', agent: 'codex', protocol: 'responses', name: '', base_url: '', api_key: '', model: '' });
const number = (value) => value == null ? '—' : new Intl.NumberFormat().format(value);

export default function ProviderSettings({ t }) {
  const [settings, setSettings] = useState(null);
  const [usage, setUsage] = useState(null);
  const [draft, setDraft] = useState(null);
  const [clearKey, setClearKey] = useState(false);
  const [models, setModels] = useState(null);
  const [pending, setPending] = useState('load');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [deleting, setDeleting] = useState('');
  const label = (key, values) => t(`providers.${key}`, values);

  const reload = async (signal) => {
    try {
      const [config, stats] = await Promise.all([getProviders(signal), getProviderUsage(signal)]);
      if (!signal?.aborted) { setSettings(config); setUsage(stats); }
    } catch (failure) {
      if (!signal?.aborted) setError(failure.message);
    } finally {
      if (!signal?.aborted) setPending('');
    }
  };
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getProviders(controller.signal), getProviderUsage(controller.signal)])
      .then(([config, stats]) => { if (!controller.signal.aborted) { setSettings(config); setUsage(stats); } })
      .catch((failure) => { if (!controller.signal.aborted) setError(failure.message); })
      .finally(() => { if (!controller.signal.aborted) setPending(''); });
    return () => controller.abort();
  }, []);

  const edit = (provider) => {
    setDraft(provider ? { ...provider, api_key: '' } : emptyDraft());
    setClearKey(false); setModels(null); setError(''); setNotice(''); setDeleting('');
  };
  const formPayload = () => {
    const fields = Object.fromEntries(['id', 'agent', 'protocol', 'name', 'base_url', 'api_key', 'model'].map((key) => [key, draft[key]]));
    if (draft.id && !draft.api_key && !clearKey) delete fields.api_key;
    if (clearKey) fields.api_key = '';
    return fields;
  };
  const mutate = async (action, values = {}) => {
    setPending(action); setError(''); setNotice('');
    try {
      const updated = await updateProvider({ action, expected_revision: settings.revision, ...values });
      setSettings(updated); setDeleting('');
      if (action === 'save') { setDraft(null); setClearKey(false); setModels(null); }
      setNotice(label(action === 'activate' ? 'activated' : action === 'delete' ? 'deleted' : 'saved'));
    } catch (failure) { setError(failure.message); }
    finally { setPending(''); }
  };
  const fetchModels = async () => {
    setPending('models'); setError(''); setModels(null);
    try {
      const result = await fetchProviderModels({ ...formPayload(), name: draft.name || 'Codex' });
      setModels(result.models);
    } catch (failure) { setError(failure.message); }
    finally { setPending(''); }
  };
  const change = (key, value) => {
    setDraft((current) => ({ ...current, [key]: value }));
    if (key === 'base_url' || key === 'api_key') setModels(null);
  };
  const disabled = Boolean(pending) || !settings?.configured;
  const active = settings?.active?.codex || '';

  return <section className="studio-settings-section provider-settings" aria-labelledby="settings-providers">
    <header><Cable size={19} aria-hidden="true" /><div><h2 id="settings-providers">{label('title')}</h2><p>{label('description')}</p></div></header>
    <div className="provider-settings__toolbar">
      <span className="provider-settings__agent">Codex</span>
      <button className="product-button product-button--secondary" type="button" disabled={Boolean(pending)} onClick={() => { setPending('load'); setError(''); reload(); }}><RefreshCw size={14} aria-hidden="true" />{label('refresh')}</button>
      <button className="product-button" type="button" disabled={disabled} onClick={() => edit()}><Plus size={14} aria-hidden="true" />{label('add')}</button>
    </div>
    {pending === 'load' ? <p role="status">{label('loading')}</p> : null}
    {settings && !settings.configured ? <p role="status">{label('databaseRequired')}</p> : null}
    {error ? <p className="provider-settings__error" role="alert">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {settings ? <div className="provider-settings__list">
      {[{ id: '', name: label('local'), base_url: label('localHint') }, ...settings.providers].map((provider) => <article key={provider.id || 'local'} className="provider-settings__connection" aria-label={provider.name}>
        <div className="provider-settings__identity"><strong>{provider.name}</strong>{active === provider.id ? <span className="provider-settings__badge">{label('active')}</span> : null}<p>{provider.base_url}</p>{provider.id ? <small>OpenAI Responses · {provider.model || label('inheritModel')} · {label(provider.has_api_key ? 'keyStored' : 'noKey')}</small> : null}</div>
        <div className="provider-settings__actions">
          {active !== provider.id ? <button type="button" className="product-button product-button--secondary" disabled={disabled} onClick={() => mutate('activate', { id: provider.id, agent: 'codex' })}>{label('activate')}</button> : null}
          {provider.id ? <><button type="button" className="product-button product-button--secondary" disabled={disabled} onClick={() => edit(provider)}>{label('edit')}</button><button type="button" className="provider-settings__text-button" disabled={disabled || active === provider.id} onClick={() => setDeleting(provider.id)}>{label('delete')}</button></> : null}
        </div>
        {deleting === provider.id && provider.id ? <div className="provider-settings__delete"><p>{label('deleteHint')}</p><button className="product-button" type="button" disabled={disabled} onClick={() => mutate('delete', { id: provider.id })}>{label('confirmDelete')}</button><button className="product-button product-button--secondary" type="button" onClick={() => setDeleting('')}>{label('cancel')}</button></div> : null}
      </article>)}
    </div> : null}
    {draft ? <form className="provider-settings__form" aria-label={label(draft.id ? 'edit' : 'add')} onSubmit={(event) => { event.preventDefault(); mutate('save', formPayload()); }}>
      <h3>{label(draft.id ? 'edit' : 'add')}</h3>
      <fieldset disabled={Boolean(pending)}>
        <div className="provider-settings__fields">
          <label>{label('name')}<input required maxLength={100} autoFocus value={draft.name} onChange={(event) => change('name', event.target.value)} /></label>
          <label>{label('protocol')}<select value={draft.protocol} onChange={(event) => change('protocol', event.target.value)}><option value="responses">OpenAI Responses</option></select></label>
          <label className="provider-settings__wide">{label('url')}<input required type="url" maxLength={2048} placeholder="https://api.example.com/v1" value={draft.base_url} onChange={(event) => change('base_url', event.target.value)} /><small>{label('urlHint')}</small></label>
          <label className="provider-settings__wide">API Key<input type="password" autoComplete="new-password" maxLength={8192} disabled={clearKey} placeholder={label(draft.has_api_key ? 'keyPlaceholder' : 'keyOptional')} value={draft.api_key} onChange={(event) => change('api_key', event.target.value)} /><small>{label('keyHint')}</small></label>
          {draft.has_api_key ? <label className="provider-settings__clear provider-settings__wide"><input type="checkbox" checked={clearKey} onChange={(event) => { setClearKey(event.target.checked); setModels(null); }} />{label('clearKey')}</label> : null}
          <label className="provider-settings__wide">{label('model')}<input list="provider-models" maxLength={200} value={draft.model} placeholder={label('modelPlaceholder')} onChange={(event) => change('model', event.target.value)} /><datalist id="provider-models">{(models || []).map((model) => <option key={model} value={model} />)}</datalist><small>{label('modelHint')}</small></label>
        </div>
        <div className="provider-settings__actions"><button type="button" className="product-button product-button--secondary" disabled={!draft.base_url} onClick={fetchModels}><RefreshCw size={14} aria-hidden="true" />{label(pending === 'models' ? 'fetchingModels' : 'fetchModels')}</button>{models ? <span role="status">{label(models.length ? 'modelsLoaded' : 'modelsEmpty', { count: models.length })}</span> : null}</div>
        {models?.length ? <label className="provider-settings__model-picker">{label('chooseModel')}<select value={models.includes(draft.model) ? draft.model : ''} onChange={(event) => change('model', event.target.value)}><option value="">{label('chooseModel')}</option>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label> : null}
        <div className="provider-settings__actions"><button type="submit" className="product-button">{label(pending === 'save' ? 'saving' : 'save')}</button><button type="button" className="product-button product-button--secondary" onClick={() => { setDraft(null); setModels(null); }}>{label('cancel')}</button></div>
      </fieldset>
    </form> : null}
    {usage ? <div className="provider-settings__usage">
      <h3>{label('usage')}</h3><p>{label('usageHint')}</p>
      <dl className="provider-settings__totals">{['total_tokens', 'input_tokens', 'cached_input_tokens', 'output_tokens'].map((key) => <div key={key}><dt>{label(key)}</dt><dd>{number(usage.totals[key])}</dd></div>)}</dl>
      <p>{label('coverage', { reported: usage.totals.reported_invocations, total: usage.totals.invocations })}</p>
      {usage.by_model.length ? <div className="provider-settings__table"><table><caption className="sr-only">{label('usage')}</caption><thead><tr>{['provider', 'modelName', 'invocations', 'input_tokens', 'cached_input_tokens', 'output_tokens'].map((key) => <th scope="col" key={key}>{label(key)}</th>)}</tr></thead><tbody>{usage.by_model.map((row) => <tr key={`${row.provider_id}:${row.model}`}><th scope="row">{settings?.providers.find((provider) => provider.id === row.provider_id)?.name || row.provider_name}</th><td>{row.model}</td><td>{row.reported_invocations}/{row.invocations}</td><td>{number(row.input_tokens)}</td><td>{number(row.cached_input_tokens)}</td><td>{number(row.output_tokens)}</td></tr>)}</tbody></table></div> : <p>{label('usageEmpty')}</p>}
    </div> : null}
  </section>;
}
