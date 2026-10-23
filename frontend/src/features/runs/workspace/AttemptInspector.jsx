import WorkspacePanel from '../../../components/ui/WorkspacePanel';
import TaskInputContext from './TaskInputContext.jsx';
import DeliverySummary from './DeliverySummary';
import { resolveAttemptMemory } from './attemptMemory';
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
    unknown: 'Unknown', memoryNote: 'Host records do not establish provider receipt or reading. Writeback records do not establish learning or task success.',
    captured: 'Pickup packet captured; preparation not recorded', prepared: 'Host input preparations', writebacks: 'Recorded writeback observations',
    capturedAt: 'Pickup captured', transport: 'Host call entries', noTransport: 'Not recorded', receiptDetails: 'Memory observation details',
    preparation: 'Input preparation', resultMemory: 'Result memory', auditMemory: 'Execution audit', observedAt: 'Observed',
    contextStatus: 'Prepared memory context', citations: 'Referenced events', sources: 'Rendered sources', inputDigest: 'Prepared input digest',
    fragmentDigest: 'Memory fragment digest', resultDigest: 'Result digest', outcome: 'Recorded outcome', appended: 'New WAL events',
    replayed: 'Same-source replays', deduplicated: 'Other-source duplicates', unattempted: 'Unprocessed items',
    lowerBounds: 'Counts are lower bounds because part of the write outcome is unknown.',
    omittedPreparations: 'Earlier input preparations omitted from this view', omittedWrites: 'Earlier writeback observations omitted from this view',
    inputScope: 'Preparation describes the host-rendered input, not a provider read. Event references and summary sources are different counts.',
    captureChanged: 'Prepared packet differs from the captured packet.',
    memoryReasons: { not_recorded: 'No retained observations are available for this attempt.', unsupported_attempt: 'This attempt uses a separate input protocol; ordinary Worker memory observations are unknown.',
      scope_mismatch: 'Memory observations do not match this exact project, run, and attempt.', records_unavailable: 'Retained memory observations could not be verified.' },
    memoryStatuses: { available: 'Available', partial: 'Partial', unavailable: 'Unavailable', unknown: 'Unknown',
      completed: 'Completed', skipped: 'Skipped', known_not_committed: 'No commit observed' },
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
    unknown: '未知', memoryNote: '主机记录不能证明模型已接收或读取。回写记录也不能证明已学习或任务成功。',
    captured: '已捕获记忆包；未记录输入准备', prepared: '主机输入准备记录', writebacks: '已记录的回写观测',
    capturedAt: '记忆包捕获时间', transport: '主机调用入口记录', noTransport: '未记录', receiptDetails: '记忆观测详情',
    preparation: '输入准备', resultMemory: '结果记忆', auditMemory: '执行审计', observedAt: '观测时间',
    contextStatus: '已准备的记忆上下文', citations: '引用事件数', sources: '已渲染来源数', inputDigest: '已准备输入摘要',
    fragmentDigest: '记忆片段摘要', resultDigest: '结果摘要', outcome: '已记录的结果', appended: '新增 WAL 事件',
    replayed: '同源重放', deduplicated: '其他来源的重复项', unattempted: '未处理项',
    lowerBounds: '部分写入结果未知，所示计数仅为已知下限。',
    omittedPreparations: '此视图省略的较早输入准备记录', omittedWrites: '此视图省略的较早回写观测',
    inputScope: '输入准备描述主机渲染内容，不能证明模型已读取。事件引用与摘要来源是不同计数。',
    captureChanged: '已准备的记忆包与捕获时不同。',
    memoryReasons: { not_recorded: '此尝试没有可用的留存观测记录。', unsupported_attempt: '此尝试采用独立输入协议，普通 Worker 的记忆观测未知。',
      scope_mismatch: '记忆观测与当前项目、运行及尝试不匹配。', records_unavailable: '无法验证留存的记忆观测。' },
    memoryStatuses: { available: '可用', partial: '部分可用', unavailable: '不可用', unknown: '未知',
      completed: '已完成', skipped: '已跳过', known_not_committed: '未观测到提交' },
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

function AttemptMemory({ inspection, projectKey, runId, w }) {
  const memory = resolveAttemptMemory({ inspection, projectKey, runId });
  const observed = memory.status === 'observed';
  return <section className="attempt-inspector__section" aria-label={w.memory}>
    <h3>{w.memory}</h3>
    <dl className="attempt-inspector__facts">
      <Fact name={w.memoryInput}>{observed ? memory.preparationCount ? `${w.prepared}: ${memory.preparationCount}` : w.captured : w.unknown}</Fact>
      <Fact name={w.memoryRead}>{w.unknown}</Fact>
      <Fact name={w.memoryWrite}>{observed && memory.writebackCount ? `${w.writebacks}: ${memory.writebackCount}` : w.unknown}</Fact>
      {observed ? <>
        <Fact name={w.capturedAt}><RecordedTime value={memory.capture.captured_at} missing={w.missing} /></Fact>
        <Fact name={w.transport}>{memory.transportAttemptCount || w.noTransport}</Fact>
      </> : null}
    </dl>
    <p className="attempt-inspector__note">{w.memoryNote}</p>
    {!observed ? <p className="attempt-inspector__note">{w.memoryReasons[memory.reason]}</p> : null}
    {observed && (memory.preparationCount > 0 || memory.writebackCount > 0) ? <details className="attempt-inspector__identity">
      <summary>{w.receiptDetails}</summary>
      {memory.preparationCount > 0 ? <p className="attempt-inspector__note">{w.inputScope}</p> : null}
      {memory.omittedPreparations > 0 ? <p>{w.omittedPreparations}: {memory.omittedPreparations}</p> : null}
      {memory.preparations.map(item => <section key={item.id} className="attempt-inspector__section" aria-label={w.preparation}>
        <h4>{w.preparation}</h4>
        <dl className="attempt-inspector__facts">
          <Fact name={w.observedAt}><RecordedTime value={item.prepared_at} missing={w.missing} /></Fact>
          <Fact name={w.contextStatus}>{w.memoryStatuses[item.context_status]}</Fact>
          <Fact name={w.citations}>{item.citation_count}</Fact>
          <Fact name={w.sources}>{item.rendered_source_count}</Fact>
          <Fact name={w.inputDigest}><code>{item.input_digest}</code></Fact>
          <Fact name={w.fragmentDigest}><code>{item.fragment_digest}</code></Fact>
        </dl>
        {!item.capture_packet_match ? <p className="attempt-inspector__note">{w.captureChanged}</p> : null}
      </section>)}
      {memory.omittedWritebacks > 0 ? <p>{w.omittedWrites}: {memory.omittedWritebacks}</p> : null}
      {memory.writebacks.map(item => <section key={item.id} className="attempt-inspector__section" aria-label={item.kind === 'result' ? w.resultMemory : w.auditMemory}>
        <h4>{item.kind === 'result' ? w.resultMemory : w.auditMemory}</h4>
        <dl className="attempt-inspector__facts">
          <Fact name={w.observedAt}><RecordedTime value={item.observed_at} missing={w.missing} /></Fact>
          <Fact name={w.outcome}>{w.memoryStatuses[item.outcome]}</Fact>
          <Fact name={w.appended}>{item.appended}</Fact>
          <Fact name={w.replayed}>{item.replayed}</Fact>
          <Fact name={w.deduplicated}>{item.deduplicated}</Fact>
          <Fact name={w.unattempted}>{item.unattempted}</Fact>
          <Fact name={w.resultDigest}><code>{item.result_digest}</code></Fact>
        </dl>
        {!item.counts_complete ? <p className="attempt-inspector__note">{w.lowerBounds}</p> : null}
      </section>)}
    </details> : null}
  </section>;
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
    <AttemptMemory inspection={inspection} projectKey={projectKey} runId={runId} w={w} />
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
