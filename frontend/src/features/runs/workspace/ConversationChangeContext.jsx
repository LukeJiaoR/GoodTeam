import Button from '../../../components/ui/Button';
import { changeContextStatus } from './conversationChangeContext';
import './conversationChangeContext.css';

export default function ConversationChangeContext({ active, intent, target, recordedChange, busy, onBegin, t }) {
  if (!active && !intent) return null;
  const status = changeContextStatus(intent);
  return (
    <div className="team-change-context" role="note" aria-label={t('work.changeContext.label')} data-state={status}>
      {!intent ? <details className="team-change-context-help">
        <summary>{t(`work.changeContext.${status}.title`)}</summary>
        <p>{t(`work.changeContext.${status}.body`)}</p>
        {active && !target ? <small>{t('work.changeContext.noBoundary')}</small> : null}
      </details> : <><strong>{t(`work.changeContext.${status}.title`)}</strong>
        <p>{t(`work.changeContext.${status}.body`)}</p></>}
      {!intent && recordedChange?.event === 'change_request_committed'
        && recordedChange.details?.intent_status === 'committed' ? (
          <small>{t('work.changeContext.committed.body')}</small>
        ) : null}
      {active && !intent ? target ? (
        <Button type="button" variant="secondary" disabled={busy} busy={busy} onClick={onBegin}>
          {t('work.changeContext.begin')}
        </Button>
      ) : null : null}
    </div>
  );
}
