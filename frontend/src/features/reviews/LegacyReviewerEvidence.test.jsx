import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { en } from '../../i18n/locales/en';
import { requestJson } from '../../lib/api/client';
import LegacyReviewerEvidence from './LegacyReviewerEvidence';

vi.mock('../../lib/api/client', () => ({ requestJson: vi.fn() }));
afterEach(() => requestJson.mockReset());
const t = key => en[key] || key;
const queued = { project_key: 'DEMO', run_id: 'run-one', approval: 'release', artifact_key: 'release_notes',
  artifact_version: 'sha256:artifact', profile_id: 'correctness-security',
  job_id: 'job-7c5932f6-f2bb-495c-b2e9-472826dc3bfb', status: 'queued', advisory_only: true };
const completed = { ...queued, status: 'completed', report_version: 'sha256:report',
  href: '/api/reviewer-report?project=DEMO&run=run-one&job=job-7c5932f6-f2bb-495c-b2e9-472826dc3bfb&profile=correctness-security&approval=release&artifact=release_notes&artifact_version=sha256%3Aartifact&version=sha256%3Areport' };
const panel = (item, props = {}) => <LegacyReviewerEvidence projectKey="DEMO" runId="run-one" items={[item]}
  activeApproval="release" t={t} {...props} />;

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

it('starts only the exact queued job and prevents duplicate submissions', async () => {
  const pending = deferred();
  requestJson.mockReturnValue(pending.promise);
  const refresh = vi.fn();
  render(panel(queued, { onRefresh: refresh }));
  const start = screen.getByRole('button', { name: en['review.legacy.start'] });
  fireEvent.click(start); fireEvent.click(start);
  expect(requestJson).toHaveBeenCalledTimes(1);
  expect(requestJson.mock.calls[0][0]).toBe('/api/reviewer-jobs/run');
  expect(JSON.parse(requestJson.mock.calls[0][1].body)).toEqual({
    project_key: 'DEMO', run_id: 'run-one', approval: 'release', artifact_key: 'release_notes',
    artifact_version: 'sha256:artifact', profile_id: 'correctness-security', job_id: queued.job_id,
  });
  await act(async () => pending.resolve({ ...queued, advisory_only: true, approval_unchanged: true,
    ran_count: 1, errors: [], results: [{ ...queued, status: 'completed' }] }));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(start).toBeDisabled();
});

it('discards a late report after the task changes', async () => {
  const pending = deferred();
  requestJson.mockReturnValue(pending.promise);
  const view = render(panel(completed));
  fireEvent.click(screen.getByRole('button', { name: en['review.legacy.open'] }));
  const signal = requestJson.mock.calls[0][1].signal;
  view.rerender(panel(completed, { runId: 'new-run', items: [] }));
  expect(signal.aborted).toBe(true);
  await act(async () => pending.resolve({ ...completed, content: 'High: old finding' }));
  expect(screen.queryByText('High: old finding')).not.toBeInTheDocument();
});

it.each(['artifact_version', 'profile_id', 'report_version'])('rejects a legacy report with a changed %s', async (field) => {
  requestJson.mockResolvedValue({ ...completed, [field]: 'different', content: 'High: mismatched report' });
  render(panel(completed));
  fireEvent.click(screen.getByRole('button', { name: en['review.legacy.open'] }));
  expect(await screen.findByRole('alert')).toHaveTextContent(en['review.legacy.mismatch']);
  expect(screen.queryByText('High: mismatched report')).not.toBeInTheDocument();
});
