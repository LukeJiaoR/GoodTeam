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
  const [selected, setSelected] = useState(() => new Set(
    items
      .filter((item) => item?.recommended || item?.confirmed)
      .map(itemIdentity),
  ));
  const [note, setNote] = useState('');
  if (!inputRequest) return null;

  const commandRequest = isCommandRequest(inputRequest);
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

  return (
    <form className="readiness-input-panel" onSubmit={submit}>
      <div>
        <p className="readiness-input-eyebrow">{t('readiness.input.eyebrow')}</p>
        <h3>{inputRequest.title || t(commandRequest ? 'readiness.input.commandsTitle' : 'readiness.input.constraintsTitle')}</h3>
        <p>{inputRequest.description || t(commandRequest ? 'readiness.input.commandsDescription' : 'readiness.input.constraintsDescription')}</p>
      </div>

      {items.length > 0 ? (
        <fieldset className="readiness-choice-list">
          <legend>{t('readiness.input.confirmLegend')}</legend>
          {items.map((item, index) => {
            const identity = itemIdentity(item, index);
            const label = itemLabel(item);
            return (
              <label className="readiness-choice" key={`${identity}-${index}`}>
                <input
                  type="checkbox"
                  checked={selected.has(identity)}
                  onChange={(event) => setSelected((current) => {
                    const next = new Set(current);
                    if (event.target.checked) next.add(identity);
                    else next.delete(identity);
                    return next;
                  })}
                />
                {commandRequest ? <code>{label}</code> : <span>{label}</span>}
              </label>
            );
          })}
        </fieldset>
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
