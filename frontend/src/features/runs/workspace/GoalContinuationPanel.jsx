import { useRef, useState } from 'react';
import Button from '../../../components/ui/Button';
import ProductError from '../../../components/ui/ProductError';
import StatusBadge from '../../../components/ui/StatusBadge';
import { statusText, tone } from './presentationUtils';
import './goalContinuation.css';

const TERMINAL = new Set(['complete', 'stopped', 'canceled']);
const lines = (value) => value.split('\n').map((line) => line.trim()).filter(Boolean);
const label = (value, t) => {
  const key = `work.goal.status.${value}`;
  const translated = t(key);
  return translated === key ? statusText(value, t) : translated;
};

function GoalForm({ busy, onAction, onClose, t }) {
  const [objective, setObjective] = useState('');
  const [criteria, setCriteria] = useState('');
  const [scope, setScope] = useState('');
  const [maxUnits, setMaxUnits] = useState('1');
  const [maxAssessments, setMaxAssessments] = useState('3');
  const [billing, setBilling] = useState(false);
  const [requestKey] = useState(() => globalThis.crypto.randomUUID());
  const [submitting, setSubmitting] = useState(false);
  const pending = useRef(false);
  const valid = objective.trim() && lines(criteria).length && lines(scope).length
    && Number.isInteger(Number(maxUnits)) && Number(maxUnits) >= 1 && Number(maxUnits) <= 10
    && Number.isInteger(Number(maxAssessments)) && Number(maxAssessments) >= 1 && Number(maxAssessments) <= 3 && billing;
  const submit = async (event) => {
    event.preventDefault();
    if (!valid || busy || pending.current || !onAction) return;
    pending.current = true;
    setSubmitting(true);
    try {
      const result = await onAction({
        action: 'create_goal', objective: objective.trim(), acceptance_criteria: lines(criteria),
        allowed_scope: lines(scope), max_work_units: Number(maxUnits), max_assessment_attempts: Number(maxAssessments), confirmed: true,
        billing_acknowledged: billing, idempotency_key: requestKey,
      });
      if (result && result !== false) onClose();
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  };
  return <form className="goal-continuation-form" onSubmit={submit}>
    <fieldset disabled={Boolean(busy || submitting)}>
      <legend>{t('work.goal.new')}</legend>
      <label>{t('work.goal.objective')}<textarea required rows={2} value={objective} onChange={event => setObjective(event.target.value)} /></label>
      <label>{t('work.goal.criteria')}<textarea required rows={3} value={criteria} onChange={event => setCriteria(event.target.value)} /></label>
      <label>{t('work.goal.scope')}<textarea required rows={2} value={scope} onChange={event => setScope(event.target.value)} /></label>
      <label>{t('work.goal.maxUnits')}<input type="number" min={1} max={10} step={1} required value={maxUnits} onChange={event => setMaxUnits(event.target.value)} /></label>
      <label>{t('work.goal.maxAssessments')}<input type="number" min={1} max={3} step={1} required value={maxAssessments} onChange={event => setMaxAssessments(event.target.value)} /></label>
      <p>{t('work.goal.assessmentLimitHelp')}</p>
      <p>{t('work.goal.authorization')}</p>
      <p>{t('work.goal.costWarning')}</p>
      <label className="goal-continuation-checkbox"><input type="checkbox" checked={billing} onChange={event => setBilling(event.target.checked)} />{t('work.goal.billingConsent')}</label>
      <div className="goal-continuation-actions">
        <Button type="submit" disabled={!valid || !onAction} busy={submitting}>{t('work.goal.confirm')}</Button>
        <Button variant="secondary" onClick={onClose}>{t('work.goal.dismiss')}</Button>
      </div>
    </fieldset>
  </form>;
}

function GoalSnapshot({ goal, busy, readOnly, onAction, t }) {
  const pending = useRef(false);
  const requestKeys = useRef(new Map());
  const [submitting, setSubmitting] = useState('');
  const [retryConsent, setRetryConsent] = useState(false);
  const gate = goal.gate;
  const work = (goal.work_units || []).at(-1);
  const executionStatus = work?.observed_execution_status || work?.execution_status;
  const terminal = TERMINAL.has(goal.status);
  const bindingValid = Boolean(goal.id && Number.isInteger(goal.revision) && goal.revision >= 1);
  const act = async (request) => {
    if (!onAction || busy || readOnly || pending.current || !bindingValid) return;
    const identity = JSON.stringify([goal.id, goal.revision, request]);
    if (!requestKeys.current.has(identity)) requestKeys.current.set(identity, globalThis.crypto.randomUUID());
    pending.current = true;
    setSubmitting(request.decision || request.status || request.action);
    try {
      await onAction({ ...request, goal_id: goal.id, expected_revision: goal.revision,
        idempotency_key: requestKeys.current.get(identity) });
    } finally {
      pending.current = false;
      setSubmitting('');
    }
  };
  const disabled = Boolean(busy || submitting || readOnly || !bindingValid || !onAction);
  const budgetGate = goal.status === 'awaiting_approval' && gate?.kind === 'budget'
    && gate?.status === 'pending' && gate?.revision === goal.revision && Boolean(gate.id)
    && gate.proposed_max_work_units === goal.max_work_units + 1 && gate.proposed_max_work_units <= 10;
  const retryTarget = goal.assessment_retry_target;
  const canRetry = goal.status === 'blocked' && retryTarget?.work_id === work?.id
    && retryTarget?.run_id === work?.run_id && retryTarget?.attempt < 3 && Boolean(retryTarget?.job_id);
  return <article className="goal-continuation-snapshot" aria-label={t('work.goal.latest')}>
    <header><strong>{goal.objective}</strong><StatusBadge tone={tone(goal.status)}>{label(goal.status, t)}</StatusBadge></header>
    <p className="goal-continuation-meta">{t('work.goal.progress', { used: (goal.work_units || []).filter(unit => unit.started_at).length, max: goal.max_work_units })} · {t('work.goal.revision', { revision: goal.revision })}</p>
    <details><summary>{t('work.goal.details')}</summary>
      <h3>{t('work.goal.criteria')}</h3><ol>{(goal.acceptance_criteria || []).map((criterion, index) => <li key={index}>{criterion}</li>)}</ol>
      <h3>{t('work.goal.scope')}</h3><ul>{(goal.allowed_scope || []).map((constraint, index) => <li key={index}>{constraint}</li>)}</ul>
    </details>
    {work ? <section className="goal-continuation-work" aria-label={t('work.goal.currentWork')}>
      <h3>{t('work.goal.currentWork')}</h3>
      <p>{work.task}</p>
      {executionStatus ? <p>{t('work.goal.executionStatus', { status: label(executionStatus, t) })}</p>
        : work.status ? <p>{label(work.status, t)}</p> : null}
      {work.run_id ? <p className="goal-continuation-meta">{t('work.goal.run', { runId: work.run_id })}</p> : null}
      {work.rationale ? <p>{work.rationale}</p> : null}
      {work.dependencies?.length ? <p className="goal-continuation-meta">{t('work.goal.dependencies', { ids: work.dependencies.join(', ') })}</p> : null}
      {work.priority ? <p className="goal-continuation-meta">{t('work.goal.priority', { priority: work.priority })}</p> : null}
      {work.criterion_indices?.length ? <p>{t('work.goal.criteriaCovered', { indices: work.criterion_indices.map(index => index + 1).join(', ') })}</p> : null}
      {(work.evidence_refs?.length || work.source_evidence_refs?.length || work.assessment_checks?.length) ? <details>
        <summary>{t('work.goal.evidence')}</summary>
        <ul>{(work.evidence_refs?.length ? work.evidence_refs : work.source_evidence_refs || []).map((ref, index) => <li key={index}>{ref}</li>)}</ul>
        {work.assessment_checks?.length ? <section className="goal-continuation-assessment" aria-label={t('work.goal.assessment')}>
          <h3>{t('work.goal.assessment')}</h3>
          <p>{t('work.goal.assessmentCaution')}</p>
          <ol>{work.assessment_checks.map(check => <li key={check.criterion}>
            <strong>{goal.acceptance_criteria?.[check.criterion] || t('work.goal.criterionNumber', { number: check.criterion + 1 })}</strong>
            <p>{t(`work.goal.assessmentStatus.${check.status}`)}</p>
            <blockquote>{check.quote}</blockquote>
            <p className="goal-continuation-meta">{check.evidence_ref}</p>
          </li>)}</ol>
        </section> : null}
      </details> : null}
    </section> : <p>{t('work.goal.waiting')}</p>}
    {goal.stop_reason ? <p role="status">{goal.stop_reason}</p> : null}
    {gate ? <section className="goal-continuation-gate" aria-label={t('work.goal.gate')}>
      <h3>{t(budgetGate ? 'work.goal.budgetGate' : 'work.goal.gate')}</h3>
      <p>{gate.rationale}</p>
      <p className="goal-continuation-meta">{label(gate.status, t)}</p>
      {budgetGate ? <>
        <p>{t('work.goal.oneUnit')}</p>
        {gate.criterion_indices?.length ? <ol>{gate.criterion_indices.map(index => <li key={index}>{goal.acceptance_criteria[index]}</li>)}</ol> : null}
        {gate.evidence_refs?.length ? <details><summary>{t('work.goal.evidence')}</summary><ul>{gate.evidence_refs.map((ref, index) => <li key={index}>{ref}</li>)}</ul></details> : null}
        <p>{t('work.goal.costWarning')}</p>
        <div className="goal-continuation-actions">
          <Button disabled={disabled} busy={submitting === 'approve'} onClick={() => act({ action: 'decide_goal_gate', gate_id: gate.id, decision: 'approve' })}>{t('work.goal.approveUnit')}</Button>
          <Button variant="secondary" disabled={disabled} busy={submitting === 'reject'} onClick={() => act({ action: 'decide_goal_gate', gate_id: gate.id, decision: 'reject' })}>{t('work.goal.rejectUnit')}</Button>
        </div>
      </> : null}
    </section> : null}
    {canRetry && !readOnly ? <section className="goal-continuation-gate" aria-label={t('work.goal.retryAssessment')}>
      <p>{t('work.goal.retryAssessmentHelp', { attempt: retryTarget.attempt + 1 })}</p>
      <label className="goal-continuation-checkbox"><input type="checkbox" disabled={disabled} checked={retryConsent} onChange={event => setRetryConsent(event.target.checked)} />{t('work.goal.retryConsent')}</label>
      <Button disabled={disabled || !retryConsent} busy={submitting === 'retry_goal_assessment'} onClick={() => {
        setRetryConsent(false);
        return act({ action: 'retry_goal_assessment', work_id: work.id, run_id: work.run_id,
          evidence_digest: retryTarget.evidence_digest, failed_job_id: retryTarget.job_id, confirmed: true });
      }}>{t('work.goal.retryAssessment')}</Button>
    </section> : null}
    {!terminal && !readOnly ? <div className="goal-continuation-actions">
      <Button variant="secondary" disabled={disabled} busy={submitting === 'stopped'} onClick={() => act({ action: 'stop_goal', status: 'stopped' })}>{t('work.goal.stop')}</Button>
      <Button variant="secondary" disabled={disabled} busy={submitting === 'canceled'} onClick={() => act({ action: 'stop_goal', status: 'canceled' })}>{t('work.goal.cancel')}</Button>
      <p>{t('work.goal.stopHelp')}</p>
    </div> : null}
  </article>;
}

export default function GoalContinuationPanel({ goals = [], busy, readOnly = false, activeRun = false, error, onAction, t }) {
  const [creating, setCreating] = useState(false);
  const latest = goals.at(-1);
  const activeGoal = goals.some(goal => !TERMINAL.has(goal.status));
  return <section className="goal-continuation" aria-label={t('work.goal.title')}>
    <header className="goal-continuation-heading"><h2>{t('work.goal.title')}</h2>
      {!readOnly && !creating && !activeGoal ? <Button variant="secondary" disabled={Boolean(busy || activeRun || !onAction)} onClick={() => setCreating(true)}>{t('work.goal.new')}</Button> : null}
    </header>
    {activeRun && !activeGoal && !readOnly ? <p>{t('work.goal.waitForRun')}</p> : null}
    {latest ? <GoalSnapshot key={latest.id} goal={latest} busy={busy} readOnly={readOnly} onAction={onAction} t={t} /> : <p>{t('work.goal.optional')}</p>}
    {creating && !readOnly && !activeGoal ? <GoalForm busy={busy || activeRun} onAction={onAction} onClose={() => setCreating(false)} t={t} /> : null}
    {error ? <ProductError error={error} t={t} /> : null}
  </section>;
}
