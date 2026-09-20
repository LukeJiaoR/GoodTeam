import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { changeRuntimeSelection, newWorkerRuntime, runtimeOptions } from './teamModel';

export default function RuntimeFields({ draft, setDraft, backendOptions, agents, defaultProvider, agentsLoading, onRefresh, prompt = true, t }) {
  const [customModel, setCustomModel] = useState(false);
  const [promptOpen, setPromptOpen] = useState(Boolean(draft.additionalPrompt));
  const options = runtimeOptions(agents, draft.provider, draft.model, defaultProvider);
  const providers = [...new Set([draft.provider, defaultProvider, ...backendOptions].filter(Boolean))];
  const changeSelection = (patch) => setDraft(changeRuntimeSelection(draft, patch, agents, defaultProvider));
  const effortLabel = (value) => t(`team.dialog.reasoning${value[0].toUpperCase()}${value.slice(1)}`);
  const currentUnsupported = draft.reasoningDepth !== 'default' && !options.reasoningDepths.includes(draft.reasoningDepth);
  const unavailable = !agentsLoading && (!options.agent || ['unavailable', 'unsupported'].includes(options.agent.models_source));

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
          <select aria-label={t('team.dialog.provider')} value={draft.provider || defaultProvider} onChange={(event) => {
            setCustomModel(false);
            changeSelection({ provider: event.target.value, model: newWorkerRuntime(event.target.value).model });
          }}>
            {providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
          </select>
          {options.agent?.available === false ? <small className="team-field-warning">{t('team.config.notInstalled')}</small>
            : options.agent?.ready === false ? <small className="team-field-warning">{t('team.config.notReady')}</small> : null}
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
          {unavailable ? <small role="status">{t('team.config.catalogFallback')}</small> : null}
        </div>
        <label>
          <span>{t('team.dialog.reasoning')}</span>
          <select aria-label={t('team.dialog.reasoning')} disabled={!options.reasoningDepths.length && !currentUnsupported} value={draft.reasoningDepth} onChange={(event) => setDraft({ ...draft, reasoningDepth: event.target.value })}>
            <option value="default">{t('team.dialog.reasoningDefault')}</option>
            {currentUnsupported ? <option value={draft.reasoningDepth} disabled>{effortLabel(draft.reasoningDepth)} · {t('team.dialog.reasoningUnavailable')}</option> : null}
            {options.reasoningDepths.map((depth) => <option key={depth} value={depth}>{effortLabel(depth)}</option>)}
          </select>
          {!options.reasoningDepths.length ? <small>{t('team.config.reasoningAutomatic')}</small> : null}
        </label>
      </div>
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
