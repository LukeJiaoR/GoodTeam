import { Check, RotateCcw, X } from 'lucide-react';
import Button from '../../../components/ui/Button';
import './assistantControls.css';

function RequestRecovery({ requests = {}, busy, onAction, t }) {
  return Object.entries(requests).filter(([, request]) => request.status !== 'succeeded').map(([id, request]) => (
    <section className="assistant-request" key={id} aria-label={t('assistant.request')}>
      <strong role="status">{t(`assistant.status.${request.status}`)}</strong>
      <p>{request.text}</p>
      {request.error ? <p>{t(`assistant.error.${request.status === 'interrupted' ? 'interrupted' : request.error.code}`)}</p> : null}
      {request.status === 'failed' ? <Button disabled={Boolean(busy)} onClick={() => onAction?.({
        action: 'retry_message', message_id: id, expected_attempt: request.attempt,
      })}><RotateCcw size={14} aria-hidden="true" />{t('assistant.retry')}</Button> : null}
    </section>
  ));
}

function TeamDecision({ pending, busy, onAction, t }) {
  if (!pending) return null;
  return (
    <section className="assistant-team-decision" aria-label={t('assistant.teamProposal')}>
      <strong>{t(pending.status === 'pending' ? 'assistant.teamProposal' : 'assistant.teamStale')}</strong>
      <p>{pending.reason}</p>
      <ul>{(pending.commands || []).map((command, index) => <li key={index}>
        <strong>{t(`assistant.command.${command.kind}`)}</strong>
        <dl>{Object.entries(command).filter(([key]) => key !== 'kind').map(([key, value]) => (
          <div key={key}><dt>{key}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>
        ))}</dl>
      </li>)}</ul>
      <div className="assistant-control-actions">
        {pending.status === 'pending' ? <Button disabled={Boolean(busy)} onClick={() => onAction?.({ action: 'confirm_team_decision', proposal_id: pending.id })}>
          <Check size={14} aria-hidden="true" />{t('assistant.confirmTeam')}
        </Button> : null}
        <Button variant="secondary" disabled={Boolean(busy)} onClick={() => onAction?.({ action: 'dismiss_team_decision', proposal_id: pending.id })}>
          <X size={14} aria-hidden="true" />{t('assistant.dismissTeam')}
        </Button>
      </div>
    </section>
  );
}

export default function AssistantControls({ runtime, requests, pending, busy, readOnly, onAction, t }) {
  if (readOnly || (!pending && !Object.values(requests || {}).some((request) => request.status !== 'succeeded'))) return null;
  const locked = busy || Object.values(requests || {}).some((request) => request.status === 'running') || runtime?.status === 'checking';
  return <div className="assistant-controls">
    <RequestRecovery requests={requests} busy={locked} onAction={onAction} t={t} />
    <TeamDecision pending={pending} busy={locked} onAction={onAction} t={t} />
  </div>;
}
