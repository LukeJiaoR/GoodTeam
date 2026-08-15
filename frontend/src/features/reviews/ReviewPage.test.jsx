import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

const state = {
  start_context: { project_key: 'DEMO', project_name: 'Demo project' },
  approval_reviews: [
    {
      approval: 'prd',
      title: 'PRD',
      artifact: 'prd',
      artifact_path: '/runs/x/prd.md',
      content: '# PRD\nInitial plan.',
      is_actionable: true,
      recommendation: { verdict: 'approve', reasons: [{ kind: 'clean' }] },
      evidence_snapshot: { ci_status: 'passed', smoke_status: 'unknown', qa_status: 'failed', blocker_count: 1 },
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

function routerFor(path) {
  return createMemoryRouter([
    {
      element: <TranslationLayout />,
      children: [
        { path: '/projects/:projectKey/review/:reviewId', element: <ReviewPage /> },
      ],
    },
  ], { initialEntries: [path] });
}

function renderReview(path = '/projects/DEMO/review/prd') {
  mocks.getReviewState.mockResolvedValue(state);
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
  mocks.refresh.mockResolvedValue(state);
  const router = routerFor(path);
  render(<RouterProvider router={router} />);
  return router;
}

function t(key, params) { return translate('en', key, params); }

afterEach(() => {
  vi.clearAllMocks();
});

test('guards navigation away from unsaved review edits with a dialog', async () => {
  const user = userEvent.setup();
  const router = renderReview();

  const editor = await screen.findByRole('textbox', { name: t('review.artifact.content') });
  await user.type(editor, ' + extra edit');

  expect(screen.getByRole('link', { name: /PRD/ })).toHaveAttribute('aria-current', 'true');

  await user.click(screen.getByRole('link', { name: /RFC/ }));
  expect(await screen.findByRole('dialog')).toBeVisible();
  expect(screen.getByText(t('review.dirty.title'))).toBeVisible();

  await user.click(screen.getByRole('button', { name: t('review.dirty.stay') }));
  expect(router.state.location.pathname).toBe('/projects/DEMO/review/prd');
  expect(screen.getByRole('textbox', { name: t('review.artifact.content') })).toHaveValue('# PRD\nInitial plan. + extra edit');

  await user.click(screen.getByRole('link', { name: /RFC/ }));
  await user.click(await screen.findByRole('button', { name: t('review.dirty.leave') }));
  expect(router.state.location.pathname).toBe('/projects/DEMO/review/rfc');
});

test('shows an approved decision result with the next step and link to Work', async () => {
  const user = userEvent.setup();
  mocks.decideReview.mockResolvedValue({ ok: true });
  renderReview();

  await user.click(await screen.findByRole('button', { name: t('review.decision.approve') }));

  expect(await screen.findByText('Approved PRD')).toBeVisible();
  expect(screen.getByText(`Next: ${t('review.decision.node.rfc')}`)).toBeVisible();
  expect(screen.getByRole('link', { name: t('review.decision.viewNext') }))
    .toHaveAttribute('href', '/projects/DEMO/work');
  expect(mocks.decideReview).toHaveBeenCalledWith('DEMO', 'prd', 'approved', '');
});

test('renders the evidence summary badges from the snapshot', async () => {
  renderReview();

  expect(await screen.findByText('CI: Passed')).toBeVisible();
  expect(screen.getByText('Smoke: Unknown')).toBeVisible();
  expect(screen.getByText('QA: Failed')).toBeVisible();
  expect(screen.getByText('Blockers 1')).toBeVisible();
  expect(screen.getByText(t('review.evidence.title'), { selector: 'summary' })).toBeVisible();
});

test('clears submitted question answers and confirms the count', async () => {
  const user = userEvent.setup();
  mocks.getReviewState.mockResolvedValue(state);
  mocks.getOpenQuestions.mockResolvedValue({
    questions: ['Should the CLI be the only surface?'],
  });
  mocks.refresh.mockResolvedValue(state);
  mocks.answerOpenQuestions.mockResolvedValue({ ok: true });
  const router = routerFor('/projects/DEMO/review/prd');
  render(<RouterProvider router={router} />);

  const answerBox = await screen.findByRole('textbox', { name: 'Should the CLI be the only surface?' });
  await user.type(answerBox, 'yes');
  await user.click(screen.getByRole('button', { name: t('review.questions.apply') }));

  expect(await screen.findByText('Submitted 1 answer')).toBeVisible();
  expect(mocks.answerOpenQuestions).toHaveBeenCalledWith('DEMO', 'prd', [
    { question: 'Should the CLI be the only surface?', answer: 'yes' },
  ]);
  expect(screen.getByRole('textbox', { name: 'Should the CLI be the only surface?' })).toHaveValue('');
});

test('renders the pipeline position indicator for the selected gate', async () => {
  renderReview('/projects/DEMO/review/prd');

  expect(await screen.findByText(`Step 2 of 3`)).toBeVisible();
});
