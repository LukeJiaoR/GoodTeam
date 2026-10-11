import Button from '../../../components/ui/Button';

export default function WorkflowRetryAction({ retry, busy, onRetry, t }) {
  const target = retry?.target;
  return (
    <div className="work-retry-action">
      {target ? <>
        <p>{t(`work.retry.mode.${target.mode}`)}</p>
        <p>{t('work.retry.run', { runId: target.run_id })}</p>
        <p>{t('work.retry.attemptEvidence')}</p>
        {target.attempts.length ? <ul>{target.attempts.map(attempt => <li key={attempt.id}>
          <code>{attempt.id}</code> · {t('work.retry.attempt', {
            role: attempt.role,
            attempt: attempt.attempt === '' || attempt.attempt == null ? t('work.retry.countUnknown') : attempt.attempt,
            retryAttempt: attempt.retry_attempt === '' || attempt.retry_attempt == null ? t('work.retry.countUnknown') : attempt.retry_attempt,
          })}
        </li>)}</ul> : <p>{t('work.retry.noAttempts')}</p>}
      </> : null}
      {retry?.reason ? <p>{t(`work.retry.${retry.reason}`)}</p> : null}
      {!target && !retry?.reason ? <p>{t('work.retry.bindingMissing')}</p> : null}
      <Button busy={busy} disabled={!target || retry?.disabled || !onRetry} onClick={onRetry}>
        {t(target?.mode === 'resume_transition' ? 'work.retry.resume' : 'work.retry.retryStage')}
      </Button>
    </div>
  );
}
