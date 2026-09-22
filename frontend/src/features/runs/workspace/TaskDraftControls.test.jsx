import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { en } from '../../../i18n/locales/en';
import TaskDraftControls, { ProjectModeControl } from './TaskDraftControls';

const t = (key) => en[key] || key;
const draft = { id: 'p1', revision: 2, exact_request: 'Add search.\n\nAlso add tests.', source_message_ids: ['m1', 'm2'] };

describe('Task draft controls', () => {
  it('confirms exactly the visible draft version', () => {
    const onConfirm = vi.fn();
    render(<TaskDraftControls draft={draft} onConfirm={onConfirm} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start this task' }));
    expect(onConfirm).toHaveBeenCalledWith('p1', 2);
  });

  it('blocks an incomplete draft until a clarified revision arrives', () => {
    const onConfirm = vi.fn();
    const view = render(<TaskDraftControls draft={{ ...draft, needs_clarification: true }} onConfirm={onConfirm} t={t} />);
    const start = screen.getByRole('button', { name: 'Start this task' });
    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(onConfirm).not.toHaveBeenCalled();
    view.rerender(<TaskDraftControls draft={{ ...draft, revision: 3 }} onConfirm={onConfirm} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start this task' }));
    expect(onConfirm).toHaveBeenCalledExactlyOnceWith('p1', 3);
  });

  it('edits exact text with expected revision and preserves input on failure', async () => {
    const onAction = vi.fn().mockResolvedValue(false);
    render(<TaskDraftControls draft={draft} onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit task' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Task draft' }), { target: { value: 'Search without new dependencies.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(onAction).toHaveBeenCalledWith(expect.objectContaining({
      action: 'edit_task', proposal_id: 'p1', expected_revision: 2, text: 'Search without new dependencies.',
    })));
    expect(screen.getByRole('textbox')).toHaveValue('Search without new dependencies.');
  });

  it('blocks start while a change needs a choice and submits exact replacement', () => {
    const onAction = vi.fn();
    render(<TaskDraftControls draft={{ ...draft, pending_change: { text: 'Build login.', kind: 'resolve' } }} onAction={onAction} t={t} />);
    expect(screen.getByRole('button', { name: 'Start this task' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Replace task' }));
    expect(onAction).toHaveBeenCalledWith({ action: 'resolve_draft', proposal_id: 'p1', expected_revision: 2, choice: 'replace' });
  });

  it('withdraws the visible version and never offers start for cancelled/history drafts', () => {
    const onAction = vi.fn();
    const view = render(<TaskDraftControls draft={draft} onAction={onAction} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Withdraw draft' }));
    expect(onAction).toHaveBeenCalledWith({ action: 'resolve_draft', proposal_id: 'p1', expected_revision: 2, choice: 'withdraw' });
    view.rerender(<TaskDraftControls draft={{ ...draft, status: 'cancelled' }} t={t} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    view.rerender(<TaskDraftControls draft={draft} readOnly t={t} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('requires explicit project mode selection', () => {
    const onAction = vi.fn();
    render(<ProjectModeControl onAction={onAction} t={t} />);
    expect(screen.getByRole('button', { name: 'Confirm project type' })).toBeDisabled();
    fireEvent.click(screen.getByRole('radio', { name: 'Existing project' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm project type' }));
    expect(onAction).toHaveBeenCalledWith({ action: 'confirm_project_mode', mode: 'existing_project' });
  });

  it('can resume an already confirmed draft after preparation becomes ready', () => {
    const onConfirm = vi.fn();
    render(<TaskDraftControls draft={{ ...draft, confirmed: true }} resumeReady onConfirm={onConfirm} t={t} />);
    fireEvent.click(screen.getByRole('button', { name: 'Continue starting task' }));
    expect(onConfirm).toHaveBeenCalledWith('p1', 2);
  });
});
