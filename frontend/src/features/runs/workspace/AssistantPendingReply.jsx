import { useEffect, useState } from 'react';
import { TeamAvatar } from './ConversationTimeline';

export default function AssistantPendingReply({ request, t }) {
  const [observedAt] = useState(() => Date.now());
  const [now, setNow] = useState(observedAt);
  const timestamp = Date.parse(request.updated_at || '');
  const elapsed = Math.max(0, Math.floor((now - (Number.isFinite(timestamp) ? timestamp : observedAt)) / 1000));
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return <li className="team-manager-event assistant-pending-reply" data-actor="assistant">
    <TeamAvatar label={t('work.manager.name')} manager />
    <div className="team-manager-event__bubble">
      <div className="team-manager-event__meta"><strong>{t('work.manager.name')}</strong>
        <small aria-live="off">{t('assistant.wait.elapsed', { seconds: elapsed })}</small>
      </div>
      <div className="assistant-pending-reply__status" role="status">
        <span className="assistant-pending-reply__dots" aria-hidden="true"><i /><i /><i /></span>
        <span>{t('assistant.project.responding')}</span>
      </div>
      {elapsed >= 30 ? <p>{t('assistant.wait.long')}</p> : null}
    </div>
  </li>;
}
