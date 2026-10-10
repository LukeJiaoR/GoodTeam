import { useRef, useState } from 'react';
import Button from '../../../components/ui/Button';
import { workApprovalContext } from './workApprovalContext';

function BoundWorkApprovalAction({ busy, context, gate, onDecide, t }) {
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const inFlight = useRef(false);
  const disabled = Boolean(busy) || pending || typeof onDecide !== 'function';
  const canSubmit = !disabled && (!context.requiresReason || Boolean(note.trim()));

  const submit = async () => {
    if (!canSubmit || inFlight.current) return;
    // Acquire synchronously; React rendering and parent busy propagation can lag.
    inFlight.current = true;
    setPending(true);
    setFailed(false);
    try {
      await onDecide(gate, 'approved', note.trim(), context.target);
    } catch {
      // Do not discard the operator's reason after a rejected/stale request.
      setFailed(true);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };

  return (
    <div className="team-attention__confirm">
      {context.requiresReason ? (
        <>
          <p>{t('work.informedApproval.explanation')}</p>
          <label>
            <span>{t('work.informedApproval.reason')}</span>
            <textarea
              rows="3"
              required
              disabled={disabled}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                  event.preventDefault();
                  void submit();
                }
              }}
            />
          </label>
        </>
      ) : null}
      <Button busy={pending || busy === 'decision'} disabled={!canSubmit} onClick={() => { void submit(); }}>
        {t(context.requiresReason ? 'work.informedApproval.continue' : 'work.manager.approvalBrief.approve')}
      </Button>
      {failed ? <p role="alert">{t('work.informedApproval.failed')}</p> : null}
    </div>
  );
}

export default function WorkApprovalAction({ brief, busy, gate, onDecide, projectKey, t }) {
  const context = workApprovalContext({ brief, gate, projectKey });
  if (!context) return <p role="note">{t('work.informedApproval.targetMissing')}</p>;
  return (
    <BoundWorkApprovalAction
      key={context.key}
      busy={busy}
      context={context}
      gate={gate}
      onDecide={onDecide}
      t={t}
    />
  );
}
