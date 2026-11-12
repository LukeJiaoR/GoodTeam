import { useEffect, useState } from 'react';
import { getProviders } from '../../settings/providerApi';

/** Saving config does not execute or test a provider. Only the saved revision can run. */
export default function TaskSearchConfiguration({ item, disabled, onAction, language, onEditingChange }) {
  const zh = String(language).startsWith('zh');
  const saved = item.web_search;
  const policyKnown = Object.hasOwn(item, 'web_search');
  const [editing, setEditing] = useState(false);
  const [providers, setProviders] = useState([]);
  const [error, setError] = useState('');
  const [enabled, setEnabled] = useState(Boolean(saved));
  const [providerId, setProviderId] = useState(saved?.provider?.id || '');
  const [model, setModel] = useState(saved?.model || '');
  const [limit, setLimit] = useState(saved?.max_requests || 1);
  const [timeout, setTimeout] = useState(saved?.timeout_seconds || 30);
  useEffect(() => {
    if (!editing) return;
    const controller = new AbortController();
    getProviders(controller.signal).then(data => { if (!controller.signal.aborted) setProviders(data.providers || []); })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [editing]);
  const provider = providers.find(value => value.id === providerId);
  const valid = !enabled || (provider && model.trim() && model.trim().length <= 200
    && Number.isInteger(Number(limit)) && Number(limit) >= 1 && Number(limit) <= 20
    && Number.isInteger(Number(timeout)) && Number(timeout) >= 1 && Number(timeout) <= 120);
  const readOnly = item.assignment?.execution_policy === 'read_only'
    && !['research', 'planner'].includes(item.assignment?.role);
  return <section aria-label={zh ? '任务搜索配置' : 'Task search configuration'}>
    <p>{zh ? '已保存的 Web Search：' : 'Saved Web Search: '}{saved
      ? `${saved.provider.id} @ ${saved.provider.revision} · ${saved.model} · ${saved.max_requests} requests · ${saved.timeout_seconds}s`
      : (policyKnown ? (zh ? '关闭' : 'Off') : (zh ? '旧任务配置未投影，请保存明确配置后执行' : 'Legacy policy not projected; save explicit configuration before running'))}</p>
    <p><small>{zh
      ? '搜索可能产生额外模型和搜索费用。次数限制不是金额上限，也不限制供应商内部子调用；查询会发送到所选供应商。结果不明不会自动重试。支持普通 Pi Worker，以及已接入只读方法的 Researcher/Planner；不授予写入权限。'
      : 'Search may incur extra model and search charges. Request limits are not spending caps or limits on provider subcalls. Queries go to the selected provider. Unknown outcomes are not retried. Supports ordinary Pi Workers and method-bound read-only Researcher/Planner tasks; no write permission is granted.'}</small></p>
    {editing ? <form onSubmit={async event => {
      event.preventDefault();
      if (!valid) return;
      const result = await onAction?.({ action: 'configure_queued_search', proposal_id: item.id,
        expected_revision: item.revision, confirmed: true, web_search: enabled ? {
          provider: { id: provider.id, revision: provider.revision }, model: model.trim(),
          max_requests: Number(limit), timeout_seconds: Number(timeout),
        } : null });
      if (result !== false) { setEditing(false); onEditingChange?.(false); }
    }}>
      <fieldset disabled={disabled}>
        <label><input type="checkbox" checked={enabled} disabled={readOnly} onChange={event => setEnabled(event.target.checked)} />{zh ? '允许本任务搜索' : 'Allow search for this task'}</label>
        {enabled && <>
          <label>{zh ? '供应商' : 'Provider'}<select required value={providerId} onChange={event => setProviderId(event.target.value)}>
            <option value="">{zh ? '选择已有供应商' : 'Choose existing provider'}</option>
            {providers.map(value => <option key={value.id} value={value.id}>{value.name} (revision {value.revision})</option>)}
          </select></label>
          <label>{zh ? '模型' : 'Model'}<input required maxLength={200} value={model} onChange={event => setModel(event.target.value)} /></label>
          <label>{zh ? '最多请求次数' : 'Maximum requests'}<input required type="number" min="1" max="20" step="1" value={limit} onChange={event => setLimit(event.target.value)} /></label>
          <label>{zh ? '单次超时（秒）' : 'Request timeout (seconds)'}<input required type="number" min="1" max="120" step="1" value={timeout} onChange={event => setTimeout(event.target.value)} /></label>
        </>}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={!valid}>{zh ? '确认费用提示并保存配置（不执行）' : 'Acknowledge charges and save configuration (no execution)'}</button>
        <button type="button" onClick={() => { setEditing(false); onEditingChange?.(false); }}>{zh ? '取消' : 'Cancel'}</button>
      </fieldset>
    </form> : <>
      <button type="button" disabled={disabled} onClick={() => { setEnabled(Boolean(saved)); setProviderId(saved?.provider?.id || ''); setModel(saved?.model || ''); setLimit(saved?.max_requests || 1); setTimeout(saved?.timeout_seconds || 30); setError(''); setEditing(true); onEditingChange?.(true); }}>{zh ? '配置搜索' : 'Configure search'}</button>
      <button type="button" disabled={disabled || !policyKnown} onClick={() => onAction?.({ action: 'start_queued_task_batch',
        proposal_ids: [item.id], expected_revisions: { [item.id]: item.revision }, confirmed: true,
      })}>{zh ? '确认范围、已保存配置与模型费用并执行此任务' : 'Confirm scope, saved configuration and model charges and run this task'}</button>
    </>}
  </section>;
}
