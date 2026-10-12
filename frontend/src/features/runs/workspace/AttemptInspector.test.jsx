import { useState } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { en } from '../../../i18n/locales/en';
import { requestJson } from '../../../lib/api/client';
import AttemptInspector from './AttemptInspector';

// Keep the dialog and all readers real; only the network boundary is replaced.
vi.mock('../../../lib/api/client', () => ({ requestJson: vi.fn() }));
afterEach(() => vi.resetAllMocks());
const t = key => en[key] || key;
const artifact = { key: 'implementation_handoff', version: 'sha256:one',
  href: '/api/delivery-artifact?project=DEMO&run=run&key=implementation_handoff&version=sha256%3Aone' };
const attempt = { id: 'job-1', role: 'implementation', status: 'completed', adapter: 'codex', updated_at: '2026-10-11T10:05:00Z',
  worker_id: 'worker-1', execution_id: 'execution-1', profile_id: 'profile-1', artifact_key: artifact.key, artifact_version: artifact.version };
const actor = { id: 'task-assignment:DEMO-1', role: 'implementation', state: 'working', task_id: 'DEMO-1', responsibility: 'Implement the approved task',
  current_attempt_id: attempt.id, updated_at: '2026-10-11T10:06:00Z' };
const summary = { project_key: 'DEMO', run_id: 'run', status: 'needs_verification', simulated: false, attempts: [attempt], artifacts: [artifact] };
const inspection = { available: true, actor, attempt, summary,
  job: { id: attempt.id, summary: 'Saved work summary', heartbeat_at: '2026-10-11T10:04:00Z' } };
const locked = {
  schema_version: 'goodteam.task-input-context.v1', project_key: 'DEMO', run_id: 'run', task_kind: 'workflow_run', read_only: true,
  capture_status: 'locked', input_origin: 'task_start_lock',
  input: { exact_request: '  Original task input\n  keep indent', source_type: 'existing_project', delivery_mode: 'feature_increment', delivery_standards: ['Stable API'] },
  captured_at: '2026-10-11T10:00:00Z', team_revision: 2,
  task_lock_ref: { id: 'task-1', digest: `sha256:${'a'.repeat(64)}` },
  subjects: [{ subject: 'role:code_review', mode: 'custom', diagnostics: [],
    methods: [{ skill_id: 'review-method', version_id: 'version-1', package_digest: `sha256:${'b'.repeat(64)}`, activation: 'on_demand' }] }],
};
const report = { content: '# Run-level result\nSaved delivery report.', project_key: 'DEMO', run_id: 'run', artifact: artifact.key, version: artifact.version };
const reviews = { schema_version: 'goodteam.task-reviews.v1', project_key: 'DEMO', run_id: 'run', reviews: [], start_available: true };
const inputUrl = '/api/projects/DEMO/runs/run/input-context';
const reviewUrl = '/api/projects/DEMO/runs/run/skill-reviews';
const props = { inspection, projectKey: 'DEMO', runId: 'run', language: 'en', t, onClose: vi.fn() };
const setup = overrides => render(<AttemptInspector {...props} {...overrides} />);
const openInputs = () => fireEvent.click(screen.getByRole('button', { name: 'Task inputs and methods' }));
const deliverySection = () => screen.getByRole('region', { name: 'Delivery and review for this run' });
const openReport = () => fireEvent.click(within(deliverySection()).getByRole('button', { name: /handoff/i }));
const openReviews = () => fireEvent.click(within(deliverySection()).getByRole('button', { name: 'Review evidence for this delivery' }));
const valueFor = (root, name) => within(root).getByText(name, { selector: 'dt' }).nextElementSibling;
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('keeps assignment state, attempt status and actual recorded timestamps separate without fetching', () => {
  setup();
  const assignment = screen.getByRole('region', { name: 'Task assignment' });
  expect(valueFor(assignment, 'Assignment state')).toHaveTextContent(en['work.team.status.working']);
  expect(valueFor(assignment, 'Saved attempt status')).toHaveTextContent('completed');
  expect(valueFor(assignment, 'Assignment record updated').querySelector('time')).toHaveAttribute('datetime', actor.updated_at);
  expect(valueFor(assignment, 'Attempt record updated').querySelector('time')).toHaveAttribute('datetime', attempt.updated_at);
  expect(valueFor(screen.getByRole('dialog'), 'Selected attempt')).toHaveTextContent(attempt.id);
  expect(valueFor(screen.getByRole('dialog'), 'Task run')).toHaveTextContent('run');
  expect(valueFor(screen.getByRole('dialog'), 'Project')).toHaveTextContent('DEMO');
  const activity = screen.getByRole('region', { name: 'Recorded activity' });
  expect(valueFor(activity, 'Last recorded heartbeat').querySelector('time')).toHaveAttribute('datetime', inspection.job.heartbeat_at);
  expect(screen.getByText('A heartbeat is a liveness record, not progress or completion.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Task inputs and methods' })).toHaveAttribute('aria-expanded', 'false');
  expect(requestJson).not.toHaveBeenCalled();
});

test('reuses exact task-start input, versioned run delivery and GET-only review readers on demand', async () => {
  requestJson.mockImplementation(url => Promise.resolve(url === inputUrl ? locked : url === artifact.href ? report : reviews));
  setup(); openInputs();
  const input = await screen.findByText(/Original task input/);
  expect(input.textContent).toBe(locked.input.exact_request);
  expect(screen.getByText('version-1')).toBeInTheDocument();
  expect(screen.getByText('This run-level record is the task-start baseline, not the full input to this attempt.')).toBeInTheDocument();
  openReport(); await screen.findByRole('heading', { name: 'Run-level result' });
  openReviews(); await within(deliverySection()).findByText(/No registered review evidence/);
  fireEvent.click(within(screen.getByRole('region', { name: 'Task inputs and methods' })).getByRole('button', { name: 'Review evidence for this delivery' }));
  await screen.findAllByText(/No registered review evidence/);
  expect(screen.queryByRole('button', { name: /Start review|Retry with locked inputs|Cancel review|Approve|Terminate/ })).not.toBeInTheDocument();
  expect(requestJson.mock.calls.map(([url]) => url)).toEqual([inputUrl, artifact.href, reviewUrl, reviewUrl]);
  for (const [, options] of requestJson.mock.calls) {
    expect(options).toEqual(expect.objectContaining({ cache: 'no-store', signal: expect.any(AbortSignal) }));
    expect(options.method || 'GET').toBe('GET');
  }
  expect(screen.getByText('These versioned records belong to the same run. They do not establish what this attempt produced or reviewed.')).toBeInTheDocument();
});

test('unavailable → available → changed unmounts all readers and rejects their late responses', async () => {
  const inputs = deferred(), delivery = deferred(), review = deferred();
  requestJson.mockImplementation(url => url === inputUrl ? inputs.promise : url === artifact.href ? delivery.promise : review.promise);
  const view = setup({ inspection: { available: false, reason: 'missing' } });
  expect(screen.queryByRole('button', { name: 'Task inputs and methods' })).not.toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
  view.rerender(<AttemptInspector {...props} />);
  openInputs(); openReport(); openReviews();
  const signals = requestJson.mock.calls.map(([, options]) => options.signal);
  expect(signals).toHaveLength(3);
  expect(signals.every(signal => !signal.aborted)).toBe(true);
  view.rerender(<AttemptInspector {...props} inspection={{ available: false, reason: 'changed' }} />);
  expect(signals.every(signal => signal.aborted)).toBe(true);
  expect(screen.getByRole('status')).toHaveTextContent('The assignment’s current attempt changed.');
  expect(screen.getByRole('button', { name: 'Close attempt details' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: 'Task inputs and methods' })).not.toBeInTheDocument();
  await act(async () => { inputs.resolve(locked); delivery.resolve(report); review.resolve(reviews); });
  expect(screen.queryByText(/Original task input|Saved delivery report|No registered review evidence/)).not.toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledTimes(3);
});

test.each(['actor', 'attempt'])('rekeys the readable body when the available %s identity changes', async field => {
  const pending = deferred();
  requestJson.mockReturnValue(pending.promise);
  const view = setup(); openInputs(); openReport();
  const signals = requestJson.mock.calls.map(([, options]) => options.signal);
  const next = { ...inspection, [field]: { ...inspection[field], id: 'next-id' } };
  view.rerender(<AttemptInspector {...props} inspection={next} />);
  expect(signals.every(signal => signal.aborted)).toBe(true);
  expect(screen.getByRole('button', { name: 'Task inputs and methods' })).toHaveAttribute('aria-expanded', 'false');
  await act(async () => pending.resolve(locked));
  expect(screen.queryByText(/Original task input/)).not.toBeInTheDocument();
});

test('a stale snapshot discards already displayed task input and reports, including nested review readers', async () => {
  requestJson.mockImplementation(url => Promise.resolve(url === inputUrl ? locked : report));
  const view = setup(); openInputs(); await screen.findByText(/Original task input/);
  openReport(); await screen.findByRole('heading', { name: 'Run-level result' });
  requestJson.mockReturnValue(new Promise(() => {}));
  fireEvent.click(within(screen.getByRole('region', { name: 'Task inputs and methods' })).getByRole('button', { name: 'Review evidence for this delivery' }));
  const nestedSignal = requestJson.mock.calls.at(-1)[1].signal;
  view.rerender(<AttemptInspector {...props} inspection={{ available: false, reason: 'stale' }} />);
  expect(nestedSignal.aborted).toBe(true);
  expect(screen.queryByText(/Original task input|Saved delivery report/)).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Delivery and review for this run' })).not.toBeInTheDocument();
});

test('historical attempts remain inspectable without job detail or invented timestamps and memory evidence', () => {
  setup({ historical: true, inspection: { ...inspection, actor: { ...actor, updated_at: '' }, attempt: { ...attempt, updated_at: '' }, job: null } });
  expect(screen.getByText('Historical task · read-only')).toBeInTheDocument();
  expect(screen.getByText(en['work.deliverySummary.historical'])).toBeInTheDocument();
  const assignment = screen.getByRole('region', { name: 'Task assignment' });
  expect(valueFor(assignment, 'Saved attempt status')).toHaveTextContent('completed');
  expect(valueFor(assignment, 'Attempt record updated')).toHaveTextContent('Not recorded');
  expect(valueFor(assignment, 'Assignment record updated')).toHaveTextContent('Not recorded');
  expect(screen.getByText('Matching job details are absent from this snapshot. The saved attempt record is still shown above.')).toBeInTheDocument();
  const memory = screen.getByRole('region', { name: 'Attempt memory' });
  for (const name of ['Memory input', 'Memory read evidence', 'Memory writeback']) expect(valueFor(memory, name)).toHaveTextContent('Unknown');
  expect(document.querySelector('time')).toBeNull();
  expect(requestJson).not.toHaveBeenCalled();
});

test('does not mount a foreign run delivery reader', () => {
  setup({ inspection: { ...inspection, summary: { ...summary, run_id: 'other' } } });
  expect(within(deliverySection()).queryByRole('button')).not.toBeInTheDocument();
  expect(screen.getByText('No matching run-level delivery record is available.')).toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
});

test('keeps unknown availability reasons and private job fields out of the UI and bounds the saved summary', () => {
  const view = setup({ inspection: { ...inspection, job: { ...inspection.job, summary: 's'.repeat(2500), prompt: 'private-prompt', stderr: 'private-log', memory: 'private-memory' } } });
  const activity = screen.getByRole('region', { name: 'Recorded activity' });
  expect(valueFor(activity, 'Saved job summary').textContent).toBe(`${'s'.repeat(2000)}…`);
  expect(screen.queryByText(/private-prompt|private-log|private-memory/)).not.toBeInTheDocument();
  view.rerender(<AttemptInspector {...props} inspection={{ available: false, reason: 'private/unrecognized-reason' }} />);
  expect(screen.queryByText('private/unrecognized-reason')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('No matching saved attempt');
});

test.each(['scope', 'changed', 'ambiguous', 'missing', 'stale'])('unavailable %s exposes only a close action and no reader', reason => {
  setup({ inspection: { available: false, reason } });
  expect(screen.getAllByRole('button')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Close attempt details' })).toBeEnabled();
  expect(screen.getByRole('status')).not.toBeEmptyDOMElement();
  expect(requestJson).not.toHaveBeenCalled();
});

test.each(['close', 'cancel'])('native dialog %s aborts pending reads and restores the opener focus', action => {
  requestJson.mockReturnValue(new Promise(() => {}));
  function Harness() {
    const [open, setOpen] = useState(false);
    return <><button onClick={() => setOpen(true)}>Inspect attempt</button>{open ? <AttemptInspector {...props} onClose={() => setOpen(false)} /> : null}</>;
  }
  render(<Harness />);
  const trigger = screen.getByRole('button', { name: 'Inspect attempt' });
  trigger.focus(); fireEvent.click(trigger);
  expect(screen.getByRole('button', { name: 'Close attempt details' })).toHaveFocus();
  openInputs(); openReport();
  const signals = requestJson.mock.calls.map(([, options]) => options.signal);
  if (action === 'cancel') {
    // Browsers emit cancel for Escape on a modal native dialog; jsdom does not implement that default action.
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { bubbles: false, cancelable: true }));
  } else fireEvent.click(screen.getByRole('button', { name: 'Close attempt details' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(signals.every(signal => signal.aborted)).toBe(true);
  expect(trigger).toHaveFocus();
});

test.each(['unavailable', 'replacement'])('aborts a protected Reviewer report on %s and never revives the late report', async change => {
  const item = {
    schema_version: 'goodteam.review-execution.v1', job_id: `job-skill-${'a'.repeat(64)}`,
    task_skill_context: { task: { logical_id: 'run', project_key: 'DEMO' } },
    snapshot_ref: { id: 'snapshot-a', digest: 'sha256:snapshot' }, context_ref: { digest: 'sha256:context' },
    target: { artifact_key: 'source', display_name: 'main.js', artifact_version: 'sha256:source' },
    status: 'received', report_available: true, report_digest: 'sha256:review-report', methods: [],
  };
  const reviewReport = { ...item, project_key: 'DEMO', run_id: 'run', report: {
    schema_version: 'goodteam.code-review-report.v1', summary: 'Protected review of the recorded source.', findings: [], checks: [], evidence: [],
    advisory: true, judgment_verification: 'not_independently_verified',
  } };
  const pending = deferred();
  requestJson.mockResolvedValueOnce({ ...reviews, reviews: [item] }).mockReturnValueOnce(pending.promise);
  const view = setup(); openReviews();
  fireEvent.click(await screen.findByRole('button', { name: 'Open review report' }));
  const [url, options] = requestJson.mock.calls[1];
  expect(url).toBe(`${reviewUrl}/${item.job_id}/report`);
  expect(options.method || 'GET').toBe('GET');
  const next = change === 'unavailable' ? { available: false, reason: 'changed' }
    : { ...inspection, actor: { ...actor, current_attempt_id: 'job-2' }, attempt: { ...attempt, id: 'job-2' } };
  view.rerender(<AttemptInspector {...props} inspection={next} />);
  expect(options.signal.aborted).toBe(true);
  await act(async () => pending.resolve(reviewReport));
  expect(screen.queryByText(reviewReport.report.summary)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Close review report' })).not.toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledTimes(2);
});
