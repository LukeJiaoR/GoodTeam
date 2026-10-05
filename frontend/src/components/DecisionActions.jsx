import { useRef, useState } from 'react';
import Button from './ui/Button';
import { canConfirmApproval } from '../lib/reviews/approvalConfirmation';

const SNIPPET_KEYS = [
  'review.snippet.approve',
  'review.snippet.tests',
  'review.snippet.refine',
];

function BoundDecisionActions({ approveDisabled = false, busy = '', error, informedApproval = false, onDecision, onRequestChanges, t }) {
  const [note, setNote] = useState('');
  const [pending, setPending] = useState('');
  const [failed, setFailed] = useState(false);
  const inFlight = useRef(false);
  const disabled = Boolean(busy || pending);
  const canApprove = canConfirmApproval({
    disabled: disabled || approveDisabled || typeof onDecision !== 'function',
    requiresReason: informedApproval,
    note,
  });

  const submit = async (decision) => {
    if (disabled || inFlight.current) return;
    if (decision === 'approved' && !canApprove) return;
    if (decision === 'rejected' && !note.trim()) return;
    if (decision === 'changes_requested' && !onRequestChanges && !note.trim()) return;
    const operation = decision === 'changes_requested' && onRequestChanges
      ? () => onRequestChanges(note)
      : () => onDecision?.(decision, note);
    // Lock before invoking the callback; parent busy propagation can lag.
    inFlight.current = true;
    setPending(decision);
    setFailed(false);
    try {
      await operation();
    } catch {
      setFailed(true);
    } finally {
      inFlight.current = false;
      setPending('');
    }
  };

  const handleKeyDown = (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      void submit('approved');
    }
  };

  const insertSnippet = (key) => {
    const text = t(key);
    if (!text) return;
    setNote((current) => (current ? `${current}；${text}` : text));
  };

  return (
    <form className="review-decision-panel" onSubmit={(event) => event.preventDefault()}>
      {informedApproval ? <p>{t('work.informedApproval.explanation')}</p> : (
        <div className="review-decision-snippets">
          {SNIPPET_KEYS.map((key) => {
            const label = t(key);
            return (
              <button type="button" key={key} className="review-snippet-pill" disabled={disabled} onClick={() => insertSnippet(key)}>
                {label}
              </button>
            );
          })}
        </div>
      )}
      <label>
        <span>{t(informedApproval ? 'work.informedApproval.reason' : 'review.decision.rationale')}</span>
        <textarea rows="3" value={note} disabled={disabled} onChange={(event) => setNote(event.target.value)} onKeyDown={handleKeyDown} />
      </label>
      <Button busy={(pending || busy) === 'approved'} disabled={!canApprove} onClick={() => { void submit('approved'); }}>
        {t(informedApproval ? 'work.informedApproval.continue' : 'review.decision.approve')}
        <span className="product-kbd-hint" aria-hidden="true">⌘↵</span>
      </Button>
      <Button variant="secondary" busy={(pending || busy) === 'changes_requested'} disabled={disabled || (!onRequestChanges && !note.trim())} onClick={() => { void submit('changes_requested'); }}>
        {t('review.decision.changes')}
      </Button>
      <Button variant="danger" busy={(pending || busy) === 'rejected'} disabled={disabled || !note.trim()} onClick={() => { void submit('rejected'); }}>
        {t('review.decision.reject')}
      </Button>
      <p>{t('review.decision.authority')}</p>
      {error ? <p className="workflow-canvas-error" role="alert">{error.message || String(error)}</p>
        : failed ? <p role="alert">{t('work.informedApproval.failed')}</p> : null}
    </form>
  );
}

export default function DecisionActions({ decisionContextKey = '', ...props }) {
  return <BoundDecisionActions key={decisionContextKey} {...props} />;
}
