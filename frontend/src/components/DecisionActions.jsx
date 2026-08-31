import { useState } from 'react';
import Button from './ui/Button';

const SNIPPET_KEYS = [
  'review.snippet.approve',
  'review.snippet.tests',
  'review.snippet.refine',
];

export default function DecisionActions({ busy = '', error, onDecision, onRequestChanges, t }) {
  const [note, setNote] = useState('');

  const handleKeyDown = (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      onDecision('approved', note);
    }
  };

  const insertSnippet = (key) => {
    const text = t(key);
    if (!text) return;
    setNote((current) => (current ? `${current}；${text}` : text));
  };

  return (
    <form className="review-decision-panel" onSubmit={(event) => event.preventDefault()}>
      <div className="review-decision-snippets">
        {SNIPPET_KEYS.map((key) => {
          const label = t(key);
          return (
            <button
              type="button"
              key={key}
              className="review-snippet-pill"
              onClick={() => insertSnippet(key)}
            >
              {label}
            </button>
          );
        })}
      </div>
      <label>
        <span>{t('review.decision.rationale')}</span>
        <textarea
          rows="3"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          onKeyDown={handleKeyDown}
        />
      </label>
      <Button
        busy={busy === 'approved'}
        onClick={() => onDecision('approved', note)}
      >
        {t('review.decision.approve')}
        <span className="product-kbd-hint" aria-hidden="true">⌘↵</span>
      </Button>
      <Button
        variant="secondary"
        busy={busy === 'changes_requested'}
        disabled={!onRequestChanges && !note.trim()}
        onClick={() => (
          onRequestChanges ? onRequestChanges() : onDecision('changes_requested', note)
        )}
      >
        {t('review.decision.changes')}
      </Button>
      <Button
        variant="danger"
        busy={busy === 'rejected'}
        disabled={!note.trim()}
        onClick={() => onDecision('rejected', note)}
      >
        {t('review.decision.reject')}
      </Button>
      <p>{t('review.decision.authority')}</p>
      {error ? <p className="workflow-canvas-error" role="alert">{error.message || String(error)}</p> : null}
    </form>
  );
}
