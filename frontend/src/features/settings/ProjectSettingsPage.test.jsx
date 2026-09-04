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
      children: [
        { path: '/projects/:projectKey/settings/:section', element: <ProjectSettingsPage /> },
        { path: '/projects/:projectKey/team', element: <h1>Unified Team</h1> },
      ],
    },
  ], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => {
  vi.clearAllMocks();
});

test('redirects the legacy workers settings route to the unified Team surface', async () => {
  const router = renderSettings();

  expect(await screen.findByRole('heading', { name: 'Unified Team' })).toBeVisible();
  expect(router.state.location.pathname).toBe('/projects/DEMO/team');
});

test('keeps non-Team project settings in the settings surface', async () => {
  renderSettings('/projects/DEMO/settings/general');

  expect(await screen.findByRole('heading', { name: 'General' })).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Workers and models' })).not.toBeInTheDocument();
});
