import { useEffect, useRef, useState } from 'react';
import Button from '../../../components/ui/Button';
import ProductError from '../../../components/ui/ProductError';
import { isChangeDiscussion, sendConversationInput } from './conversationRouting';

export default function ConversationComposer({
  busy, embedded = true, error, onStart, suggestions = [], t,
  changeIntent, onDiscussChangeIntent, onKeepDiscussingChangeIntent,
}) {
  const [drafts, setDrafts] = useState({});
  const [modes, setModes] = useState({});
  const [sending, setSending] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const submitting = useRef(false);
  const inputRef = useRef(null);
  const editableIntent = isChangeDiscussion(changeIntent);
  const changing = editableIntent && modes[changeIntent.id] !== 'message';
  const scopeKey = changing ? `change:${changeIntent.id}` : 'message';
  const seed = changing && !changeIntent.interpretation ? changeIntent.draft_text || '' : '';
  const goal = drafts[scopeKey] ?? seed;
  const blocked = Boolean(busy || sending);
  const setGoal = (value) => setDrafts((current) => ({ ...current, [scopeKey]: value }));

  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, [scopeKey, changeIntent?.status]);

  const submit = async (event) => {
    event.preventDefault();
    if (blocked || submitting.current || !goal.trim()) return;
    submitting.current = true;
    setSending(true);
    setSubmitError(null);
    const sent = goal;
    try {
      const result = await sendConversationInput({
        text: sent,
        intent: changing ? changeIntent : null,
        onStart,
        onDiscuss: onDiscussChangeIntent,
        onKeepDiscussing: onKeepDiscussingChangeIntent,
      });
      if (result !== false) {
        setDrafts((current) => (current[scopeKey] ?? seed) === sent
          ? { ...current, [scopeKey]: '' } : current);
      }
    } catch (caught) {
      // Keep the exact input on network, interpretation and stale-target errors.
      setSubmitError(caught);
    } finally {
      submitting.current = false;
      setSending(false);
    }
  };

  return (
    <form className={embedded ? 'team-manager-entry' : 'work-start-panel'} onSubmit={submit}>
      {editableIntent ? (
        <div className="team-manager-suggestions" aria-label={t('work.manager.intent.question')}>
          <button type="button" aria-pressed={!changing} onClick={() => setModes((current) => ({ ...current, [changeIntent.id]: 'message' }))}>
            {t('work.taskAssistant.message')}
          </button>
          <button type="button" aria-pressed={changing} onClick={() => setModes((current) => ({ ...current, [changeIntent.id]: 'changes' }))}>
            {t('work.manager.requestChanges')}
          </button>
        </div>
      ) : null}
      {changing ? <p className="team-intent-commit__boundary">{t('work.manager.intent.confirmBoundary')}</p> : null}
      {!changing && suggestions.length ? (
        <div className="team-manager-suggestions" aria-label={t('work.taskAssistant.suggestions')}>
          {suggestions.map((suggestion) => (
            <button
              key={suggestion.id}
              onClick={() => setGoal(t(`work.taskAssistant.suggestionDrafts.${suggestion.id}`))}
              type="button"
            >
              {t(`work.taskAssistant.suggestionLabels.${suggestion.id}`)}
            </button>
          ))}
        </div>
      ) : null}
      <label>
        <span className="sr-only">{t(changing ? 'work.manager.changeReason' : 'work.taskAssistant.message')}</span>
        <textarea
          ref={inputRef}
          placeholder={t(changing ? 'work.manager.intent.question' : 'work.taskAssistant.placeholder')}
          rows="3"
          required
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
        />
      </label>
      {error || submitError ? <ProductError error={error || submitError} t={t} /> : null}
      <Button busy={blocked} disabled={blocked || !goal.trim()} type="submit">
        {t(changing ? 'work.manager.intent.reviewInterpretation' : 'work.taskAssistant.send')}
      </Button>
    </form>
  );
}
