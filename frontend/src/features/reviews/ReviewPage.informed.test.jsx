import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import ReviewPage from './ReviewPage';

const mocks = vi.hoisted(() => ({ data: null, status: 'ready', refresh: vi.fn(), decideReview: vi.fn(), getOpenQuestions: vi.fn() }));
vi.mock('../runs/useProjectState', () => ({ default: () => ({ data: mocks.data, status: mocks.status, refresh: mocks.refresh, error: null }) }));
vi.mock('./reviewApi', () => ({ reviewApi: { getReviewState: vi.fn(), getArtifact: vi.fn(), decideReview: mocks.decideReview, getOpenQuestions: mocks.getOpenQuestions } }));

const t = (key, params) => translate('en', key, params);
function Layout() { return <Outlet context={{ language: 'en', t }} />; }
function renderReview() {
  const router = createMemoryRouter([{ element: <Layout />, children: [
    { path: '/projects/:projectKey/review/:reviewId', element: <ReviewPage /> },
  ] }], { initialEntries: ['/projects/DEMO/review/prd'] });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.status = 'ready';
  mocks.data = {
    state: { run_id: 'run-1' }, workflow_steps: [], action_items: [],
    approval_reviews: [{
      approval: 'prd', title: 'PRD', artifact: 'prd', artifact_path: '/isolated/prd.md',
      artifact_version: 'sha256:prd-v1', content: '# PRD\nA bounded plan.', is_actionable: true,
      recommendation: { verdict: 'caution', reasons: [{ kind: 'missing_evidence', text: 'Independent review is missing.' }] },
      evidence_snapshot: {}, reviewer_provenance: {},
    }],
  };
  mocks.refresh.mockImplementation(async () => mocks.data);
  mocks.decideReview.mockResolvedValue({ ok: true });
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
});

test('Review rejects empty shortcuts and sends a typed reason with the loaded identity', async () => {
  renderReview();
  const button = await screen.findByRole('button', { name: /Confirm manual continuation/ });
  const input = screen.getByRole('textbox', { name: t('work.informedApproval.reason') });
  expect(button).toBeDisabled();
  fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
  fireEvent.keyDown(input, { key: 'Enter', metaKey: true });
  expect(mocks.decideReview).not.toHaveBeenCalled();
  const note = 'I checked this planning document; independent review is still missing.';
  fireEvent.change(input, { target: { value: note } });
  fireEvent.click(button);
  await waitFor(() => expect(mocks.decideReview).toHaveBeenCalledWith('DEMO', 'prd', 'approved', note, {
    expectedApproval: { run_id: 'run-1', target_type: 'review', target_id: 'prd', artifact_key: 'prd', artifact_version: 'sha256:prd-v1' },
  }));
});

test('an explicit positive recommendation still approves without an extra rationale', async () => {
  mocks.data.approval_reviews[0].recommendation = { verdict: 'approve', reasons: [{ kind: 'clean' }] };
  renderReview();
  fireEvent.click(await screen.findByRole('button', { name: /^Approve/ }));
  await waitFor(() => expect(mocks.decideReview).toHaveBeenCalledWith('DEMO', 'prd', 'approved', '', expect.any(Object)));
});

test('stale page data cannot be approved even with a manual reason', async () => {
  mocks.status = 'stale';
  renderReview();
  const input = await screen.findByRole('textbox', { name: t('work.informedApproval.reason') });
  fireEvent.change(input, { target: { value: 'I accept this gap.' } });
  fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
  expect(screen.getByRole('button', { name: /Confirm manual continuation/ })).toBeDisabled();
  expect(mocks.decideReview).not.toHaveBeenCalled();
});

test('a failed approval preserves the typed reason for an explicit retry', async () => {
  mocks.decideReview.mockRejectedValueOnce(new Error('Approval was not accepted.'));
  renderReview();
  const input = await screen.findByRole('textbox', { name: t('work.informedApproval.reason') });
  fireEvent.change(input, { target: { value: 'Checked the current gap.' } });
  fireEvent.click(screen.getByRole('button', { name: /Confirm manual continuation/ }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Approval was not accepted.'));
  expect(input).toHaveValue('Checked the current gap.');
  await waitFor(() => expect(screen.getByRole('button', { name: /Confirm manual continuation/ })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: /Confirm manual continuation/ }));
  await waitFor(() => expect(mocks.decideReview).toHaveBeenCalledTimes(2));
});

for (const missing of ['version', 'artifact']) {
  test(`a missing ${missing} cannot be approved by supplying an informed reason`, async () => {
    if (missing === 'version') mocks.data.approval_reviews[0].artifact_version = 'missing';
    else mocks.data.approval_reviews[0].artifact = '';
    renderReview();
    const input = await screen.findByRole('textbox', { name: t('work.informedApproval.reason') });
    fireEvent.change(input, { target: { value: 'I accept this gap.' } });
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    const button = screen.getByRole('button', { name: /Confirm manual continuation/ });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(mocks.decideReview).not.toHaveBeenCalled();
  });
}
