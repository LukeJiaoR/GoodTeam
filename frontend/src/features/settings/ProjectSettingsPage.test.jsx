import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import ProjectSettingsPage from './ProjectSettingsPage';

const mocks = vi.hoisted(() => ({
  getProjectSettings: vi.fn(),
  saveWorkerDefaults: vi.fn(),
}));

vi.mock('./settingsApi', () => ({
  settingsApi: { getProjectSettings: mocks.getProjectSettings, saveWorkerDefaults: mocks.saveWorkerDefaults },
}));
vi.mock('../runs/useProjectState', () => ({
  default: () => ({ data: mocks.projectState(), error: null, refresh: vi.fn(), status: 'ready' }),
}));

mocks.projectState = vi.fn(() => ({
  start_context: {
    project_key: 'DEMO',
    project_name: 'Demo project',
    project_base: '/tmp/demo',
    worker_backend: { mode: 'codex' },
    worker_backend_options: ['codex', 'fake', 'noop'],
    project_report: {
      worker_backends_preflight: {
        codex: { ready: true, mode: 'codex', hint: '' },
        fake: { ready: true, mode: 'fake', hint: 'The fake worker runs locally.' },
        noop: { ready: false, mode: 'noop', hint: 'Set the NOOP secret before dispatch.' },
      },
    },
  },
}));

function TranslationLayout() {
  return <Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />;
}

function renderSettings(path = '/projects/DEMO/settings/workers') {
  const router = createMemoryRouter([
    {
      element: <TranslationLayout />,
      children: [{ path: '/projects/:projectKey/settings/:section', element: <ProjectSettingsPage /> }],
    },
  ], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
}

afterEach(() => {
  vi.clearAllMocks();
});

test('renders worker preflight as a structured list with hints and raw JSON collapsed', async () => {
  renderSettings();

  expect(await screen.findByText('codex', { selector: 'strong' })).toBeVisible();
  expect(screen.getByText('fake', { selector: 'strong' })).toBeVisible();
  expect(screen.getAllByText('Ready')).toHaveLength(2);
  expect(screen.getByText('Unavailable')).toBeVisible();
  expect(screen.getByText('Set the NOOP secret before dispatch.')).toBeVisible();
  expect(screen.getByText('Raw preflight report', { selector: 'summary' })).toBeVisible();
});

test('shows an empty state when no preflight data exists', async () => {
  mocks.projectState.mockReturnValue({
    start_context: {
      project_key: 'DEMO',
      project_name: 'Demo project',
      worker_backend: { mode: 'codex' },
      worker_backend_options: ['codex'],
      project_report: {},
    },
  });
  renderSettings();

  expect(await screen.findByText('No worker preflight data is available.')).toBeVisible();
});
