import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test, vi } from 'vitest';
import MarkdownView from '../../components/ui/MarkdownView';
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

function routerFor(path) {
  return createMemoryRouter([
    {
      element: <TranslationLayout />,
      children: [
        { path: '/projects/:projectKey/review/:reviewId', element: <ReviewPage /> },
        {
          path: '/projects/:projectKey/work/runs/:runId',
          element: <div>Manager Intent Commit</div>,
        },
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

test('loads full version-bound text before offering truncated artifact editing', async () => {
  const review = { ...state.approval_reviews[0], content_truncated: true, content: '# Truncated preview' };
  let resolve;
  mocks.getArtifact.mockReturnValue(new Promise((done) => { resolve = done; }));
  mocks.getReviewState.mockResolvedValue({ ...state, approval_reviews: [review] });
  mocks.getOpenQuestions.mockResolvedValue({ questions: [] });
  render(<RouterProvider router={routerFor('/projects/DEMO/review/prd')} />);
  await vi.waitFor(() => expect(mocks.getArtifact).toHaveBeenCalled());
  expect(screen.queryByRole('button', { name: 'Save artifact' })).not.toBeInTheDocument();
  resolve({ content: '# Complete artifact\nUntruncated acceptance criteria.', run_id: 'run-1', artifact_version: 'sha256:prd-v1' });
  expect(await screen.findByText('Untruncated acceptance criteria.')).toBeInTheDocument();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('ReviewPage workflow and navigation', () => {
  test('guards navigation away from unsaved review edits with a dialog', async () => {
    const user = userEvent.setup();
    const router = renderReview();

    await user.click(await screen.findByRole('button', { name: t('review.artifact.edit') }));
    const editor = screen.getByRole('textbox', { name: t('review.artifact.content') });
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
    expect(mocks.decideReview).toHaveBeenCalledWith(
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
          artifact_version: 'sha256:prd-v1',
        },
      },
    );
  });

  test('routes Request changes into Manager Intent Commit without a direct decision', async () => {
    const user = userEvent.setup();
    mocks.startChangeIntent.mockResolvedValue({
      intent_commit: { id: 'intent-prd-1', status: 'discussing' },
    });
    const router = renderReview();

    await user.click(await screen.findByRole('button', { name: t('review.decision.changes') }));

    expect(mocks.startChangeIntent).toHaveBeenCalledWith(
      'DEMO',
      'prd',
      {
        run_id: 'run-1',
        target_type: 'review',
        target_id: 'prd',
        artifact_key: 'prd',
        artifact_version: 'sha256:prd-v1',
      },
      { initialText: '' },
    );
    expect(mocks.decideReview).not.toHaveBeenCalled();
    expect(router.state.location.pathname).toBe('/projects/DEMO/work/runs/run-1');
    expect(await screen.findByText('Manager Intent Commit')).toBeVisible();
  });

  test('carries the exact review note to the version-bound draft before navigating', async () => {
    const user = userEvent.setup();
    const note = '  Keep Markdown breaks.  \n\n    keep_indentation()\n';
    mocks.startChangeIntent.mockResolvedValue({ intent_commit: { id: 'intent-feedback' } });
    const router = renderReview();
    await user.type(await screen.findByRole('textbox', { name: t('review.decision.rationale') }), note);
    await user.click(screen.getByRole('button', { name: t('review.decision.changes') }));

    expect(mocks.startChangeIntent).toHaveBeenCalledWith(
      'DEMO', 'prd', expect.objectContaining({ artifact_version: 'sha256:prd-v1' }),
      { initialText: note },
    );
    expect(router.state.location.pathname).toBe('/projects/DEMO/work/runs/run-1');
    expect(mocks.decideReview).not.toHaveBeenCalled();
  });

  test('keeps feedback and stays in Review when draft creation conflicts', async () => {
    const user = userEvent.setup();
    const note = 'Do not overwrite the existing change draft.';
    mocks.startChangeIntent.mockRejectedValue(new Error('A different change draft already exists.'));
    const router = renderReview();
    const editor = await screen.findByRole('textbox', { name: t('review.decision.rationale') });
    await user.type(editor, note);
    await user.click(screen.getByRole('button', { name: t('review.decision.changes') }));

    expect(await screen.findByRole('alert')).toHaveTextContent('A different change draft already exists.');
    expect(editor).toHaveValue(note);
    expect(router.state.location.pathname).toBe('/projects/DEMO/review/prd');
    expect(mocks.decideReview).not.toHaveBeenCalled();
  });

  test('renders the evidence summary badges from the snapshot', async () => {
    renderReview();

    expect(await screen.findByText('CI: Passed')).toBeVisible();
    expect(screen.getByText('Smoke: Unknown')).toBeVisible();
    expect(screen.getByText('QA: Failed')).toBeVisible();
    expect(screen.getByText('Blockers 1')).toBeVisible();
    expect(screen.getByText(t('review.evidence.title'), { selector: 'summary' })).toBeVisible();
    expect(screen.getByText(t('review.provenance.title'), { selector: 'summary' })).toBeVisible();
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

  test('renders the selected workflow stage in the workspace summary', async () => {
    renderReview('/projects/DEMO/review/prd');

    expect(await screen.findByText(t('review.workspace.stage'))).toBeVisible();
    expect(screen.getByText('2/3')).toBeVisible();
  });

  test('opens non-actionable review artifacts in preview mode', async () => {
    renderReview('/projects/DEMO/review/rfc');

    expect(await screen.findByRole('heading', { level: 1, name: 'RFC' })).toBeVisible();
    expect(screen.getByRole('button', { name: t('review.artifact.preview') })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('textbox', { name: t('review.artifact.content') })).not.toBeInTheDocument();
  });
});

describe('Markdown rendering in Review and Knowledge workflows', () => {
  test('MarkdownView renders PRD, RFC, and QA report elements accurately', () => {
    const prdSample = `
# GitHub Actions CI/CD Deployment PRD

## 1. Document Overview
This document specifies the deployment pipeline.

### Target Objectives
- Zero secret hardcoding
- Automated readyz health check

\`\`\`yaml
name: Deploy
on:
  push:
    branches: [main]
\`\`\`

| Stage | Responsibility |
|---|---|
| Build | Compile and test |
| Deploy | Push to target |
`;

    render(<MarkdownView content={prdSample} />);

    expect(screen.getByRole('heading', { level: 1, name: 'GitHub Actions CI/CD Deployment PRD' })).toBeVisible();
    expect(screen.getByRole('heading', { level: 2, name: '1. Document Overview' })).toBeVisible();
    expect(screen.getByRole('heading', { level: 3, name: 'Target Objectives' })).toBeVisible();
    expect(screen.getByText('Zero secret hardcoding')).toBeVisible();
    expect(screen.getByText('Automated readyz health check')).toBeVisible();
    expect(screen.getByText('Build')).toBeVisible();
    expect(screen.getByText('Compile and test')).toBeVisible();
  });
});

test('opens actionable material in reading mode and exposes editing on request', async () => {
  const user = userEvent.setup();
  render(<RouterProvider router={routerFor('/projects/DEMO/review/prd')} />);
  expect(await screen.findByText('Initial plan.')).toBeVisible();
  expect(screen.queryByRole('textbox', { name: 'Artifact content' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: translate('en', 'review.artifact.edit') }));
  expect(screen.getByRole('textbox', { name: translate('en', 'review.artifact.content') })).toHaveValue('# PRD\nInitial plan.');
  expect(mocks.saveArtifact).not.toHaveBeenCalled();
  expect(mocks.decideReview).not.toHaveBeenCalled();
});
