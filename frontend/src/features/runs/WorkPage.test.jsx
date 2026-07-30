import { render, screen } from '@testing-library/react';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import WorkPage from './WorkPage';

const mocks = vi.hoisted(() => ({
  getProjectRuns: vi.fn(),
  useProjectState: vi.fn(),
}));

vi.mock('./useProjectState', () => ({ default: mocks.useProjectState }));
vi.mock('./runApi', () => ({ runApi: { getProjectRuns: mocks.getProjectRuns } }));

function TranslationOutlet() {
  return <Outlet context={{ t: (key, params) => translate('en', key, params) }} />;
}

function renderWorkPage() {
  render(
    <MemoryRouter initialEntries={['/projects/DEMO/work/runs/run-active']}>
      <Routes>
        <Route element={<TranslationOutlet />}>
          <Route path="/projects/:projectKey/work/runs/:runId" element={<WorkPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

test('labels the active run and run selector with task titles instead of raw IDs', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: {} },
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [
      { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' },
      { run_id: 'run-archived', idea: 'Review the previous delivery', status: 'archived' },
    ],
  });

  renderWorkPage();

  expect(screen.getByText('Prepare a human-readable takeover brief', { selector: '.work-run-title' }))
    .toBeVisible();
  const selector = await screen.findByRole('combobox', { name: 'Select run' });
  expect(Array.from(selector.options, (option) => option.textContent)).toEqual([
    'Active · Prepare a human-readable takeover brief',
    'Archived · Review the previous delivery',
  ]);
  expect(screen.queryByText('run-active')).not.toBeInTheDocument();
});

test('makes an actionable takeover review the primary next step', async () => {
  mocks.useProjectState.mockReturnValue({
    data: {
      active_run: { run_id: 'run-active' },
      agile_board: { counts: { blocked: 0 } },
      approval_reviews: [{ approval: 'takeover', artifact: 'takeover', is_actionable: true }],
      start_context: { project_name: 'Demo project' },
      state: { run_id: 'run-active', idea: 'Prepare a human-readable takeover brief' },
      workflow_steps: [{ status: 'review-needed', title: 'Takeover Context' }],
    },
    error: null,
    refresh: vi.fn(),
    status: 'ready',
  });
  mocks.getProjectRuns.mockResolvedValue({
    runs: [{ run_id: 'run-active', idea: 'Prepare a human-readable takeover brief', status: 'active' }],
  });

  renderWorkPage();

  expect(screen.getByText('Review the takeover brief', { selector: 'h2' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Review takeover brief' }))
    .toHaveAttribute('href', '/projects/DEMO/review/takeover');
  expect(screen.getByText('Stop or abandon this run')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Terminate this run' })).not.toBeVisible();
});
