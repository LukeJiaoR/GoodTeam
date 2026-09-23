import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { requestJson } from '../../../lib/api/client';
import SkillReviewExecutions from './SkillReviewExecutions';

vi.mock('../../../lib/api/client', () => ({ requestJson: vi.fn() }));
afterEach(() => requestJson.mockReset());
const item = {
  schema_version: 'goodteam.review-execution.v1', job_id: `job-skill-${'a'.repeat(64)}`,
  task_skill_context: { task: { logical_id: 'run', project_key: 'DEMO' } },
  snapshot_ref: { id: 'snapshot-a', digest: 'sha256:snapshot' }, context_ref: { digest: 'sha256:context' },
  target: { artifact_key: 'source', display_name: 'main.js', artifact_version: 'sha256:source' },
  methods: [{ skill_id: 'skill-a', version_id: 'version-a', package_digest: 'sha256:method', activation: 'required' }],
  stages: { content_delivered: 'observed', resource_read_observed: 'unobserved' },
  status: 'received', report_available: true, report_digest: 'sha256:report', runtime_verification: 'contract_fixture',
};
const list = { schema_version: 'goodteam.task-reviews.v1', project_key: 'DEMO', run_id: 'run', reviews: [item] };
const report = { ...item, project_key: 'DEMO', run_id: 'run', report: {
  schema_version: 'goodteam.code-review-report.v1', summary: 'Inspected exact source; no findings.', findings: [],
  checks: [{ name: 'Tests', status: 'not_run', evidence_ids: [], detail: 'Tests were not executed.' }],
  evidence: [{ id: 'E1', path: 'candidate', line_start: 1, line_end: 1, quote: '<script>not executable</script>', observation: 'Source text.', source_digest: 'sha256:source' }],
  advisory: true, judgment_verification: 'not_independently_verified',
} };
const openPanel = () => fireEvent.click(screen.getByRole('button', { name: 'Review execution details' }));
async function openReport() {
  fireEvent.click(await screen.findByRole('button', { name: 'Open review report' }));
}
function setup() { return render(<SkillReviewExecutions projectKey="DEMO" runId="run" language="en" />); }

describe('Task-bound Skill review details', () => {
  it('loads the execution first, then the report only on request; source evidence stays collapsed', async () => {
    requestJson.mockResolvedValueOnce(list).mockResolvedValueOnce(report);
    setup();
    expect(requestJson).not.toHaveBeenCalled();
    openPanel();
    expect(await screen.findByText('Contract fixture · not live Runtime acceptance')).toBeVisible();
    expect(requestJson).toHaveBeenCalledTimes(1);
    await openReport();
    expect(await screen.findByText('No findings reported')).toBeVisible();
    expect(screen.getByText('Tests were not executed.')).toBeVisible();
    const quote = screen.getByText('<script>not executable</script>');
    expect(quote.closest('details')).not.toHaveAttribute('open');
    expect(quote.querySelector('script')).toBeNull();
    expect(screen.getByText(/does not independently confirm/)).toBeVisible();
  });
  it.each(['failed', 'invalid', 'cancelled', 'interrupted', 'stopping', 'recovery_required', 'running', 'prepared'])('does not show a report action for %s, even with a contradictory success flag', async (status) => {
    requestJson.mockResolvedValue({ ...list, reviews: [{ ...item, status, report_available: true }] });
    setup(); openPanel();
    await screen.findByRole('article');
    expect(screen.queryByRole('button', { name: 'Open review report' })).not.toBeInTheDocument();
  });
  it.each(['project_key', 'run_id', 'job_id', 'report_digest'])('rejects a mismatched %s response', async (field) => {
    requestJson.mockResolvedValueOnce(list).mockResolvedValueOnce({ ...report, [field]: 'OTHER' });
    setup(); openPanel(); await openReport();
    expect(await screen.findByRole('alert')).toHaveTextContent('No successful report is shown');
    expect(screen.queryByText('No findings reported')).not.toBeInTheDocument();
  });
  it('rejects changed target/version and snapshot identity', async () => {
    requestJson.mockResolvedValueOnce(list).mockResolvedValueOnce({ ...report, target: { ...item.target, artifact_version: 'sha256:other' } });
    setup(); openPanel(); await openReport();
    await screen.findByRole('alert');
    expect(screen.queryByText(report.report.summary)).not.toBeInTheDocument();
  });
  it('drops a previously shown report when refresh fails', async () => {
    requestJson.mockResolvedValueOnce(list).mockResolvedValueOnce(report).mockRejectedValueOnce(new Error('unavailable'));
    setup(); openPanel(); await openReport();
    await screen.findByText('No findings reported');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh reviews' }));
    await screen.findByRole('alert');
    expect(screen.queryByText('No findings reported')).not.toBeInTheDocument();
  });
  it('aborts and discards a late report when switching tasks', async () => {
    let resolve;
    requestJson.mockResolvedValueOnce(list).mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const view = setup(); openPanel(); await openReport();
    const signal = requestJson.mock.calls[1][1].signal;
    view.rerender(<SkillReviewExecutions projectKey="DEMO" runId="next" language="en" />);
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(report));
    expect(screen.queryByText('No findings reported')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review execution details' })).toHaveAttribute('aria-expanded', 'false');
  });
  it('rejects a list with foreign-task results rather than silently mixing tasks', async () => {
    requestJson.mockResolvedValue({ ...list, reviews: [{ ...item, task_skill_context: { task: { logical_id: 'other', project_key: 'DEMO' } } }] });
    setup(); openPanel(); await screen.findByRole('alert');
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
  });
  it('renders the same boundaries in Chinese', async () => {
    requestJson.mockResolvedValue(list);
    render(<SkillReviewExecutions projectKey="DEMO" runId="run" language="zh" />);
    fireEvent.click(screen.getByRole('button', { name: '审查执行详情' }));
    const panel = await screen.findByRole('region', { name: '审查执行详情' });
    expect(within(panel).getByText(/不会自动批准、合并或部署/)).toBeVisible();
    expect(await within(panel).findByText('合同测试夹具 · 不是真实 Runtime 验收')).toBeVisible();
  });
});

describe('Locked-source retry', () => {
  const failed = { ...item, status: 'invalid', report_available: false, retry_available: true };
  it('posts only displayed immutable identity, then refreshes without claiming a received report', async () => {
    requestJson.mockResolvedValueOnce({ ...list, reviews: [failed] })
      .mockResolvedValueOnce({ project_key: 'DEMO', run_id: 'run', original_job_id: item.job_id,
        job_id: `job-skill-${'b'.repeat(64)}`, status: 'failed' })
      .mockResolvedValueOnce({ ...list, reviews: [failed, { ...failed, job_id: `job-skill-${'b'.repeat(64)}`, retry_available: false }] });
    setup(); openPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Retry with locked inputs' }));
    await screen.findAllByRole('article');
    await vi.waitFor(() => expect(requestJson).toHaveBeenCalledTimes(3));
    const [url, options] = requestJson.mock.calls[1];
    expect(url).toBe(`/api/projects/DEMO/runs/run/skill-reviews/${item.job_id}/retry`);
    expect(options.method).toBe('POST');
    expect(options.headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(options.body)).toEqual({ snapshot_digest: item.snapshot_ref.digest, context_digest: item.context_ref.digest });
    expect(screen.queryByRole('button', { name: 'Open review report' })).not.toBeInTheDocument();
  });
  it.each(['running', 'stopping', 'received', 'recovery_required', 'unavailable'])('does not offer retry for %s despite a contradictory flag', async (status) => {
    requestJson.mockResolvedValue({ ...list, reviews: [{ ...failed, status }] });
    setup(); openPanel(); await screen.findByRole('article');
    expect(screen.queryByRole('button', { name: 'Retry with locked inputs' })).not.toBeInTheDocument();
  });
  it('keeps an API-rejected retry as a failure, not an optimistic success', async () => {
    requestJson.mockResolvedValueOnce({ ...list, reviews: [failed] }).mockRejectedValueOnce(new Error('revoked'));
    setup(); openPanel(); fireEvent.click(await screen.findByRole('button', { name: 'Retry with locked inputs' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Retry');
    expect(screen.queryByText('Report received', { exact: true })).not.toBeInTheDocument();
    expect(requestJson).toHaveBeenCalledTimes(2);
  });
  it('aborts pending retry UI on task switch and ignores its late response', async () => {
    let resolve;
    requestJson.mockResolvedValueOnce({ ...list, reviews: [failed] }).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const view = setup(); openPanel(); fireEvent.click(await screen.findByRole('button', { name: 'Retry with locked inputs' }));
    const signal = requestJson.mock.calls[1][1].signal;
    view.rerender(<SkillReviewExecutions projectKey="DEMO" runId="next" language="en" />);
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ project_key: 'DEMO', run_id: 'run', original_job_id: item.job_id, job_id: `job-skill-${'b'.repeat(64)}` }));
    expect(requestJson).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
  });
  it('shows exact commit pair and collapsed file versions; rejects the other source side', async () => {
    const target = { ...item.target, kind: 'git_change', base_sha: 'a'.repeat(40), head_sha: 'b'.repeat(40),
      files: [{ path: 'src/main.js', change: 'M', base: { blob_id: '1'.repeat(40) }, head: { blob_id: '2'.repeat(40) } }] };
    requestJson.mockResolvedValueOnce({ ...list, reviews: [{ ...item, target }] })
      .mockResolvedValueOnce({ ...report, target: { ...target, head_sha: 'c'.repeat(40) } });
    setup(); openPanel();
    const heading = await screen.findByText('Changed files · exact base and head');
    expect(heading.closest('details')).not.toHaveAttribute('open');
    fireEvent.click(heading);
    expect(screen.getByText('b'.repeat(40))).toBeVisible();
    await openReport(); await screen.findByRole('alert');
    expect(screen.queryByText('No findings reported')).not.toBeInTheDocument();
  });
});

describe('Exact-attempt cancellation', () => {
  const running = { ...item, status: 'running', report_available: false, retry_available: false, cancel_available: true };
  const stopping = { ...running, status: 'stopping', cancel_available: false, cancel_requested: true };
  const stopped = { ...stopping, status: 'cancelled', retry_available: true };
  const response = { project_key: 'DEMO', run_id: 'run', job_id: item.job_id,
    snapshot_digest: item.snapshot_ref.digest, context_digest: item.context_ref.digest,
    cancel_requested: true, status: 'stopping', termination_confirmed: false, advisory: true, approval_changed: false };
  it('posts only displayed identity, shows stopping, and allows retry only after saved termination', async () => {
    requestJson.mockResolvedValueOnce({ ...list, reviews: [running] }).mockResolvedValueOnce(response)
      .mockResolvedValueOnce({ ...list, reviews: [stopping] }).mockResolvedValueOnce({ ...list, reviews: [stopped] });
    setup(); openPanel(); fireEvent.click(await screen.findByRole('button', { name: 'Cancel review', exact: true }));
    expect(await screen.findByText('Stop requested. Termination is not yet confirmed; no report or retry is available.')).toBeVisible();
    const [url, options] = requestJson.mock.calls[1];
    expect(url).toBe(`/api/projects/DEMO/runs/run/skill-reviews/${item.job_id}/cancel`);
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({ snapshot_digest: item.snapshot_ref.digest, context_digest: item.context_ref.digest });
    expect(screen.queryByRole('button', { name: 'Retry with locked inputs' })).not.toBeInTheDocument();
    expect(screen.queryByText('Cancelled', { exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh reviews' }));
    expect(await screen.findByText('Cancelled', { exact: true })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Retry with locked inputs' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Open review report' })).not.toBeInTheDocument();
  });
  it.each(['received', 'failed', 'invalid', 'cancelled', 'interrupted', 'recovery_required'])('does not offer stop for terminal/uncertain %s despite a flag', async (status) => {
    requestJson.mockResolvedValue({ ...list, reviews: [{ ...running, status }] });
    setup(); openPanel(); await screen.findByRole('article');
    expect(screen.queryByRole('button', { name: 'Cancel review', exact: true })).not.toBeInTheDocument();
  });
  it('retains metadata-only cancellation after method revocation but never exposes a report', async () => {
    requestJson.mockResolvedValue({ ...list, reviews: [{ ...running, status: 'unavailable', control_status: 'running', error_code: 'skill_revoked' }] });
    setup(); openPanel();
    expect(await screen.findByRole('button', { name: 'Cancel review', exact: true })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Open review report' })).not.toBeInTheDocument();
  });
  it.each(['context_digest', 'snapshot_digest', 'job_id', 'run_id'])('rejects a cancellation response with wrong %s', async (key) => {
    requestJson.mockResolvedValueOnce({ ...list, reviews: [running] }).mockResolvedValueOnce({ ...response, [key]: 'other' });
    setup(); openPanel(); fireEvent.click(await screen.findByRole('button', { name: 'Cancel review', exact: true }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Stop was not confirmed');
    expect(requestJson).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('Cancelled', { exact: true })).not.toBeInTheDocument();
  });
  it('does not treat a failed HTTP request as termination', async () => {
    requestJson.mockResolvedValueOnce({ ...list, reviews: [running] }).mockRejectedValueOnce(new Error('offline'));
    setup(); openPanel(); fireEvent.click(await screen.findByRole('button', { name: 'Cancel review', exact: true }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Stop was not confirmed');
    expect(screen.queryByRole('button', { name: 'Retry with locked inputs' })).not.toBeInTheDocument();
  });
  it('aborts only the view request on task switch, without claiming to cancel the execution', async () => {
    let resolve;
    requestJson.mockResolvedValueOnce({ ...list, reviews: [running] }).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const view = setup(); openPanel(); fireEvent.click(await screen.findByRole('button', { name: 'Cancel review', exact: true }));
    const signal = requestJson.mock.calls[1][1].signal;
    view.rerender(<SkillReviewExecutions projectKey="DEMO" runId="next" language="en" />);
    expect(signal.aborted).toBe(true);
    await act(async () => resolve(response));
    expect(requestJson).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
  });
  it('does not expose a late successful report carrying an acknowledged cancellation', async () => {
    requestJson.mockResolvedValueOnce({ ...list, reviews: [{ ...item, cancel_requested: true }] });
    setup(); openPanel(); await screen.findByRole('article');
    expect(screen.queryByRole('button', { name: 'Open review report' })).not.toBeInTheDocument();
  });
});

function readItem() {
  return { ...item, runtime: { session_id: 'session-a' }, stages: { ...item.stages, resource_read_observed: 'observed' }, resource_reads: [{
    schema_version: 'goodteam.codex-resource-read.v1', origin: 'native_command_event', item_id: 'item_0',
    skill_id: 'skill-a', version_id: 'version-a', package_digest: 'sha256:method', path: 'references/check.md', size: 12,
    context_digest: item.context_ref.digest, runtime_session_id: 'session-a', runtime_verification: 'contract_fixture',
    resource_digest: `sha256:${'1'.repeat(64)}`, transcript_digest: `sha256:${'2'.repeat(64)}`,
    started_digest: `sha256:${'3'.repeat(64)}`, completed_digest: `sha256:${'4'.repeat(64)}`,
  }] };
}
it('shows context-bound read witnesses only in collapsed execution details, not method bodies', async () => {
  requestJson.mockResolvedValue({ ...list, reviews: [readItem()] });
  setup(); openPanel();
  const summary = await screen.findByText('Resource read witnesses');
  expect(summary.closest('details')).not.toHaveAttribute('open');
  expect(screen.getByText('references/check.md')).toBeInTheDocument();
  expect(screen.getByText(/not proof of comprehension/)).toBeInTheDocument();
  expect(requestJson).toHaveBeenCalledTimes(1);
});
it.each(['context_digest', 'version_id', 'runtime_session_id', 'resource_digest'])('rejects foreign or corrupt read witness %s', async field => {
  const wrong = readItem(); wrong.resource_reads[0][field] = 'other';
  requestJson.mockResolvedValue({ ...list, reviews: [wrong] });
  setup(); openPanel();
  expect(await screen.findByRole('alert')).toHaveTextContent('No successful report is shown');
  expect(screen.queryByText('Resource read witnesses')).not.toBeInTheDocument();
});
