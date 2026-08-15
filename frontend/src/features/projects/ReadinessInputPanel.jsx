import { useMemo, useState } from 'react';
import Button from '../../components/ui/Button';
import { translate } from '../../i18n';

function requestItems(request) {
  if (Array.isArray(request?.fields)) {
    return request.fields.flatMap((field) => (
      Array.isArray(field?.options) ? field.options : []
    ));
  }
  for (const field of ['commands', 'constraints', 'candidates', 'items', 'options']) {
    const value = request?.[field];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function itemIdentity(item, index) {
  if (typeof item === 'string') return item;
  return String(item?.id || item?.command || item?.value || item?.name || index);
}

function itemLabel(item) {
  if (typeof item === 'string') return item;
  return String(item?.label || item?.command || item?.value || item?.name || item?.description || '');
}

function aggregateCommands(items) {
  const groups = new Map();
  items.forEach((item, index) => {
    const label = itemLabel(item);
    const existing = groups.get(label);
    const member = {
      identity: itemIdentity(item, index),
      recommended: Boolean(item?.recommended || item?.confirmed),
      source: typeof item === 'object' ? String(item?.source || '').trim() : '',
    };
    if (existing) {
      existing.members.push(member);
      if (member.source && !existing.sources.includes(member.source)) existing.sources.push(member.source);
      return;
    }
    groups.set(label, {
      ...item,
      aggregateKey: label,
      members: [member],
      sources: member.source ? [member.source] : [],
    });
  });
  return [...groups.values()];
}

function itemIdentities(item, index) {
  if (Array.isArray(item?.members)) return item.members.map((member) => member.identity);
  return [itemIdentity(item, index)];
}

function itemIsRecommended(item) {
  if (Array.isArray(item?.members)) return item.members.some((member) => member.recommended);
  return Boolean(item?.recommended || item?.confirmed);
}

function isStandardCommand(item) {
  const command = itemLabel(item).trim();
  if (command === 'npm test') return true;
  const npmScript = command.match(/^npm run ([^\s]+)$/)?.[1];
  if (npmScript) {
    return ['build', 'check', 'format', 'lint', 'test', 'typecheck'].includes(npmScript);
  }
  return ['build', 'check', 'format', 'lint', 'test'].includes(String(item?.kind || ''));
}

function isCommandRequest(request) {
  const kind = String(
    request?.kind
    || request?.type
    || request?.input_type
    || request?.step_id
    || '',
  ).toLowerCase();
  return kind.includes('command') || Array.isArray(request?.commands);
}

export default function ReadinessInputPanel({ busy = false, inputRequest, language = 'en', onSubmit }) {
  const t = (key, params) => translate(language, key, params);
  const items = useMemo(() => requestItems(inputRequest), [inputRequest]);
  const commandRequest = isCommandRequest(inputRequest);
  const displayItems = useMemo(
    () => (commandRequest ? aggregateCommands(items) : items),
    [commandRequest, items],
  );
  const [selected, setSelected] = useState(() => new Set(
    displayItems.flatMap((item, index) => (
      (itemIsRecommended(item) || (commandRequest && isStandardCommand(item)))
        ? itemIdentities(item, index)
        : []
    )),
  ));
  const [note, setNote] = useState('');
  if (!inputRequest) return null;

  const primaryItems = commandRequest
    ? displayItems.filter(isStandardCommand)
    : displayItems;
  const advancedItems = commandRequest
    ? displayItems.filter((item) => !isStandardCommand(item))
    : [];
  const commandSelectionRequired = commandRequest && items.length > 0;
  const canSubmit = !commandSelectionRequired || selected.size > 0;
  const showSelectionHint = commandSelectionRequired && selected.size === 0;
  const submit = (event) => {
    event.preventDefault();
    const confirmedItems = [...selected];
    const writtenConstraints = note.split('\n').map((item) => item.trim()).filter(Boolean);
    const values = commandRequest
      ? { confirmed_command_ids: confirmedItems }
      : {
          confirmed: true,
          constraints: confirmedItems.length > 0 ? confirmedItems : writtenConstraints,
        };
    onSubmit?.({
      step_id: inputRequest.step_id,
      values,
    });
  };
  const renderChoice = (item, index, keyPrefix = 'primary') => {
    const identities = itemIdentities(item, index);
    const identity = item.aggregateKey || identities[0];
    const label = itemLabel(item);
    const sources = Array.isArray(item.sources) ? item.sources : [];
    const visibleSources = sources.slice(0, 3);
    const remainingSources = sources.length - visibleSources.length;
    return (
      <label className="readiness-choice" key={`${keyPrefix}-${identity}-${index}`}>
        <input
          type="checkbox"
          checked={identities.every((itemId) => selected.has(itemId))}
          onChange={(event) => setSelected((current) => {
            const next = new Set(current);
            identities.forEach((itemId) => {
              if (event.target.checked) next.add(itemId);
              else next.delete(itemId);
            });
            return next;
          })}
        />
        {commandRequest ? (
          <span className="readiness-command-choice">
            <code>{label}</code>
            {visibleSources.length > 0 ? (
              <small>
                {visibleSources.join(' · ')}
                {remainingSources > 0 ? ` · ${t('readiness.input.commandSourcesMore', { count: remainingSources })}` : ''}
              </small>
            ) : null}
          </span>
        ) : <span>{label}</span>}
      </label>
    );
  };

  return (
    <form className="readiness-input-panel" onSubmit={submit}>
      <div>
        <p className="readiness-input-eyebrow">{t('readiness.input.eyebrow')}</p>
        <h3>{inputRequest.title || t(commandRequest ? 'readiness.input.commandsTitle' : 'readiness.input.constraintsTitle')}</h3>
        <p>{inputRequest.description || t(commandRequest ? 'readiness.input.commandsDescription' : 'readiness.input.constraintsDescription')}</p>
      </div>

      {items.length > 0 ? (
        <fieldset className="readiness-choice-list">
          <legend>{t(commandRequest ? 'readiness.input.recommendedLegend' : 'readiness.input.confirmLegend')}</legend>
          {primaryItems.map((item, index) => renderChoice(item, index))}
        </fieldset>
      ) : null}

      {advancedItems.length > 0 ? (
        <details className="readiness-advanced-commands">
          <summary>{t('readiness.input.advancedCommands', { count: advancedItems.length })}</summary>
          <fieldset className="readiness-choice-list">
            <legend className="sr-only">{t('readiness.input.advancedCommandsLegend')}</legend>
            {advancedItems.map((item, index) => renderChoice(item, index, 'advanced'))}
          </fieldset>
        </details>
      ) : null}

      <label className="readiness-form-field" htmlFor="readiness-input-note">
        <span>{t('readiness.input.noteLabel')}</span>
        <textarea
          id="readiness-input-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={t('readiness.input.notePlaceholder')}
          rows={3}
        />
      </label>
      {showSelectionHint ? (
        <p className="readiness-input-hint" id="readiness-command-selection-hint">
          {t('readiness.input.commandSelectionRequired')}
        </p>
      ) : null}
      <Button
        aria-describedby={showSelectionHint ? 'readiness-command-selection-hint' : undefined}
        busy={busy}
        disabled={!canSubmit}
        type="submit"
      >
        {t('readiness.actions.confirmAndContinue')}
      </Button>
    </form>
  );
}
