import { CircleHelp, PencilLine } from 'lucide-react';
import Button from '../../../components/ui/Button';
import { changeContextStatus } from './conversationChangeContext';
import './conversationChangeContext.css';

export default function ConversationChangeContext({ active, intent, target, recordedChange, busy, onBegin, t }) {
  if (!active && !intent) return null;
  const status = changeContextStatus(intent);
  return (
    <div className="team-change-context" role="note" aria-label={t('work.changeContext.label')} data-state={status}>
      {!intent ? <details className="team-change-context-help">
        <summary><CircleHelp size={16} aria-hidden="true" /><span className="sr-only">{t(`work.changeContext.${status}.title`)}</span></summary>
        <div className="team-change-context-help__body">
        <p>{t(`work.changeContext.${status}.body`)}</p>
        {active && !target ? <small>{t('work.changeContext.noBoundary')}</small> : null}
      </div>
      </details> : <><strong>{t(`work.changeContext.${status}.title`)}</strong>
        <p>{t(`work.changeContext.${status}.body`)}</p></>}
      {!intent && recordedChange?.event === 'change_request_committed'
        && recordedChange.details?.intent_status === 'committed' ? (
          <small>{t('work.changeContext.committed.body')}</small>
        ) : null}
      {active && !intent ? target ? (
        <Button type="button" variant="secondary" disabled={busy} busy={busy} onClick={onBegin} title={t('work.changeContext.begin')}>
          <PencilLine size={16} aria-hidden="true" /><span className="sr-only">{t('work.changeContext.begin')}</span>
        </Button>
      ) : null : null}
    </div>
  );
}
