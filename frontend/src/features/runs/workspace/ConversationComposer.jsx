import { useEffect, useRef, useState } from 'react';
import Button from '../../../components/ui/Button';
import ProductError from '../../../components/ui/ProductError';
import { isChangeDiscussion, sendConversationInput } from './conversationRouting';
import { shouldSubmitConversationOnEnter } from './conversationFeedback';
import ConversationChangeContext from './ConversationChangeContext.jsx';
import { matchesSavedChange } from './conversationChangeContext';

export default function ConversationComposer({
  busy, embedded = true, error, onStart, onQueueTask, suggestions = [], t,
  changeIntent, onDiscussChangeIntent, onKeepDiscussingChangeIntent,
  activeTask = false, changeTarget = null, recordedChange, onStartChangeIntent,
}) {
  const [entryMode, setEntryMode] = useState('message');
  const [drafts, setDrafts] = useState({});
  const [modes, setModes] = useState({});
  const [sending, setSending] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const submitting = useRef(false);
  const inputRef = useRef(null);
  const editableIntent = isChangeDiscussion(changeIntent);
  const queuing = entryMode === 'new_task';
  const changing = !queuing && editableIntent && modes[changeIntent.id] !== 'message';
  const scopeKey = queuing ? 'new_task' : changing ? `change:${changeIntent.id}` : 'message';
  // Keep discussing retains the previous interpretation. Restore the exact
  // durable draft by discussion status, not by whether an interpretation exists.
  const seed = changing && changeIntent.status === 'discussing' ? changeIntent.draft_text || '' : '';
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
      const result = queuing ? await onQueueTask(sent) : await sendConversationInput({
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

  const beginChange = async () => {
    if (blocked || submitting.current || !changeTarget || changeIntent || !onStartChangeIntent) return;
    submitting.current = true;
    setSending(true);
    setSubmitError(null);
    const sent = goal;
    try {
      // Creating a draft does not interpret, confirm, approve, retry or dispatch.
      const result = await onStartChangeIntent(changeTarget.approval, changeTarget, sent);
      if (result === false) return;
      if (!matchesSavedChange(result, changeTarget, sent)) {
        throw new Error(t('work.changeContext.unconfirmed'));
      }
      setModes((current) => ({ ...current, [result.intent_commit.id]: 'changes' }));
      setDrafts((current) => (current[scopeKey] ?? seed) === sent
        ? { ...current, [scopeKey]: '' } : current);
    } catch (caught) {
      // An uncertain response is not permission to retry automatically or lose text.
      setSubmitError(caught);
    } finally {
      submitting.current = false;
      setSending(false);
    }
  };

  return (
    <form className={embedded ? 'team-manager-entry' : 'work-start-panel'} onSubmit={submit}>
      {onQueueTask ? <div className="project-composer-modes" aria-label={t('assistant.project.inputMode')}>
        <button type="button" aria-pressed={!queuing} onClick={() => { setEntryMode('message'); if (changeIntent?.id) setModes((current) => ({ ...current, [changeIntent.id]: 'message' })); }}>{t('work.taskAssistant.message')}</button>
        <button type="button" aria-pressed={queuing} onClick={() => setEntryMode('new_task')}>{t('assistant.project.newTask')}</button>
      </div> : null}
      {queuing ? <p className="product-empty-copy">{t('assistant.project.queueBoundary')}</p> : null}
      <ConversationChangeContext active={!queuing && activeTask} intent={queuing ? null : changeIntent} target={queuing ? null : changeTarget}
        recordedChange={recordedChange} busy={blocked} onBegin={beginChange} t={t} />
      {editableIntent && !queuing ? (
        <div className="team-manager-suggestions" aria-label={t('work.manager.intent.question')}>
          {!onQueueTask ? <button type="button" aria-pressed={!changing} onClick={() => setModes((current) => ({ ...current, [changeIntent.id]: 'message' }))}>
            {t('work.taskAssistant.message')}
          </button> : null}
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
        <span className="sr-only">{t(queuing ? 'assistant.project.newTask' : changing ? 'work.manager.changeReason' : 'work.taskAssistant.message')}</span>
        <textarea
          ref={inputRef}
          aria-keyshortcuts="Enter"
          onKeyDown={(event) => {
            if (!shouldSubmitConversationOnEnter(event)) return;
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }}
          placeholder={t(changing ? 'work.manager.intent.question' : 'work.taskAssistant.placeholder')}
          rows="3"
          required
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
        />
      </label>
      {error || submitError ? <ProductError error={error || submitError} t={t} /> : null}
      <Button busy={blocked} disabled={blocked || !goal.trim()} type="submit">
        {t(queuing ? 'assistant.project.saveTask' : changing ? 'work.manager.intent.reviewInterpretation' : 'work.taskAssistant.send')}
      </Button>
    </form>
  );
}
