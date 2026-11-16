import { useEffect, useRef, useState } from 'react';

export default function AssistantPendingReply({ request, onStop, t }) {
  const [observedAt] = useState(() => Date.now());
  const [now, setNow] = useState(observedAt);
  const [stopping, setStopping] = useState(false);
  const stopPending = useRef(false);
  const timestamp = Date.parse(request.started_at || request.updated_at || '');
  const elapsed = Math.max(0, Math.floor((now - (Number.isFinite(timestamp) ? timestamp : observedAt)) / 1000));
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const stop = async () => {
    if (stopPending.current || !onStop) return;
    stopPending.current = true;
    setStopping(true);
    try { await onStop(); } finally {
      stopPending.current = false;
      setStopping(false);
    }
  };
  const activity = typeof request.activity === 'string' ? request.activity
    : request.activity ? `${request.activity.kind}: ${request.activity.status}` : '';
  return (
    <li className="team-manager-event assistant-pending-reply" data-actor="assistant">
      <div className="team-manager-event__bubble">
        <strong className="sr-only">{t('work.manager.name')}</strong>
        <div className="assistant-pending-reply__status" role="status">
          <span className="assistant-pending-reply__dots" aria-hidden="true"><i /><i /><i /></span>
          <span>{t('assistant.project.responding')}</span>
          <small className="assistant-pending-reply__timer" aria-live="off">{t('assistant.wait.elapsed', { seconds: elapsed })}</small>
        </div>
        {request.stream_text ? <div className="assistant-pending-reply__text" style={{ whiteSpace: 'pre-wrap' }}>{request.stream_text}</div> : null}
        {activity ? <p className="assistant-pending-reply__activity" role="status">{activity}</p> : null}
        {elapsed >= 30 && !request.stream_text ? <p className="assistant-pending-reply__long">{t('assistant.wait.long')}</p> : null}
        {onStop ? <button type="button" onClick={stop} disabled={stopping || request.stop_requested}>
          {t('assistant.project.stopResponse')}
        </button> : null}
      </div>
    </li>
  );
}

