import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import ReviewPage from './ReviewPage';

const mocks = vi.hoisted(() => ({
  decideReview: vi.fn(),
  getArtifact: vi.fn(),
  getOpenQuestions: vi.fn(),
  getReviewState: vi.fn(),
  refineArtifact: vi.fn(),
  refresh: vi.fn(),
  saveArtifact: vi.fn(),
}));

vi.mock('./reviewApi', () => ({
  reviewApi: {
    acceptRefinement: vi.fn(),
    answerOpenQuestions: vi.fn(),
    decideReview: mocks.decideReview,
    discardRefinement: vi.fn(),
    getArtifact: mocks.getArtifact,
    getOpenQuestions: mocks.getOpenQuestions,
    getReviewState: mocks.getReviewState,
    recoverTakeoverBrief: vi.fn(),
    refineArtifact: mocks.refineArtifact,
    saveArtifact: mocks.saveArtifact,
  },
}));

vi.mock('../runs/runApi', () => ({ runApi: { getProjectState: mocks.getReviewState } }));

const FULL_CONTENT = '# Long review\n\nThe full body is loaded.\n\nAcceptance: retain this final criterion.';
const state = {
  state: { run_id: 'run-1' },
  approval_reviews: [{
    approval: 'prd',
    title: 'PRD',
    artifact: 'prd',
    artifact_path: '/runs/run-1/prd.md',
    artifact_version: 'sha256:v1',
    content: '# Long review\n\nThe preview ends here.\n\n[Preview truncated]',
    content_truncated: true,
    is_actionable: true,
  }],
  workflow_steps: [],
};

function Layout() {
  return <Outlet context={{ t: (key, params) => translate('en', key, params) }} />;
}

function routerFor() {
  return createMemoryRouter([
    { element: <Layout />, children: [{ path: '/projects/:projectKey/review/:reviewId', element: <ReviewPage /> }] },
  ], { initialEntries: ['/projects/DEMO/review/prd'] });
}

function deferred() {
  let resolve;
  const promise = new Promise((accept) => { resolve = accept; });
  return { promise, resolve };
}

afterEach(() => vi.clearAllMocks());

test('loads full content before editing and saves against its run and version', async () => {
  const user = userEvent.setup();
  const fullArtifact = deferred();
  mocks.getReviewState.mockResolvedValue(state);
  mocks.refresh.mockResolvedValue(state);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
  mocks.getArtifact
    .mockReturnValueOnce(fullArtifact.promise)
    .mockResolvedValueOnce({ content: `${FULL_CONTENT}\nEditor addition`, run_id: 'run-1', artifact_version: 'sha256:v2' });
  mocks.saveArtifact.mockResolvedValue({ saved: true });

  render(<RouterProvider router={routerFor()} />);

  const editButton = await screen.findByRole('button', { name: translate('en', 'review.artifact.edit') });
  expect(editButton).toBeDisabled();
  await act(async () => {
    fullArtifact.resolve({ content: FULL_CONTENT, run_id: 'run-1', artifact_version: 'sha256:v1' });
  });
  await screen.findByText('Acceptance: retain this final criterion.');
  await user.click(editButton);
  const editor = screen.getByRole('textbox', { name: translate('en', 'review.artifact.content') });
  await user.type(editor, '\nEditor addition');
  await user.click(screen.getByRole('button', { name: translate('en', 'review.actions.save') }));

  expect(mocks.saveArtifact).toHaveBeenCalledWith(
    'DEMO',
    'prd',
    `${FULL_CONTENT}\nEditor addition`,
    { expectedArtifact: { runId: 'run-1', artifactVersion: 'sha256:v1' } },
  );
  expect(await screen.findByRole('textbox', { name: translate('en', 'review.artifact.content') }))
    .toHaveValue(`${FULL_CONTENT}\nEditor addition`);
});

test('refines against the exact run and full-content version', async () => {
  const user = userEvent.setup();
  mocks.getReviewState.mockResolvedValue(state);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
  mocks.getArtifact.mockResolvedValue({
    content: FULL_CONTENT,
    run_id: 'run-1',
    artifact_version: 'sha256:v1',
  });
  mocks.refineArtifact.mockResolvedValue({ ok: true });

  render(<RouterProvider router={routerFor()} />);
  await screen.findByText('Acceptance: retain this final criterion.');
  await user.click(screen.getByRole('button', { name: translate('en', 'review.actions.refine') }));

  expect(mocks.refineArtifact).toHaveBeenCalledWith(
    'DEMO',
    'prd',
    FULL_CONTENT,
    { expectedArtifact: { runId: 'run-1', artifactVersion: 'sha256:v1' } },
  );
});

test('accepts a complete review mock as full content without requesting the artifact', async () => {
  const completeState = {
    ...state,
    approval_reviews: [{
      ...state.approval_reviews[0],
      content: FULL_CONTENT,
      content_truncated: undefined,
    }],
  };
  mocks.getReviewState.mockResolvedValue(completeState);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });

  render(<RouterProvider router={routerFor()} />);

  await screen.findByText('Acceptance: retain this final criterion.');
  expect(mocks.getArtifact).not.toHaveBeenCalled();
});

test('approves the loaded full-content version, not a newer preview snapshot', async () => {
  const user = userEvent.setup();
  const pendingArtifact = deferred();
  mocks.getReviewState.mockResolvedValue(state);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
  mocks.getArtifact.mockReturnValue(pendingArtifact.promise);
  mocks.decideReview.mockResolvedValue({ ok: true });

  render(<RouterProvider router={routerFor()} />);
  const approve = await screen.findByRole('button', { name: translate('en', 'review.decision.approve') });
  expect(approve).toBeDisabled();
  await waitFor(() => expect(mocks.getArtifact).toHaveBeenCalledTimes(1));

  await act(async () => {
    pendingArtifact.resolve({
      content: FULL_CONTENT,
      run_id: 'run-1',
      artifact_version: 'sha256:full-v2',
    });
    await pendingArtifact.promise;
  });

  await waitFor(() => expect(approve).toBeEnabled());
  await user.click(approve);
  await waitFor(() => expect(mocks.decideReview).toHaveBeenCalledWith(
    'DEMO',
    'prd',
    'approved',
    '',
    {
      expectedApproval: {
        run_id: 'run-1',
        target_type: 'review',
        target_id: 'prd',
        artifact_key: 'prd',
        artifact_version: 'sha256:full-v2',
      },
    },
  ));
});

test('blocks approval while the visible artifact has unsaved edits', async () => {
  const user = userEvent.setup();
  const completeState = {
    ...state,
    approval_reviews: [{
      ...state.approval_reviews[0],
      content: FULL_CONTENT,
      content_truncated: false,
    }],
  };
  mocks.getReviewState.mockResolvedValue(completeState);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });

  render(<RouterProvider router={routerFor()} />);
  await user.click(await screen.findByRole('button', { name: translate('en', 'review.artifact.edit') }));
  await user.type(screen.getByRole('textbox', { name: translate('en', 'review.artifact.content') }), '\nUnsaved edit');

  expect(screen.getByRole('button', { name: translate('en', 'review.decision.approve') })).toBeDisabled();
  expect(mocks.decideReview).not.toHaveBeenCalled();
});
