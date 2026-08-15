import { useState } from 'react';
import Button from './ui/Button';

export default function DecisionActions({ busy = '', error, onDecision, t }) {
  const [note, setNote] = useState('');
  return (
    <form className="review-decision-panel" onSubmit={(event) => event.preventDefault()}>
      <label><span>{t('review.decision.rationale')}</span><textarea rows="3" value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <Button busy={busy === 'approved'} onClick={() => onDecision('approved', note)}>{t('review.decision.approve')}</Button>
      <Button variant="secondary" busy={busy === 'changes_requested'} disabled={!note.trim()} onClick={() => onDecision('changes_requested', note)}>{t('review.decision.changes')}</Button>
      <Button variant="danger" busy={busy === 'rejected'} disabled={!note.trim()} onClick={() => onDecision('rejected', note)}>{t('review.decision.reject')}</Button>
      <p>{t('review.decision.authority')}</p>
      {error ? <p className="workflow-canvas-error" role="alert">{error.message || String(error)}</p> : null}
    </form>
  );
}
