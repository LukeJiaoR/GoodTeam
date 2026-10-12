import WorkspacePanel from '../../../components/ui/WorkspacePanel';
import TaskInputContext from './TaskInputContext';
import DeliverySummary from './DeliverySummary';
import './attemptInspector.css';

const words = {
  en: {
    title: 'Attempt details', close: 'Close attempt details', current: 'Current task · read-only', historical: 'Historical task · read-only',
    project: 'Project', run: 'Task run', attempt: 'Selected attempt', assignment: 'Task assignment', role: 'Role',
    assignmentState: 'Assignment state', attemptState: 'Saved attempt status', assignmentUpdated: 'Assignment record updated',
    attemptUpdated: 'Attempt record updated', adapter: 'Runtime adapter', missing: 'Not recorded', unbound: 'No task assignment recorded',
    statusNote: 'Assignment state and the saved attempt status describe different records.',
    activity: 'Recorded activity', summary: 'Saved job summary', heartbeat: 'Last recorded heartbeat',
    heartbeatNote: 'A heartbeat is a liveness record, not progress or completion.',
    noJob: 'Matching job details are absent from this snapshot. The saved attempt record is still shown above.',
    input: 'Task-start baseline', inputNote: 'This run-level record is the task-start baseline, not the full input to this attempt.',
    memory: 'Attempt memory', memoryInput: 'Memory input', memoryRead: 'Memory read evidence', memoryWrite: 'Memory writeback',
    unknown: 'Unknown', memoryNote: 'The available data does not establish memory input, reading, or writeback for this exact attempt.',
    delivery: 'Delivery and review for this run', deliveryNote: 'These versioned records belong to the same run. They do not establish what this attempt produced or reviewed.',
    noDelivery: 'No matching run-level delivery record is available.', identity: 'More record identity', actor: 'Assignment record ID',
    worker: 'Worker ID', execution: 'Execution ID', profile: 'Profile ID', retry: 'Recorded retry ordinal',
    artifact: 'Recorded artifact key', version: 'Recorded artifact version', original: 'Original failed attempt',
    unavailable: 'Attempt details unavailable',
    reasons: {
      scope: 'The selected record does not match this project and task run.',
      changed: 'The assignment’s current attempt changed. Close this panel and select it again.',
      ambiguous: 'The selected attempt cannot be matched to one saved record.',
      missing: 'No matching saved attempt is available for this assignment.',
      stale: 'The task view is out of date. Refresh it before inspecting an attempt.',
    },
  },
  zh: {
    title: '执行尝试详情', close: '关闭执行尝试详情', current: '当前任务 · 只读', historical: '历史任务 · 只读',
    project: '项目', run: '任务运行', attempt: '所选执行尝试', assignment: '任务分工', role: '角色',
    assignmentState: '分工状态', attemptState: '已保存的尝试状态', assignmentUpdated: '分工记录更新时间',
    attemptUpdated: '尝试记录更新时间', adapter: '执行适配器', missing: '未记录', unbound: '未记录任务分工',
    statusNote: '分工状态与已保存的尝试状态来自不同记录。',
    activity: '已记录的活动', summary: '已保存的作业摘要', heartbeat: '最后记录的心跳',
    heartbeatNote: '心跳仅表示存活记录，不代表进度或完成。',
    noJob: '本次快照没有匹配的作业详情，上方仍显示已保存的尝试记录。',
    input: '任务启动基线', inputNote: '这是运行级的任务启动记录，不是本次尝试的完整输入。',
    memory: '本次尝试的记忆', memoryInput: '记忆输入', memoryRead: '记忆读取依据', memoryWrite: '记忆回写',
    unknown: '未知', memoryNote: '现有数据无法确认此执行尝试的记忆输入、读取或回写。',
    delivery: '本次运行的交付与审查', deliveryNote: '这些有版本的记录属于同一次运行，不能据此归因于所选尝试的产出或审查。',
    noDelivery: '没有匹配的运行级交付记录。', identity: '更多记录标识', actor: '分工记录 ID',
    worker: 'Worker ID', execution: '执行 ID', profile: '配置 ID', retry: '记录的重试序号',
    artifact: '记录的材料标识', version: '记录的材料版本', original: '原失败尝试',
    unavailable: '执行尝试详情不可用',
    reasons: {
      scope: '所选记录与当前项目和任务运行不匹配。',
      changed: '此分工的当前尝试已变化，请关闭面板后重新选择。',
      ambiguous: '无法将所选尝试唯一匹配到一条已保存的记录。',
      missing: '此分工没有匹配的已保存尝试。',
      stale: '任务视图已过期，请刷新后再查看执行尝试。',
    },
  },
};

// Render only the public, bounded fields named here, never a raw job payload.
function text(value) {
  return typeof value === 'string' && value.trim() ? value : '';
}

function label(t, key, fallback) {
  const translated = t?.(key);
  return translated && translated !== key ? translated : fallback;
}

function Fact({ name, children }) {
  return <div><dt>{name}</dt><dd>{children}</dd></div>;
}

function RecordedTime({ value, missing }) {
  const recorded = text(value);
  if (!recorded) return missing;
  return Number.isFinite(Date.parse(recorded)) ? <time dateTime={recorded}>{recorded}</time> : recorded;
}

function AttemptRecord({ inspection, projectKey, runId, historical, language, t, w }) {
  const { actor, attempt, job, summary } = inspection;
  const role = label(t, `work.plan.role.${actor.role}`, text(actor.role) || w.missing);
  const assignmentState = label(t, `work.team.status.${actor.state}`, text(actor.state) || w.missing);
  const runSummary = summary?.project_key === projectKey && summary?.run_id === runId ? summary : null;
  const jobSummary = text(job?.summary);
  const identity = [
    [w.actor, actor.id], [w.worker, attempt.worker_id], [w.execution, attempt.execution_id],
    [w.profile, attempt.profile_id], [w.retry, attempt.retry_attempt], [w.original, attempt.original_failed_job_id],
    [w.artifact, attempt.artifact_key], [w.version, attempt.artifact_version],
  ].filter(([, value]) => text(value));

  return <div className="attempt-inspector">
    <p className="attempt-inspector__scope">{historical ? w.historical : w.current}</p>
    <dl className="attempt-inspector__facts attempt-inspector__scope-facts">
      <Fact name={w.project}><code>{projectKey}</code></Fact>
      <Fact name={w.run}><code>{runId}</code></Fact>
      <Fact name={w.attempt}><code>{attempt.id}</code></Fact>
    </dl>
    <section className="attempt-inspector__section" aria-label={w.assignment}>
      <h3>{w.assignment}</h3>
      {text(actor.responsibility) ? <p className="attempt-inspector__responsibility">{actor.responsibility}</p> : null}
      <dl className="attempt-inspector__facts">
        <Fact name={w.assignment}>{text(actor.task_id) ? <code>{actor.task_id}</code> : w.unbound}</Fact>
        <Fact name={w.role}>{role}</Fact>
        <Fact name={w.assignmentState}>{assignmentState}</Fact>
        <Fact name={w.attemptState}>{text(attempt.status) || w.missing}</Fact>
        <Fact name={w.assignmentUpdated}><RecordedTime value={actor.updated_at} missing={w.missing} /></Fact>
        <Fact name={w.attemptUpdated}><RecordedTime value={attempt.updated_at} missing={w.missing} /></Fact>
        <Fact name={w.adapter}>{text(attempt.adapter) || w.missing}</Fact>
      </dl>
      <p className="attempt-inspector__note">{w.statusNote}</p>
      <details className="attempt-inspector__identity"><summary>{w.identity}</summary>
        <dl className="attempt-inspector__facts">{identity.map(([name, value]) => <Fact key={name} name={name}><code>{value}</code></Fact>)}</dl>
      </details>
    </section>
    <section className="attempt-inspector__section" aria-label={w.activity}>
      <h3>{w.activity}</h3>
      {job ? <>
        <dl className="attempt-inspector__facts">
          <Fact name={w.summary}>{jobSummary ? <span className="attempt-inspector__summary">{jobSummary.slice(0, 2000)}{jobSummary.length > 2000 ? '…' : ''}</span> : w.missing}</Fact>
          <Fact name={w.heartbeat}><RecordedTime value={job.heartbeat_at} missing={w.missing} /></Fact>
        </dl>
        <p className="attempt-inspector__note">{w.heartbeatNote}</p>
      </> : <p className="attempt-inspector__note">{w.noJob}</p>}
    </section>
    <section className="attempt-inspector__section" aria-label={w.input}>
      <h3>{w.input}</h3><p className="attempt-inspector__note">{w.inputNote}</p>
      <TaskInputContext projectKey={projectKey} runId={runId} language={language} t={t} />
    </section>
    <section className="attempt-inspector__section" aria-label={w.memory}>
      <h3>{w.memory}</h3>
      <dl className="attempt-inspector__facts">{[w.memoryInput, w.memoryRead, w.memoryWrite].map(name => <Fact key={name} name={name}>{w.unknown}</Fact>)}</dl>
      <p className="attempt-inspector__note">{w.memoryNote}</p>
    </section>
    <section className="attempt-inspector__section" aria-label={w.delivery}>
      <h3>{w.delivery}</h3><p className="attempt-inspector__note">{w.deliveryNote}</p>
      {runSummary ? <DeliverySummary summary={runSummary} historical={historical} language={language} t={t} /> : <p>{w.noDelivery}</p>}
    </section>
  </div>;
}

export default function AttemptInspector({ inspection, projectKey, runId, historical = false, language, t, onClose }) {
  const w = words[String(language || '').startsWith('zh') ? 'zh' : 'en'];
  const available = inspection?.available === true && Boolean(projectKey && runId && inspection.actor && text(inspection.attempt?.id));
  return <WorkspacePanel title={w.title} closeLabel={w.close} onClose={onClose}>
    {available ? <AttemptRecord
      key={JSON.stringify([projectKey, runId, inspection.actor.id, inspection.attempt.id])}
      inspection={inspection} projectKey={projectKey} runId={runId} historical={historical} language={language} t={t} w={w}
    /> : <section className="attempt-inspector attempt-inspector__unavailable" aria-label={w.unavailable}>
      <h3>{w.unavailable}</h3>
      <p role="status">{Object.hasOwn(w.reasons, inspection?.reason) ? w.reasons[inspection.reason] : w.reasons.missing}</p>
    </section>}
  </WorkspacePanel>;
}
