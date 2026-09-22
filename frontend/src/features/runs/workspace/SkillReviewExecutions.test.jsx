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
