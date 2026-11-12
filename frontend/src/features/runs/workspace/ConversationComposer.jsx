import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp } from 'lucide-react';
import Button from '../../../components/ui/Button';
import ProductError from '../../../components/ui/ProductError';
import { isChangeDiscussion, sendConversationInput } from './conversationRouting';
import { shouldSubmitConversationOnEnter } from './conversationFeedback';
import ConversationChangeContext from './ConversationChangeContext.jsx';
import { matchesSavedChange } from './conversationChangeContext';
import { AttachmentList, AttachmentPicker } from './ConversationAttachments';
import { useConversationAttachments } from './useConversationAttachments';

function Composer({
  draftKey, projectKey, acceptedMessage, awaitingReply = false,
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
  const attachments = useConversationAttachments({ projectKey, draftKey, acceptedMessage, t });
  const hasAttachments = attachments.files.length > 0;
  const canAttach = Boolean(projectKey && !queuing && !changing);
  const setGoal = (value) => setDrafts((current) => ({ ...current, [scopeKey]: value }));

  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, [scopeKey, changeIntent?.status]);

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const nextHeight = Math.min(Math.max(el.scrollHeight, 52), 180);
    el.style.height = `${nextHeight}px`;
  }, [goal, scopeKey]);

  const submit = async (event) => {
    event.preventDefault();
    if (blocked || submitting.current || attachments.pending || (!goal.trim() && !hasAttachments)) return;
    // Restored files can outlive the tab-local Message mode selection.
    // Neither change discussion nor queued tasks accepts attachments.
    if (hasAttachments && (changing || queuing)) {
      setSubmitError(new Error(t('work.attachments.messageOnly')));
      return;
    }
    submitting.current = true;
    setSending(true);
    setSubmitError(null);
    const original = goal;
    const sent = goal.trim() ? goal : t('work.attachments.message');
    const sentFiles = attachments.files;
    try {
      const result = queuing ? await onQueueTask(sent) : await sendConversationInput({
        text: sent,
        attachments: sentFiles,
        intent: changing ? changeIntent : null,
        onStart,
        onDiscuss: onDiscussChangeIntent,
        onKeepDiscussing: onKeepDiscussingChangeIntent,
      });
      if (result !== false) {
        attachments.clear(sentFiles);
        clearDeliveredDraft(scopeKey, original);
        setDrafts((current) => (current[scopeKey] ?? seed) === original
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
      <div className="conversation-entry-context">
      {queuing ? <p className="product-empty-copy">{t('assistant.project.queueBoundary')}</p> : null}
      {changeIntent ? <ConversationChangeContext active={!queuing && activeTask} intent={queuing ? null : changeIntent} target={queuing ? null : changeTarget}
        recordedChange={recordedChange} busy={blocked} onBegin={beginChange} t={t} /> : null}
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
      <div className="conversation-editor" onDragOver={event => {
        if (canAttach && !blocked && !event.currentTarget.closest('fieldset')?.disabled && event.dataTransfer.types.includes('Files')) event.preventDefault();
      }} onDrop={event => {
        if (!event.dataTransfer.files.length) return;
        event.preventDefault();
        if (canAttach && !blocked && !event.currentTarget.closest('fieldset')?.disabled) attachments.add(event.dataTransfer.files);
      }}>
      <AttachmentList files={attachments.files} onRemove={attachments.remove} onRetry={attachments.retry} disabled={blocked} t={t} />
      <label>
        <span className="sr-only">{t(queuing ? 'assistant.project.newTask' : changing ? 'work.manager.changeReason' : 'work.taskAssistant.message')}</span>
        <textarea
          ref={inputRef}
          name="conversation-message"
          autoComplete="off"
          aria-keyshortcuts="Enter"
          onKeyDown={(event) => {
            if (!shouldSubmitConversationOnEnter(event)) return;
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }}
          placeholder={t(changing ? 'work.manager.intent.question' : 'work.taskAssistant.placeholder')}
          rows="3"
          required={!hasAttachments}
          value={goal}
          onChange={(event) => setGoal(event.target.value)}
          onPaste={event => {
            if (canAttach && !blocked && !event.currentTarget.closest('fieldset')?.disabled && event.clipboardData.files.length) {
              event.preventDefault(); attachments.add(event.clipboardData.files);
            }
          }}
        />
      </label>
      {error || submitError ? <ProductError error={error || submitError} t={t} /> : null}
      {attachments.error ? <p className="conversation-attachment-error" role="alert">{attachments.error}</p> : null}
      <div className="conversation-submit-row">
      <div className="conversation-tools">
      {projectKey ? <AttachmentPicker disabled={blocked || !canAttach} onAdd={attachments.add} t={t} /> : null}
      {onQueueTask || (editableIntent && !queuing) ? (
        <div className="project-composer-modes" aria-label={t('assistant.project.inputMode')}>
          <button type="button" disabled={blocked} aria-pressed={!queuing && !changing} onClick={() => {
            setEntryMode('message');
            if (changeIntent?.id) setModes((current) => ({ ...current, [changeIntent.id]: 'message' }));
          }}>{t('work.taskAssistant.message')}</button>
          {onQueueTask ? <button type="button" disabled={blocked || hasAttachments} aria-pressed={queuing} onClick={() => setEntryMode('new_task')}>
            {t('assistant.project.newTask')}
          </button> : null}
          {editableIntent && !queuing ? <button type="button" disabled={blocked || hasAttachments} aria-pressed={changing} onClick={() => {
            setEntryMode('message');
            setModes((current) => ({ ...current, [changeIntent.id]: 'changes' }));
          }}>{t('work.manager.requestChanges')}</button> : null}
        </div>
      ) : null}
        {!changeIntent && !queuing ? <ConversationChangeContext active={activeTask} target={changeTarget}
          recordedChange={recordedChange} busy={blocked || hasAttachments} onBegin={beginChange} t={t} /> : null}
      </div>
      <Button busy={sending && !awaitingReply} disabled={blocked || attachments.pending || (!goal.trim() && !hasAttachments)} type="submit" data-send-icon={!queuing && !changing}
        title={t('work.composer.keyboardHint')}>
        {!queuing && !changing ? <ArrowUp size={20} aria-hidden="true" /> : null}
        <span className={!queuing && !changing ? 'sr-only' : undefined}>{t(awaitingReply && !queuing && !changing ? 'work.composer.awaitingReply' : queuing ? 'assistant.project.saveTask' : changing ? 'work.manager.intent.reviewInterpretation' : 'work.taskAssistant.send')}</span>
      </Button>
      </div>
      </div>
    </form>
  );
}

// The caller owns the draft scope: project chat stays mounted across task
// contexts, while changing projects restores that project's tab-local draft.
export default function ConversationComposer(props) {
  return <Composer key={props.draftKey || 'transient'} {...props} />;
}
