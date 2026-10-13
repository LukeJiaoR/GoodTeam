import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import TaskInputContext from './TaskInputContext.jsx';
import { runApi } from '../runApi';
import { requestJson } from '../../../lib/api/client';

vi.mock('../runApi', () => ({ runApi: { getTaskInputContext: vi.fn() } }));
vi.mock('../../../lib/api/client', () => ({ requestJson: vi.fn() }));
afterEach(() => { vi.resetAllMocks(); });
const locked = {
  schema_version: 'goodteam.task-input-context.v1', project_key: 'DEMO', run_id: 'run', task_kind: 'workflow_run', read_only: true,
  capture_status: 'locked', input_origin: 'task_start_lock',
  input: { exact_request: '  Original input\n  keep indent', source_type: 'existing_project', delivery_mode: 'feature_increment', delivery_standards: ['Stable API'] },
  captured_at: '2026-10-11T10:00:00Z', team_revision: 2,
  task_lock_ref: { id: 'task-1', digest: `sha256:${'a'.repeat(64)}` },
  subjects: [{ subject: 'role:code_review', mode: 'custom', diagnostics: [{ code: 'missing_at_capture' }],
    methods: [{ skill_id: 'review-method', version_id: 'version-1', package_digest: `sha256:${'b'.repeat(64)}`, activation: 'on_demand' }] }],
};
const setup = () => render(<TaskInputContext projectKey="DEMO" runId="run" language="en" />);
const open = () => fireEvent.click(screen.getByRole('button', { name: 'Task inputs and methods' }));

test('reads the exact record only on demand and reuses the scoped read-only evidence reader', async () => {
  runApi.getTaskInputContext.mockResolvedValue(locked);
  requestJson.mockResolvedValue({ schema_version: 'goodteam.task-reviews.v1', project_key: 'DEMO', run_id: 'run', reviews: [], start_available: true });
  setup(); expect(runApi.getTaskInputContext).not.toHaveBeenCalled(); open();
  expect((await screen.findByText(/Original input/)).textContent).toBe(locked.input.exact_request);
  expect(screen.getByText('version-1')).toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Review evidence for this delivery' }));
  await screen.findByText(/No registered review evidence/);
  expect(requestJson).toHaveBeenCalledWith('/api/projects/DEMO/runs/run/skill-reviews', expect.objectContaining({ cache: 'no-store', signal: expect.any(AbortSignal) }));
  expect(screen.queryByRole('button', { name: /Start review|Retry with locked inputs|Cancel review/ })).not.toBeInTheDocument();
});

test.each([
  { schema_version: 'unknown' }, { project_key: 'OTHER' }, { run_id: 'old' }, { task_kind: 'operator' }, { read_only: false },
  { input_origin: 'workflow_state' }, { task_lock_ref: null }, { input: { exact_request: 'partial payload' } },
  { subjects: [{ subject: 'role:code_review', mode: 'custom', methods: 'bad', diagnostics: [] }] },
])('fails closed on an unverified task contract %j', async mismatch => {
  runApi.getTaskInputContext.mockResolvedValue({ ...locked, ...mismatch });
  setup(); open(); await screen.findByRole('alert');
  expect(screen.queryByText(/Original input/)).not.toBeInTheDocument();
  expect(screen.queryByText('version-1')).not.toBeInTheDocument();
  expect(requestJson).not.toHaveBeenCalled();
});

test.each(['run', 'project'])('aborts and rejects late input when the %s changes', async change => {
  let finish;
  runApi.getTaskInputContext.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const view = setup(); open();
  const signal = runApi.getTaskInputContext.mock.calls[0][2].signal;
  const projectKey = change === 'project' ? 'OTHER' : 'DEMO';
  const runId = change === 'run' ? 'next' : 'run';
  view.rerender(<TaskInputContext projectKey={projectKey} runId={runId} language="en" />);
  expect(signal.aborted).toBe(true);
  await act(async () => finish(locked));
  expect(screen.queryByText(/Original input/)).not.toBeInTheDocument();
  runApi.getTaskInputContext.mockResolvedValue({ ...locked, project_key: projectKey, run_id: runId, input: { ...locked.input, exact_request: 'Selected task' } });
  open(); await screen.findByText('Selected task');
  expect(runApi.getTaskInputContext).toHaveBeenLastCalledWith(projectKey, runId, expect.objectContaining({ signal: expect.any(AbortSignal) }));
});

test('closing a pending read and reopening cannot revive its stale result', async () => {
  let finish;
  runApi.getTaskInputContext.mockReturnValueOnce(new Promise(resolve => { finish = resolve; })).mockResolvedValueOnce({ ...locked, input: { ...locked.input, exact_request: 'Freshly read record' } });
  setup(); open();
  const signal = runApi.getTaskInputContext.mock.calls[0][2].signal;
  fireEvent.click(screen.getByRole('button', { name: 'Close task inputs and methods' }));
  expect(signal.aborted).toBe(true);
  open(); await screen.findByText('Freshly read record');
  await act(async () => finish(locked));
  expect(screen.queryByText(/Original input/)).not.toBeInTheDocument();
  expect(screen.getByText('Freshly read record')).toBeVisible();
});

test('retries a failed read without substituting workflow or Team data', async () => {
  runApi.getTaskInputContext.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(locked);
  setup(); open(); await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading task inputs' }));
  await screen.findByText(/Original input/);
  expect(runApi.getTaskInputContext).toHaveBeenCalledTimes(2);
  expect(requestJson).not.toHaveBeenCalled();
});

test('keeps unavailable and legacy input separate from a verified start capture', async () => {
  runApi.getTaskInputContext.mockResolvedValueOnce({ ...locked, capture_status: 'unavailable', input_origin: 'unavailable', input: null, subjects: [], error_code: 'snapshot_unavailable' })
    .mockResolvedValueOnce({ ...locked, capture_status: 'legacy_unverified', input_origin: 'workflow_state', team_revision: null, task_lock_ref: null, subjects: [], captured_at: '' });
  setup(); open();
  await screen.findByText('Input source unknown');
  expect(screen.queryByText(/Original input/)).not.toBeInTheDocument();
  expect(screen.queryByText('version-1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading task inputs' }));
  await screen.findByText('Workflow record · unverified');
  expect(screen.queryByText('Task-start record')).not.toBeInTheDocument();
  expect(screen.queryByText('version-1')).not.toBeInTheDocument();
});

test('closing an expanded evidence read aborts that child request too', async () => {
  runApi.getTaskInputContext.mockResolvedValue(locked);
  requestJson.mockReturnValue(new Promise(() => {}));
  setup(); open(); await screen.findByText(/Original input/);
  fireEvent.click(screen.getByRole('button', { name: 'Review evidence for this delivery' }));
  await waitFor(() => expect(requestJson).toHaveBeenCalledTimes(1));
  const signal = requestJson.mock.calls[0][1].signal;
  fireEvent.click(screen.getByRole('button', { name: 'Close task inputs and methods' }));
  expect(signal.aborted).toBe(true);
});


test.each(['snapshot_scope_mismatch', 'unexpected /private/internal/error'])('keeps unavailable reasons bounded to known diagnostics: %s', async errorCode => {
  runApi.getTaskInputContext.mockResolvedValue({ ...locked, capture_status: 'unavailable', input_origin: 'unavailable', input: null, subjects: [], error_code: errorCode });
  setup(); open(); await screen.findByText('Input source unknown');
  if (errorCode === 'snapshot_scope_mismatch') {
    expect(screen.getByText('The saved capture does not match this project, task, or project root.')).toBeInTheDocument();
  }
  expect(screen.queryByText(errorCode)).not.toBeInTheDocument();
});
