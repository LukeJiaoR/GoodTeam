import { useEffect, useState } from 'react';

export default function AssistantPendingReply({ request, t }) {
  const [observedAt] = useState(() => Date.now());
  const [now, setNow] = useState(observedAt);
  const timestamp = Date.parse(request.updated_at || '');
  const elapsed = Math.max(0, Math.floor((now - (Number.isFinite(timestamp) ? timestamp : observedAt)) / 1000));
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <li className="team-manager-event assistant-pending-reply" data-actor="assistant">
      <div className="team-manager-event__bubble">
        <strong className="sr-only">{t('work.manager.name')}</strong>
        <div className="assistant-pending-reply__status" role="status">
          <span className="assistant-pending-reply__dots" aria-hidden="true"><i /><i /><i /></span>
          <span>{t('assistant.project.responding')}</span>
          <small className="assistant-pending-reply__timer" aria-live="off">{t('assistant.wait.elapsed', { seconds: elapsed })}</small>
        </div>
        {elapsed >= 30 ? <p className="assistant-pending-reply__long">{t('assistant.wait.long')}</p> : null}
      </div>
    </li>
  );
}
