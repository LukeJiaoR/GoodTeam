import { useRef, useState } from 'react';

export default function ContinuationScopeAddition({ value, snapshot, onAction, disabled, zh }) {
  const [command] = useState(() => globalThis.crypto?.randomUUID?.() || `scope-${Date.now()}`);
  const submitted = useRef({ identity: '', revision: 0 });
  const [selected, setSelected] = useState([]);
  const [confirmed, setConfirmed] = useState('');
  const tasks = (snapshot?.candidates || []).filter(task => task.status === 'todo'
    && !task.run_id && !(value.task_ids || []).includes(task.task_id));
  const ids = tasks.filter(task => selected.includes(task.task_id)).map(task => task.task_id);
  const input = { action: 'add_project_continuation_tasks', continuation_id: value.id,
    expected_backlog_version: snapshot?.version, task_ids: ids, confirmed: true, billing_acknowledged: true };
  const identity = JSON.stringify(input);
  const ready = ids.length > 0 && ids.length + (value.task_ids || []).length <= 128 && snapshot?.version && !value.workflow_recovery
    && !value.planning_backlog_version && !value.discovery_pending
    && value.batches_started < value.max_batches && value.manager_calls < value.max_manager_calls;
  if (!tasks.length) return null;
  return <fieldset disabled={disabled}>
    <legend>{zh ? '确认加入后续任务' : 'Confirm additional later tasks'}</legend>
    <p>{zh ? '先选择任务，再明确确认加入当前授权。保留当前执行，不增加剩余批次、模型调用额度或其他能力；仍需原检查与交付验收。'
      : 'Select and explicitly confirm later tasks for this grant. Current work, remaining batch/model allowances and other capabilities stay unchanged. Original checks and delivery acceptance still apply.'}</p>
    {tasks.map(task => <label key={task.task_id} style={{ display: 'block' }}>
      <input type="checkbox" checked={ids.includes(task.task_id)} onChange={event => setSelected(current =>
        event.target.checked ? [...current, task.task_id] : current.filter(id => id !== task.task_id))} />
      {task.task_id}: {task.title} ({task.role}, {task.execution_policy})
      {task.write_domains?.length ? ` · ${task.write_domains.join(', ')}` : ''}
      <span style={{ display: 'block' }}>{task.description}</span>
      <span style={{ display: 'block' }}>{task.acceptance_criteria?.join('; ')}</span>
    </label>)}
    <label><input type="checkbox" checked={confirmed === identity}
      onChange={event => setConfirmed(event.target.checked ? identity : '')} />
      {zh ? '我确认上述任务按其当前范围与验收要求加入本次授权，并承担现有额度内的模型费用。'
        : 'I confirm these tasks with their current scope and acceptance requirements, and model charges within the existing allowance.'}</label>
    <button type="button" disabled={disabled || !ready || confirmed !== identity}
      onClick={() => {
        if (submitted.current.identity !== identity) submitted.current = { identity, revision: submitted.current.revision + 1 };
        onAction({ ...input, idempotency_key: `${command}:${submitted.current.revision}` });
      }}>
      {zh ? '确认加入当前授权' : 'Confirm additions to current grant'}
    </button>
  </fieldset>;
}
