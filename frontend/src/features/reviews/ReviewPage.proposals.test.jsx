import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import ReviewPage from './ReviewPage';
import { reviewApi } from './reviewApi';

vi.mock('./reviewApi', () => ({ reviewApi: Object.fromEntries([
  'getReviewState', 'getArtifact', 'getOpenQuestions', 'refineArtifact',
  'answerOpenQuestions', 'acceptRefinement', 'discardRefinement', 'saveArtifact',
].map((key) => [key, vi.fn()])) }));
const t = (key) => translate('en', key);
const binding = { proposal_id: 'proposal-1', run_id: 'run-1', artifact_key: 'prd', base_version: 'sha256:base', proposal_version: 'sha256:proposal' };
const proposal = { content: 'PRD ONLY PROPOSAL', run_id: 'run-1', artifact_version: binding.proposal_version, proposal_binding: binding };
const state = { state: { run_id: 'run-1' }, approval_reviews: ['prd', 'rfc'].map((artifact) => ({
  approval: artifact, title: artifact.toUpperCase(), artifact, artifact_path: `/runs/run-1/${artifact}.md`,
  artifact_version: binding.base_version, content: `${artifact.toUpperCase()} BODY`, is_actionable: artifact === 'prd',
})) };
function Layout() { return <Outlet context={{ t }} />; }
function setup() {
  const router = createMemoryRouter([{ element: <Layout />, children: [
    { path: '/projects/:projectKey/review/:reviewId', element: <ReviewPage /> },
  ] }], { initialEntries: ['/projects/DEMO/review/prd'] });
  render(<RouterProvider router={router} />);
  return { router, user: userEvent.setup() };
}
function deferred() { let resolve; const promise = new Promise((accept) => { resolve = accept; }); return { promise, resolve }; }
async function showProposal(user) {
  await user.click(await screen.findByRole('button', { name: t('review.actions.refine') }));
  await screen.findByText(proposal.content);
}
beforeEach(() => {
  vi.resetAllMocks();
  reviewApi.getReviewState.mockResolvedValue(state);
  reviewApi.getOpenQuestions.mockResolvedValue({ questions: [] });
  reviewApi.refineArtifact.mockResolvedValue({ proposed_path: '/private/path/prd_proposed.md', proposal_binding: binding });
  reviewApi.getArtifact.mockResolvedValue(proposal);
  reviewApi.acceptRefinement.mockResolvedValue({ ok: true });
  reviewApi.discardRefinement.mockResolvedValue({ ok: true });
});

test('PRD proposal does not follow RFC navigation or browser back', async () => {
  const { router, user } = setup();
  await showProposal(user);
  expect(reviewApi.getArtifact).toHaveBeenCalledWith('DEMO', 'prd_proposed');
  await act(async () => { await router.navigate('/projects/DEMO/review/rfc'); });
  await user.click(await screen.findByRole('button', { name: t('review.dirty.leave') }));
  await screen.findByText('RFC BODY');
  expect(screen.queryByText(proposal.content)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: t('review.refinement.accept') })).not.toBeInTheDocument();
  await act(async () => { await router.navigate(-1); });
  await screen.findByText('PRD BODY');
  expect(screen.queryByText(proposal.content)).not.toBeInTheDocument();
  expect(reviewApi.acceptRefinement).not.toHaveBeenCalled();
});

test.each(['generation', 'fetch'])('ignores late proposal %s after navigating away and back', async (phase) => {
  const pending = deferred();
  if (phase === 'generation') reviewApi.refineArtifact.mockReturnValueOnce(pending.promise);
  else reviewApi.getArtifact.mockReturnValueOnce(pending.promise);
  const { router, user } = setup();
  await user.click(await screen.findByRole('button', { name: t('review.actions.refine') }));
  await waitFor(() => expect(phase === 'generation' ? reviewApi.refineArtifact : reviewApi.getArtifact).toHaveBeenCalled());
  await act(async () => { await router.navigate('/projects/DEMO/review/rfc'); });
  await screen.findByText('RFC BODY');
  await act(async () => { await router.navigate(-1); });
  await screen.findByText('PRD BODY');
  await act(async () => { pending.resolve(phase === 'generation' ? { proposal_binding: binding } : proposal); });
  expect(screen.queryByText(proposal.content)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: t('review.actions.refine') })).toBeEnabled();
});

test.each(['accept', 'discard'])('%s sends the exact visible proposal binding', async (action) => {
  const { user } = setup();
  await showProposal(user);
  await user.click(screen.getByRole('button', { name: t(`review.refinement.${action}`) }));
  expect(reviewApi[`${action}Refinement`]).toHaveBeenCalledWith('DEMO', 'prd', { expectedProposal: binding });
});

test.each(['proposal_id', 'run_id', 'artifact_key', 'base_version', 'proposal_version'])('rejects mismatched fetched %s', async (field) => {
  reviewApi.getArtifact.mockResolvedValue({ ...proposal, proposal_binding: { ...binding, [field]: 'changed' } });
  const { user } = setup();
  await user.click(await screen.findByRole('button', { name: t('review.actions.refine') }));
  await screen.findByText(/Refinement proposal changed while loading/);
  expect(screen.queryByText(proposal.content)).not.toBeInTheDocument();
});

test('new edits invalidate the proposal even if the edit is later undone', async () => {
  const { user } = setup();
  await showProposal(user);
  await user.click(screen.getByRole('button', { name: t('review.artifact.edit') }));
  const editor = screen.getByRole('textbox', { name: t('review.artifact.content') });
  await user.type(editor, 'x');
  await user.keyboard('{Backspace}');
  expect(screen.queryByText(proposal.content)).not.toBeInTheDocument();
  expect(reviewApi.acceptRefinement).not.toHaveBeenCalled();
});

test('answers are bound to the displayed run and artifact version', async () => {
  reviewApi.getOpenQuestions.mockResolvedValue({ questions: ['Which color?'] });
  reviewApi.answerOpenQuestions.mockResolvedValue({ proposal_binding: binding });
  const { user } = setup();
  await user.type(await screen.findByRole('textbox', { name: 'Which color?' }), 'Blue');
  await user.click(screen.getByRole('button', { name: t('review.questions.apply') }));
  await screen.findByText(proposal.content);
  expect(reviewApi.answerOpenQuestions).toHaveBeenCalledWith('DEMO', 'prd', [{ question: 'Which color?', answer: 'Blue' }], {
    expectedArtifact: { runId: 'run-1', artifactVersion: binding.base_version },
  });
});

test('proposal and question drafts are isolated across projects', async () => {
  reviewApi.getOpenQuestions.mockResolvedValue({ questions: ['Which color?'] });
  const { router, user } = setup();
  await showProposal(user);
  await user.type(screen.getByRole('textbox', { name: 'Which color?' }), 'Blue');
  await act(async () => { await router.navigate('/projects/OTHER/review/prd'); });
  await user.click(await screen.findByRole('button', { name: t('review.dirty.leave') }));
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Which color?' })).toHaveValue(''));
  expect(screen.queryByText(proposal.content)).not.toBeInTheDocument();
});

test('a refreshed run cannot inherit the previous run proposal', async () => {
  const { user } = setup();
  await showProposal(user);
  reviewApi.getReviewState.mockResolvedValue({ ...state, state: { run_id: 'run-2' } });
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
  await waitFor(() => expect(screen.queryByText(proposal.content)).not.toBeInTheDocument());
  expect(screen.queryByRole('button', { name: t('review.refinement.accept') })).not.toBeInTheDocument();
});

test('saving invalidates an existing proposal', async () => {
  reviewApi.saveArtifact.mockResolvedValue({ ok: true });
  const { user } = setup();
  await showProposal(user);
  reviewApi.getArtifact.mockResolvedValue({ content: 'PRD BODY', run_id: 'run-1', artifact_version: 'sha256:saved' });
  await user.click(screen.getByRole('button', { name: t('review.actions.save') }));
  await waitFor(() => expect(screen.queryByText(proposal.content)).not.toBeInTheDocument());
  expect(reviewApi.acceptRefinement).not.toHaveBeenCalled();
});

test('refining edits adopts the verified new base revision and retains its proposal', async () => {
  const updatedBinding = { ...binding, base_version: 'sha256:edited' };
  reviewApi.refineArtifact.mockResolvedValue({ proposal_binding: updatedBinding });
  reviewApi.getArtifact.mockImplementation(async (_project, key) => key === 'prd'
    ? { content: 'PRD BODY edited', run_id: 'run-1', artifact_version: updatedBinding.base_version }
    : { ...proposal, proposal_binding: updatedBinding });
  const { user } = setup();
  await user.click(await screen.findByRole('button', { name: t('review.artifact.edit') }));
  await user.type(screen.getByRole('textbox', { name: t('review.artifact.content') }), ' edited');
  reviewApi.getReviewState.mockResolvedValue({ ...state, approval_reviews: state.approval_reviews.map((review) => review.artifact === 'prd'
    ? { ...review, content: 'PRD BODY edited', artifact_version: updatedBinding.base_version, artifact_path: '/runs/run-1/revisions/prd.md' } : review) });
  await showProposal(user);
  await user.click(screen.getByRole('button', { name: t('review.refinement.accept') }));
  expect(reviewApi.acceptRefinement).toHaveBeenCalledWith('DEMO', 'prd', { expectedProposal: updatedBinding });
});
