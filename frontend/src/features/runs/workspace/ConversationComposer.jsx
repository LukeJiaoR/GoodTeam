import { useEffect, useRef, useState } from 'react';
import Button from '../../../components/ui/Button';
import ProductError from '../../../components/ui/ProductError';
import { isChangeDiscussion, sendConversationInput } from './conversationRouting';
import { shouldSubmitConversationOnEnter } from './conversationFeedback';
import ConversationChangeContext from './ConversationChangeContext.jsx';
import { matchesSavedChange } from './conversationChangeContext';

function Composer({
  draftKey, acceptedMessage, awaitingReply = false,
  busy, embedded = true, error, onStart, onQueueTask, suggestions = [], t,
  changeIntent, onDiscussChangeIntent, onKeepDiscussingChangeIntent,
  activeTask = false, changeTarget = null, recordedChange, onStartChangeIntent,
}) {
  const [entryMode, setEntryMode] = useState('message');
  const storageKey = draftKey ? `goodteam:conversation-draft:${draftKey}` : '';
  const [drafts, setDrafts] = useState(() => {
    try {
      const saved = storageKey ? JSON.parse(sessionStorage.getItem(storageKey)) : null;
      return saved && typeof saved === 'object' && !Array.isArray(saved)
        ? Object.fromEntries(Object.entries(saved).filter(([, value]) => typeof value === 'string'))
        : {};
    } catch { return {}; }
  });
  useEffect(() => {
    if (!storageKey) return;
    try {
      const unsent = Object.fromEntries(Object.entries(drafts).filter(([, value]) => value.length));
      if (Object.keys(unsent).length) sessionStorage.setItem(storageKey, JSON.stringify(unsent));
      else sessionStorage.removeItem(storageKey);
    } catch { /* The composer stays usable when browser storage is unavailable. */ }
  }, [drafts, storageKey]);
  const clearDeliveredDraft = (scope, sent) => {
    // A request may complete after the user navigates away. Clear the saved
    // text only if it still matches this request, preserving any newer edit.
    if (!storageKey) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      if (!saved || saved[scope] !== sent) return;
      delete saved[scope];
      if (Object.keys(saved).length) sessionStorage.setItem(storageKey, JSON.stringify(saved));
      else sessionStorage.removeItem(storageKey);
    } catch { /* Storage availability must not change delivery semantics. */ }
  };
  const acceptedId = useRef(null);
  useEffect(() => {
    if (!acceptedMessage?.id || acceptedId.current === acceptedMessage.id) return;
    acceptedId.current = acceptedMessage.id;
    setDrafts(current => current.message === acceptedMessage.text
      ? { ...current, message: '' } : current);
  }, [acceptedMessage]);
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
        clearDeliveredDraft(scopeKey, sent);
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
      clearDeliveredDraft(scopeKey, sent);
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
      {onQueueTask || (editableIntent && !queuing) ? (
        <div className="project-composer-modes" aria-label={t('assistant.project.inputMode')}>
          <button type="button" aria-pressed={!queuing && !changing} onClick={() => {
            setEntryMode('message');
            if (changeIntent?.id) setModes((current) => ({ ...current, [changeIntent.id]: 'message' }));
          }}>{t('work.taskAssistant.message')}</button>
          {onQueueTask ? <button type="button" aria-pressed={queuing} onClick={() => setEntryMode('new_task')}>
            {t('assistant.project.newTask')}
          </button> : null}
          {editableIntent && !queuing ? <button type="button" aria-pressed={changing} onClick={() => {
            setEntryMode('message');
            setModes((current) => ({ ...current, [changeIntent.id]: 'changes' }));
          }}>{t('work.manager.requestChanges')}</button> : null}
        </div>
      ) : null}
      <div className="conversation-entry-context">
      {queuing ? <p className="product-empty-copy">{t('assistant.project.queueBoundary')}</p> : null}
      <ConversationChangeContext active={!queuing && activeTask} intent={queuing ? null : changeIntent} target={queuing ? null : changeTarget}
        recordedChange={recordedChange} busy={blocked} onBegin={beginChange} t={t} />
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
      </div>
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
      <div className="conversation-submit-row">
      <small className="conversation-keyboard-hint">{t('work.composer.keyboardHint')}</small>
      <Button busy={sending && !awaitingReply} disabled={blocked || !goal.trim()} type="submit">
        {t(awaitingReply && !queuing && !changing ? 'work.composer.awaitingReply' : queuing ? 'assistant.project.saveTask' : changing ? 'work.manager.intent.reviewInterpretation' : 'work.taskAssistant.send')}
      </Button>
      </div>
    </form>
  );
}

// The caller owns the draft scope: project chat stays mounted across task
// contexts, while changing projects restores that project's tab-local draft.
export default function ConversationComposer(props) {
  return <Composer key={props.draftKey || 'transient'} {...props} />;
}
