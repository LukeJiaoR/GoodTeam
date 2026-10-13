import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, FileText } from 'lucide-react';
import { runApi } from '../runApi';
import SkillReviewExecutions from './SkillReviewExecutions';
import { validTaskInputContext } from './taskInputContext.js';
import './taskInputContext.css';

const words = {
  en: {
    title: 'Task inputs and methods', close: 'Close task inputs and methods', loading: 'Loading the task record…',
    failed: 'The task record could not be verified. Its input source is unknown.', retry: 'Retry loading task inputs',
    locked: 'Task-start record', legacy: 'Workflow record · unverified', unavailable: 'Input source unknown',
    lockedNote: 'These inputs and method versions were captured for this task at its start. This record does not prove that a model received, read, or followed them.',
    legacyNote: 'Only the saved workflow record is available. The original task-start input and method versions could not be verified.',
    unavailableNote: 'The original task record is unavailable. Its inputs and methods cannot be established from this response.',
    request: 'Recorded request', source: 'Project source', mode: 'Delivery mode', standards: 'Delivery standards',
    missing: 'Not recorded', methods: 'Methods at task start', noSubjects: 'No subject method records are available for this task.',
    noMethods: 'No method version is recorded for this subject.', method: 'Method', version: 'Version', digest: 'Package digest',
    required: 'Required', on_demand: 'On demand', default: 'Default selection', custom: 'Custom selection',
    diagnostic: 'Diagnostics at capture time', diagnosticNote: 'These diagnostics describe the saved capture, not current permission to run.',
    methodsNote: 'Recorded versions do not establish execution or resource reading. Use the bound execution evidence below to inspect what was observed.',
    provenance: 'Record identity and source', run: 'Task run', project: 'Project', captured: 'Captured at', revision: 'Team revision at capture', lock: 'Task lock',
    errors: { snapshot_missing: 'The saved task capture is missing.', snapshot_scope_mismatch: 'The saved capture does not match this project, task, or project root.', task_context_missing: 'The task has a registered capture, but its task context is missing.', task_start_input_missing: 'The original task-start input is missing.' },
    idea: 'New project', new_project: 'New project', existing_project: 'Existing project',
  },
  zh: {
    title: '本次输入与方法', close: '关闭本次输入与方法', loading: '正在读取本次任务记录…',
    failed: '无法核对本次任务记录，输入来源未知。', retry: '重试读取本次输入',
    locked: '任务启动时的记录', legacy: '工作流记录 · 未验证', unavailable: '输入来源未知',
    lockedNote: '这些输入与方法版本在本次任务启动时已捕获。此记录不证明模型已收到、读取或遵循它们。',
    legacyNote: '目前只能读取已保存的工作流记录，无法验证原始启动输入与方法版本。',
    unavailableNote: '原始任务记录不可用，无法从本次响应确认输入与方法。',
    request: '记录的需求', source: '项目来源', mode: '交付模式', standards: '交付标准',
    missing: '未记录', methods: '启动时的方法', noSubjects: '本次任务没有可用的角色方法记录。',
    noMethods: '此角色未记录方法版本。', method: '方法', version: '版本', digest: '方法包摘要',
    required: '必需', on_demand: '按需', default: '默认选择', custom: '自定义选择',
    diagnostic: '捕获时的诊断', diagnosticNote: '这些诊断描述保存时的状态，不代表当前运行许可。',
    methodsNote: '记录中的版本不能证明已执行或读取资源。下方已绑定的执行依据可查看实际观察到的情况。',
    provenance: '记录身份与来源', run: '任务运行', project: '项目', captured: '捕获时间', revision: '捕获时的 Team 修订', lock: '任务锁',
    errors: { snapshot_missing: '已保存的任务捕获记录缺失。', snapshot_scope_mismatch: '保存的记录与当前项目、任务或项目目录不匹配。', task_context_missing: '本任务已有登记记录，但任务上下文缺失。', task_start_input_missing: '原始任务启动输入缺失。' },
    idea: '新项目', new_project: '新项目', existing_project: '已有项目',
  },
};

function InputRecord({ value, projectKey, runId, language, t, w }) {
  const locked = value.capture_status === 'locked';
  const legacy = value.capture_status === 'legacy_unverified';
  const input = value.input;
  const deliveryKey = `newProject.deliveryModes.${input?.delivery_mode}`;
  const delivery = t?.(deliveryKey);
  return <>
    <p className="task-input-context__source"><strong>{locked ? w.locked : legacy ? w.legacy : w.unavailable}</strong></p>
    <p>{locked ? w.lockedNote : legacy ? w.legacyNote : w.unavailableNote}</p>
    {!locked && !legacy && Object.hasOwn(w.errors, value.error_code) ? <p>{w.errors[value.error_code]}</p> : null}
    {input ? <>
      <h3>{w.request}</h3>
      <pre className="task-input-context__request">{input.exact_request || w.missing}</pre>
      <dl className="task-input-context__facts">
        <div><dt>{w.source}</dt><dd>{['idea', 'new_project', 'existing_project'].includes(input.source_type) ? w[input.source_type] : input.source_type || w.missing}</dd></div>
        <div><dt>{w.mode}</dt><dd>{delivery && delivery !== deliveryKey ? delivery : input.delivery_mode || w.missing}</dd></div>
      </dl>
      <h3>{w.standards}</h3>
      {input.delivery_standards.length ? <ul>{input.delivery_standards.map((standard, index) => <li key={index}>{standard}</li>)}</ul> : <p>{w.missing}</p>}
    </> : null}
    <h3>{w.methods}</h3>
    <p>{w.methodsNote}</p>
    {!value.subjects.length ? <p>{w.noSubjects}</p> : value.subjects.map(subject => {
      const roleKey = `work.stage.${subject.subject.replace(/^role:/, '')}`;
      const role = t?.(roleKey);
      return <details className="task-input-context__subject" key={subject.subject}>
        <summary>{role && role !== roleKey ? role : subject.subject} · {w[subject.mode]} · {subject.methods.length}</summary>
        <code>{subject.subject}</code>
        {subject.methods.length ? subject.methods.map(method => <dl className="task-input-context__method" key={method.skill_id}>
          <div><dt>{w.method}</dt><dd><code>{method.skill_id}</code> · {w[method.activation]}</dd></div>
          <div><dt>{w.version}</dt><dd><code>{method.version_id}</code></dd></div>
          <div><dt>{w.digest}</dt><dd><code>{method.package_digest}</code></dd></div>
        </dl>) : <p>{w.noMethods}</p>}
        {subject.diagnostics.length ? <div>
          <h4>{w.diagnostic}</h4><p>{w.diagnosticNote}</p>
          <ul>{subject.diagnostics.map((diagnostic, index) => <li key={index}><code>{diagnostic.code}</code></li>)}</ul>
        </div> : null}
      </details>;
    })}
    <details className="task-input-context__provenance"><summary>{w.provenance}</summary>
      <dl>
        <dt>{w.project}</dt><dd><code>{projectKey}</code></dd>
        <dt>{w.run}</dt><dd><code>{runId}</code></dd>
        <dt>{w.captured}</dt><dd>{value.captured_at || w.missing}</dd>
        <dt>{w.revision}</dt><dd>{value.team_revision ?? w.missing}</dd>
        {locked ? <><dt>{w.lock}</dt><dd><code>{value.task_lock_ref.id}</code><code>{value.task_lock_ref.digest}</code></dd></> : null}
      </dl>
    </details>
    <SkillReviewExecutions projectKey={projectKey} runId={runId} language={language} mode="delivery" />
  </>;
}

function TaskInputPanel({ projectKey, runId, language, t }) {
  const w = words[String(language || '').startsWith('zh') ? 'zh' : 'en'];
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(null);
  const [error, setError] = useState(false);
  const controller = useRef(null);
  const triggerRef = useRef(null);
  const panelId = useId();
  useEffect(() => () => controller.current?.abort(), []);
  const load = async () => {
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setValue(null);
    setError(false);
    try {
      const payload = await runApi.getTaskInputContext(projectKey, runId, { signal: request.signal });
      if (request.signal.aborted) return;
      if (!validTaskInputContext(payload, projectKey, runId)) throw new Error('input_context_mismatch');
      setValue(payload);
    } catch {
      if (!request.signal.aborted) setError(true);
    }
  };
  const close = () => {
    controller.current?.abort();
    setOpen(false);
    setValue(null);
    setError(false);
    triggerRef.current?.focus({ preventScroll: true });
  };
  return <div className="task-input-context" onKeyDown={event => {
    if (open && event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); close(); }
  }}>
    <button type="button" className="task-input-context__toggle" ref={triggerRef} aria-expanded={open} aria-controls={panelId}
      onClick={() => { if (open) close(); else { setOpen(true); void load(); } }}>
      <FileText size={15} aria-hidden="true" /><span>{w.title}</span><ChevronDown size={15} aria-hidden="true" />
    </button>
    {open ? <section id={panelId} aria-label={w.title} className="task-input-context__panel">
      <header><h2>{w.title}</h2><button type="button" onClick={close}>{w.close}</button></header>
      <div className="task-input-context__body">
        {error ? <><p role="alert">{w.failed}</p><button type="button" onClick={load}>{w.retry}</button></>
          : !value ? <p role="status">{w.loading}</p> : <InputRecord value={value} projectKey={projectKey} runId={runId} language={language} t={t} w={w} />}
        {value?.capture_status === 'unavailable' ? <button type="button" onClick={load}>{w.retry}</button> : null}
      </div>
    </section> : null}
  </div>;
}

export default function TaskInputContext(props) {
  if (!props.projectKey || !props.runId) return null;
  return <TaskInputPanel key={JSON.stringify([props.projectKey, props.runId])} {...props} />;
}
