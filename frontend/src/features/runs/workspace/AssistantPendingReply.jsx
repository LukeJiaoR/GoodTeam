/**
 * [INPUT]: 接收请求状态、停止回调和翻译函数；依赖 React 的状态与计时器。
 * [OUTPUT]: 导出 AssistantPendingReply，展示回复进度、文本和停止操作。
 * [POS]: 项目会话中的待回复项；ProjectConversation 控制其生命周期。
 * [PROTOCOL]: Update this header on change, then check AGENTS.md
 */
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
  const activity = typeof request.activity === 'string' ? request.activity : '';
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
