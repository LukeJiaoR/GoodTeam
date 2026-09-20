import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import CurrentWorkContext from './CurrentWorkContext.jsx';

const base = { projectKey: 'A', projectName: 'Project A', title: 'Keep APIs stable', freshness: 'ready', workspace: { schema_version: 'team_workspace.v1', mission: { project_key: 'A', status: 'working', source: 'workflow' } } };
const t = (key, params) => translate('en', key, params);
describe('persistent task context', () => {
  it('shows identity and one next action without a hover or workflow mutation', () => {
    render(<CurrentWorkContext {...base} t={t} />);
    const bar = screen.getByRole('region', { name: 'Current task and next step' });
    expect(within(bar).getByText('Project A')).toBeVisible();
    expect(within(bar).getByText('Keep APIs stable')).toBeVisible();
    expect(within(bar).getAllByRole('button')).toHaveLength(1);
    expect(within(bar).getByRole('button', { name: 'Inspect current progress' })).toBeVisible();
    expect(bar.closest('[inert], [aria-hidden="true"]')).toBeNull();
  });
  it('offers refresh rather than an execution action on stale data', () => {
    const onRefresh = vi.fn();
    render(<CurrentWorkContext {...base} freshness="stale" onRefresh={onRefresh} t={t} />);
    expect(screen.getByText('State unknown')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Inspect current progress' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh current state' }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
  it('renders history as read only even with a pending approval in the input', () => {
    render(<CurrentWorkContext {...base} historical workspace={{ ...base.workspace, attention_request: { kind: 'approval' } }} t={t} />);
    expect(screen.getByText('History · read-only')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Inspect task results' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Go to required decision' })).toBeNull();
  });
  it('uses the existing Chinese catalogue', () => {
    render(<CurrentWorkContext {...base} t={(key, params) => translate('zh-CN', key, params)} />);
    expect(screen.getByRole('region', { name: '当前任务与下一步' })).toBeVisible();
    expect(screen.getByRole('button', { name: '查看当前进度' })).toBeVisible();
  });
});
