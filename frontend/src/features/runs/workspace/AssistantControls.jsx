import { useState } from 'react';
import { Check, Plug, RotateCcw, Settings, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import Button from '../../../components/ui/Button';
import { routes } from '../../../lib/routing/routes';
import TeamDecisionPreview from './TeamDecisionPreview';
import './assistantControls.css';

function RuntimeControls({ onConfigureRuntime, runtime, busy, onAction, projectKey, t }) {
  const [consent, setConsent] = useState(false);
  if (!runtime) return null;
  return (
    <section className="assistant-runtime" aria-label={t('assistant.runtime')}>
      <details className="assistant-runtime-details" open={!['verified', 'simulated'].includes(runtime.status)}>
        <summary>{t('assistant.runtime')} · {t(`assistant.status.${runtime.status}`)}</summary>
        <div className="assistant-runtime-details__body">
          <strong>{runtime.provider} / {runtime.model || 'builtin'} / {runtime.reasoning_depth}</strong>
          {runtime.error ? <p>{t(`assistant.error.${runtime.error.code}`)}</p> : null}
          <label><input type="checkbox" checked={consent} disabled={Boolean(busy)} onChange={(event) => setConsent(event.target.checked)} />{t('assistant.costConsent')}</label>
          <Button disabled={!consent || Boolean(busy)} onClick={() => {
            setConsent(false);
            onAction?.({ action: 'check_runtime', confirmed: true, message_id: globalThis.crypto?.randomUUID?.() || `check-${Date.now()}` });
          }}><Plug size={14} aria-hidden="true" />{t('assistant.check')}</Button>
        </div>
      </details>
      {onConfigureRuntime ? <Button variant="secondary" disabled={Boolean(busy)} onClick={onConfigureRuntime}><Settings size={14} aria-hidden="true" />{t('assistant.configure')}</Button> : <Link to={routes.projectTeam(projectKey)}><Settings size={14} aria-hidden="true" />{t('assistant.configure')}</Link>}
    </section>
  );
}

function RequestRecovery({ requests = {}, busy, onAction, t }) {
  return Object.entries(requests).filter(([, request]) => !['succeeded', 'running'].includes(request.status)).map(([id, request]) => (
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
  const previewAvailable = pending.preview?.schema_version === 'team_decision_preview.v1'
    && pending.preview.team_revision === pending.expected_team_revision;
  return (
    <section className="assistant-team-decision" aria-label={t('assistant.teamProposal')}>
      <strong>{t(pending.status === 'pending' ? 'assistant.teamProposal' : 'assistant.teamStale')}</strong>
      <p>{pending.reason}</p>
      {previewAvailable ? <TeamDecisionPreview preview={pending.preview} commands={pending.commands || []} t={t} />
        : <p>{t('assistant.preview.unavailable')}</p>}
      <div className="assistant-control-actions">
        {pending.status === 'pending' ? <Button disabled={Boolean(busy) || !previewAvailable} onClick={() => onAction?.({ action: 'confirm_team_decision', proposal_id: pending.id })}>
          <Check size={14} aria-hidden="true" />{t('assistant.confirmTeam')}
        </Button> : null}
        <Button variant="secondary" disabled={Boolean(busy)} onClick={() => onAction?.({ action: 'dismiss_team_decision', proposal_id: pending.id })}>
          <X size={14} aria-hidden="true" />{t('assistant.dismissTeam')}
        </Button>
      </div>
    </section>
  );
}

export default function AssistantControls({ showRuntime = true, onConfigureRuntime, runtime, requests, pending, busy, readOnly, onAction, projectKey, t }) {
  if (readOnly) return null;
  const locked = busy || Object.values(requests || {}).some((request) => request.status === 'running') || runtime?.status === 'checking';
  return <div className="assistant-controls">
    <RequestRecovery requests={requests} busy={locked} onAction={onAction} t={t} />
    <TeamDecision pending={pending} busy={locked} onAction={onAction} t={t} />
    {showRuntime ? <RuntimeControls onConfigureRuntime={onConfigureRuntime} runtime={runtime} busy={locked} onAction={onAction} projectKey={projectKey} t={t} /> : null}
  </div>;
}
