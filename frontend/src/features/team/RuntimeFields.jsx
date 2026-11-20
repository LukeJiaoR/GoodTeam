/**
 * [INPUT]: 接收运行设置、Manager 档位和 Worker 模型目录。
 * [OUTPUT]: 提供模型与推理深度编辑字段。
 * [POS]: 分开呈现 Manager 档位和 Worker 目录能力。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { changeRuntimeSelection, newWorkerRuntime, runtimeOptions } from './teamModel';

export default function RuntimeFields({ draft, setDraft, backendOptions, agents, defaultProvider, agentsLoading, onRefresh, prompt = true, fixedProvider = false, managerReasoning, t }) {
  const [customModel, setCustomModel] = useState(false);
  const [promptOpen, setPromptOpen] = useState(Boolean(draft.additionalPrompt));
  const options = runtimeOptions(agents, draft.provider, draft.model, defaultProvider);
  agentsLoading = options.agent?.discovery_status ? options.agent.discovery_status === 'loading' : agentsLoading;
  const providers = fixedProvider ? [draft.provider || 'codex']
    : [...new Set([draft.provider, defaultProvider, ...backendOptions].filter(Boolean))];
  const changeSelection = (patch) => setDraft(managerReasoning ? { ...draft, ...patch }
    : changeRuntimeSelection(draft, patch, agents, defaultProvider));
  const effortLabel = (value) => t(`team.dialog.reasoning${value[0].toUpperCase()}${value.slice(1)}`);
  const reasoningDepths = managerReasoning
    ? [...new Set([...managerReasoning.reasoningDepths, managerReasoning.reasoningDefault])] : options.reasoningDepths;
  const effort = managerReasoning && draft.reasoningDepth === 'default' ? managerReasoning.reasoningDefault : draft.reasoningDepth;
  const currentUnsupported = effort !== 'default' && !reasoningDepths.includes(effort);
  const catalogUnconfirmed = !options.agent || options.agent.stale || ['unavailable', 'unsupported'].includes(options.agent.models_source);
  const catalogMessage = agentsLoading ? 'team.dialog.catalogLoading'
    : options.agent?.stale ? 'team.dialog.catalogStale'
    : !options.agent ? 'team.dialog.catalogUnknown'
      : options.agent.models_source === 'unsupported' ? 'team.dialog.catalogUnsupported'
        : options.agent.models_source === 'unavailable' ? 'team.dialog.catalogUnavailable' : null;
  // Backend-authored local diagnostics are plain text, never markup or actions.
  const diagnosticHint = typeof options.agent?.hint === 'string' ? options.agent.hint.trim() : '';
  const diagnosticFallback = options.agent?.available === false ? 'team.config.notInstalled'
    : options.agent?.ready === false ? 'team.config.notReady' : null;

  return (
    <section className="team-runtime-fields" aria-label={t('team.config.runtime')}>
      <div className="team-runtime-fields__heading">
        <h3>{t('team.config.runtime')}</h3>
        <button className="team-text-button" type="button" disabled={agentsLoading} onClick={onRefresh}>
          <RefreshCw aria-hidden="true" size={14} />{t(agentsLoading ? 'team.modelsLoading' : 'team.refreshModels')}
        </button>
      </div>
      <div className="team-runtime-fields__grid">
        <label>
          <span>{t('team.dialog.provider')}</span>
          <select aria-label={t('team.dialog.provider')} disabled={fixedProvider} value={draft.provider || defaultProvider} onChange={(event) => {
            setCustomModel(false);
            changeSelection({ provider: event.target.value, model: newWorkerRuntime(event.target.value).model });
          }}>
            {providers.map((provider) => <option key={provider} value={provider}>{fixedProvider && provider === 'codex' ? 'Codex app-server' : provider}</option>)}
          </select>
          {!agentsLoading && !options.agent?.stale && (diagnosticHint || diagnosticFallback) ? <small className={options.agent?.ready === false ? 'team-field-warning' : undefined}>
            {t('team.config.localDiagnostic')}: {diagnosticHint || t(diagnosticFallback)}
          </small> : null}
        </label>
        <div className="team-model-field">
          <label>
            <span>{t('team.dialog.model')}</span>
            {customModel ? (
              <input autoFocus aria-label={t('team.dialog.model')} value={draft.model} placeholder={t('team.config.modelPlaceholder')}
                onChange={(event) => changeSelection({ model: event.target.value })} />
            ) : (
              <select aria-label={t('team.dialog.model')} value={draft.model} onChange={(event) => changeSelection({ model: event.target.value })}>
                <option value="">{t('team.dialog.inheritModel')}{options.agent?.model_default ? ` (${options.agent.model_default})` : ''}</option>
                {options.models.map((model) => <option key={model} value={model}>{model}</option>)}
              </select>
            )}
          </label>
          <button type="button" className="team-text-button" onClick={() => setCustomModel(!customModel)}>
            {t(customModel ? 'team.config.chooseModel' : 'team.config.customModel')}
          </button>
          {catalogMessage ? <small role="status">{t(catalogMessage)}</small> : null}
          {options.agent ? <small>{t('team.dialog.catalogSource')}: {options.agent.stale_source || options.agent.models_source} {options.agent.observed_at || ''}</small> : null}
        </div>
        <label>
          <span>{t('team.dialog.reasoning')}</span>
          <select aria-label={t('team.dialog.reasoning')} disabled={!reasoningDepths.length && !currentUnsupported} value={effort} onChange={(event) => setDraft({ ...draft, reasoningDepth: event.target.value })}>
            {!managerReasoning ? <option value="default">{t('team.dialog.reasoningDefault')}</option> : null}
            {currentUnsupported ? <option value={effort} disabled>{effortLabel(effort)} · {t(agentsLoading || catalogUnconfirmed ? 'team.dialog.reasoningUnconfirmed' : 'team.dialog.reasoningUnavailable')}</option> : null}
            {reasoningDepths.map((depth) => <option key={depth} value={depth}>{effortLabel(depth)}{managerReasoning ? ` (${depth})` : null}</option>)}
          </select>
          {managerReasoning ? <small>{t('team.dialog.managerEffort')}</small> : <><small>{t(options.reasoningSource === 'model' ? 'team.dialog.effortModel' : 'team.dialog.effortAdapter')} {draft.reasoningDepth === 'default' ? t('team.dialog.effortAuto', { value: options.reasoningDefault || t('team.dialog.effortUnknown') }) : t('team.dialog.effortManual', { value: draft.reasoningDepth })}</small>
          {!agentsLoading && !catalogUnconfirmed && !currentUnsupported && !options.reasoningDepths.length ? <small>{t('team.config.reasoningAutomatic')}</small> : null}</>}
        </label>
      </div>
      <small className="team-runtime-fields__note">{t('team.config.localCheckScope')}</small>
      {prompt ? (
        <details className="team-runtime-advanced" open={promptOpen} onToggle={(event) => setPromptOpen(event.currentTarget.open)}>
          <summary>{t('team.config.advanced')}</summary>
          <label>
            <span>{t('team.dialog.additionalPrompt')}</span>
            <textarea aria-label={t('team.dialog.additionalPrompt')} maxLength="4000" rows="4" value={draft.additionalPrompt}
              onChange={(event) => setDraft({ ...draft, additionalPrompt: event.target.value })}
              placeholder={t('team.dialog.additionalPromptPlaceholder')} />
          </label>
        </details>
      ) : null}
    </section>
  );
}
