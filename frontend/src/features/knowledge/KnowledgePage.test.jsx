import { createMemoryRouter, Outlet, RouterProvider } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';
import { translate } from '../../i18n';
import KnowledgePage from './KnowledgePage';

const mocks = vi.hoisted(() => ({
  getKnowledgeArtifact: vi.fn(),
  getKnowledgeHome: vi.fn(),
  getKnowledgeProjects: vi.fn(),
  getKnowledgeState: vi.fn(),
  getMemory: vi.fn(),
  getMemoryEvent: vi.fn(),
  getMemoryEvents: vi.fn(),
  getCrossProjectMemory: vi.fn(),
  getRepositoryUnderstanding: vi.fn(),
}));

vi.mock('./knowledgeApi', () => ({ knowledgeApi: mocks }));

const projectState = {
  start_context: { project_name: 'Demo project' },
  state: {
    artifacts: {
      prd: '/runs/demo/prd.md',
      rfc: '/runs/demo/rfc.md',
    },
  },
};

function TranslationLayout() {
  return <Outlet context={{ language: 'en', t: (key, params) => translate('en', key, params) }} />;
}

function renderKnowledge(path = '/projects/DEMO/knowledge') {
  mocks.getKnowledgeState.mockResolvedValue(projectState);
  mocks.getKnowledgeHome.mockResolvedValue({
    readiness: { project_mode: 'existing_project', updated_at: '2026-09-04T00:00:00Z' },
    facts: { confirmed_commands: ['npm test'], constraints: ['Keep API compatibility'] },
  });
  mocks.getKnowledgeArtifact.mockResolvedValue({ content: '# PRD\nProduct requirements.' });
  mocks.getMemory.mockResolvedValue({ roles: [{ role: 'architect', event_count: 1 }] });
  mocks.getKnowledgeProjects.mockResolvedValue({ projects: [] });
  mocks.getMemoryEvents.mockResolvedValue({ events: [] });
  const router = createMemoryRouter([{
    element: <TranslationLayout />,
    children: [{ path: '/projects/:projectKey/knowledge', element: <KnowledgePage /> }],
  }], { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

afterEach(() => vi.clearAllMocks());

test('renders the new knowledge workspace without the legacy Home action', async () => {
  renderKnowledge();

  expect(await screen.findByRole('heading', { name: 'Knowledge' })).toBeVisible();
  expect(screen.getByText('2')).toBeVisible();
  expect(screen.getByText('Recorded artifacts')).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Back to Home' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Understanding' })).toHaveAttribute('aria-pressed', 'true');
});

test('switches between artifact documents and role memory in the same workspace', async () => {
  const user = userEvent.setup();
  renderKnowledge();

  await user.click(await screen.findByRole('button', { name: 'Artifacts and docs' }));
  expect(await screen.findByRole('heading', { name: 'prd' })).toBeVisible();
  expect(await screen.findByText('Product requirements.')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Memory' }));
  expect(await screen.findByRole('heading', { name: 'Role memory' })).toBeVisible();
  expect(screen.getByRole('button', { name: /architect/ })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('searchbox', { name: 'Search this role’s memory' })).toBeVisible();
});
