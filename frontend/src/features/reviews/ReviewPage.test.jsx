import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import ReviewPage from './ReviewPage';

const mocks = vi.hoisted(() => ({
  answerOpenQuestions: vi.fn(),
  decideReview: vi.fn(),
  getArtifact: vi.fn(),
  getOpenQuestions: vi.fn(),
  getReviewState: vi.fn(),
  refresh: vi.fn(),
  saveArtifact: vi.fn(),
  startChangeIntent: vi.fn(),
}));

vi.mock('./reviewApi', () => ({
  reviewApi: {
    acceptRefinement: vi.fn(),
    answerOpenQuestions: mocks.answerOpenQuestions,
    decideReview: mocks.decideReview,
    discardRefinement: vi.fn(),
    getArtifact: mocks.getArtifact,
    getOpenQuestions: mocks.getOpenQuestions,
    getReviewState: mocks.getReviewState,
    recoverTakeoverBrief: vi.fn(),
    refineArtifact: vi.fn(),
    saveArtifact: mocks.saveArtifact,
  },
}));

vi.mock('../runs/runApi', () => ({
  runApi: {
    startChangeIntent: mocks.startChangeIntent,
  },
}));

const state = {
  state: { run_id: 'run-1' },
  start_context: { project_key: 'DEMO', project_name: 'Demo project' },
  approval_reviews: [
    {
      approval: 'prd',
      title: 'PRD',
      artifact: 'prd',
      artifact_path: '/runs/x/prd.md',
      artifact_version: 'sha256:prd-v1',
      content: '# PRD\nInitial plan.',
      is_actionable: true,
      recommendation: { verdict: 'approve', reasons: [{ kind: 'clean' }] },
      evidence_snapshot: { ci_status: 'passed', smoke_status: 'unknown', qa_status: 'failed', blocker_count: 1 },
      reviewer_provenance: { source: 'independent_reviewer', review_id: 'prd-review-1' },
    },
    {
      approval: 'rfc',
      title: 'RFC',
      artifact: 'rfc',
      artifact_path: '/runs/x/rfc.md',
      content: '# RFC\nArchitecture.',
      is_actionable: false,
      decision: 'pending',
    },
  ],
  workflow_steps: [
    { key: 'takeover', number: '1', title: 'Takeover Context', status: 'done', approval: 'takeover' },
    { key: 'prd', number: '2', title: 'Product / Requirement', status: 'review-needed', approval: 'prd' },
    { key: 'rfc', number: '3', title: 'Architecture', status: 'not-started', approval: 'rfc' },
  ],
  action_items: [{ action: 'approve', after_node: 'rfc', timestamp: '2026-08-15T00:00:00Z' }],
};

function TranslationLayout() {
  return <Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />;
}

function routerFor(path, props = {}) {
  return createMemoryRouter([
    {
      element: <TranslationLayout />,
      children: [
        { path: '/projects/:projectKey/review/:reviewId', element: <ReviewPage {...props} /> },
        {
          path: '/projects/:projectKey/work/runs/:runId',
          element: props.embedded ? <ReviewPage {...props} /> : <div>Manager Intent Commit</div>,
        },
      ],
    },
  ], { initialEntries: [path] });
}

function renderReview(path = '/projects/DEMO/review/prd', props = {}) {
  mocks.getReviewState.mockResolvedValue(state);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
  mocks.refresh.mockResolvedValue(state);
  const router = routerFor(path, props);
  render(<RouterProvider router={router} />);
  return router;
}

function t(key, params) { return translate('en', key, params); }

afterEach(() => {
  vi.clearAllMocks();
});

test('embedded review refuses to substitute a different run or missing approval', async () => {
  const view = renderReview('/projects/DEMO/work/runs/run-old', {
    embedded: true, selectedReviewId: 'prd', expectedRunId: 'run-old', onClose: vi.fn(),
  });
  expect(await screen.findByRole('dialog', { name: 'Review', exact: true })).toBeVisible();
  expect(await screen.findByText(t('work.panel.taskChanged'))).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Approve', exact: true })).not.toBeInTheDocument();
  expect(view.state.location.pathname).toBe('/projects/DEMO/work/runs/run-old');
  expect(mocks.decideReview).not.toHaveBeenCalled();
});
