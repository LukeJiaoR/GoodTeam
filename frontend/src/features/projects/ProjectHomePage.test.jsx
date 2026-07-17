import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';
import ProjectHomePage from './ProjectHomePage';

const ambiguousHome = {
  project: { project_key: 'ASSETS', display_name: 'Assets', root: '/tmp/assets' },
  facts: { root: '/tmp/assets', detected_mode: 'unconfirmed', active_run: null },
  readiness: {
    lifecycle: 'attention_required',
    project_mode: 'unconfirmed',
    steps: [
      { id: 'project_record', status: 'complete', required: true },
      { id: 'safe_preflight', status: 'complete', required: true },
      { id: 'project_mode', status: 'needs_input', required: true },
    ],
  },
  capabilities: { start_workflow: false, start_workflow_reason: 'project_mode_unconfirmed' },
  next_action: { id: 'project_mode', status: 'needs_input' },
};

test('restores readiness and confirms an ambiguous project as existing', async () => {
  const user = userEvent.setup();
  const api = {
    getProjectHome: vi.fn().mockResolvedValue(ambiguousHome),
    confirmProjectMode: vi.fn().mockResolvedValue({
      ...ambiguousHome,
      readiness: { ...ambiguousHome.readiness, project_mode: 'existing_project' },
      next_action: { id: 'repository_understanding', status: 'pending' },
    }),
  };
  render(<ProjectHomePage projectKey="ASSETS" api={api} language="en" />);
  expect(await screen.findByText('Choose how GoodTeam should prepare this project')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Understand an existing codebase' }));
  expect(api.confirmProjectMode).toHaveBeenCalledWith('ASSETS', 'existing_project');
  expect(await screen.findByText('Repository understanding')).toBeVisible();
});

test('shows why workflow start is disabled', async () => {
  const api = { getProjectHome: vi.fn().mockResolvedValue(ambiguousHome) };
  render(<ProjectHomePage projectKey="ASSETS" api={api} language="en" />);
  expect(await screen.findByRole('button', { name: 'Start workflow' })).toBeDisabled();
  expect(screen.getByText('Confirm the project type before starting work.')).toBeVisible();
});

test('renders the same ambiguous decision contract in Simplified Chinese', async () => {
  const api = { getProjectHome: vi.fn().mockResolvedValue(ambiguousHome) };
  render(<ProjectHomePage projectKey="ASSETS" api={api} language="zh-CN" />);
  expect(await screen.findByText('选择 GoodTeam 应如何准备这个项目')).toBeVisible();
  expect(screen.getByRole('button', { name: '理解现有代码库' })).toBeVisible();
});
